#!/usr/bin/env python3
"""hprof_analyzer.analyzer — Main entry point for the hprof-analyzer skill.

Converts HPROF files to Parquet, runs DuckDB analysis, and generates
a comprehensive memory analysis report.

Usage example:
    from skills.hprof_analyzer.analyzer import analyze_hprof

    result = analyze_hprof(
        hprof_name="taqu_android_client_logfile_401..."  # Just filename (no extension!)
    )
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

# Relative imports within the skill package
from .lib.parser import HPROFParser
from .lib.writer import convert_hprof_to_parquet

# Import DuckDB for analysis
try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def ensure_parquet(hprof_path: str, output_dir: str) -> Dict[str, int]:
    """Convert HPROF to Parquet. Always performs conversion to ensure fresh data."""
    import os, subprocess, glob
    print(f"Converting HPROF to Parquet...")

    from .lib.standard_parser import is_hprof_libs, convert_to_standard, StandardHprofParser

    if is_hprof_libs(hprof_path):
        # 输出到 hprof_analysis/xxx/standard/xxx.standard.hprof
        std_dir = os.path.join(os.path.dirname(output_dir), 'standard')
        os.makedirs(std_dir, exist_ok=True)
        hprof_basename = os.path.basename(hprof_path).replace('.hprof', '.standard.hprof')
        std_path = os.path.join(std_dir, hprof_basename)
        print(f"  Detected hprof-libs format, running hprof-conv...")
        if convert_to_standard(hprof_path, std_path):
            # 尝试使用 HeapDumpStarDiver (快, 6s)
            star_diver = os.path.join(os.path.dirname(__file__), 'references', 'HeapDumpStarDiver')
            if os.path.isfile(star_diver) and os.access(star_diver, os.X_OK):
                print(f"  Using HeapDumpStarDiver (fast mode)...")
                for f in glob.glob(os.path.join(output_dir, '*.parquet')):
                    os.remove(f)
                result = subprocess.run(
                    [star_diver, '--file', std_path, 'dump-objects-to-parquet'],
                    cwd=output_dir, capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    inner = os.path.join(output_dir, 'parquet')
                    if os.path.isdir(inner):
                        for f in os.listdir(inner):
                            os.rename(os.path.join(inner, f), os.path.join(output_dir, f))
                        os.rmdir(inner)
                    class_files = [f for f in glob.glob(os.path.join(output_dir, '*.parquet'))
                                   if not os.path.basename(f).startswith('_')]
                    print(f"  \u2713 HeapDumpStarDiver: {len(class_files)} class files")
                    return {'objects': 0, 'classes': len(class_files), 'gc_roots': 0,
                            'strings': 0, 'object_refs': 0, 'star_diver': True}
                else:
                    print(f"  \u26a0 HeapDumpStarDiver failed, using Python parser")

            # 使用 Python StandardHprofParser (慢, 9min, 但纯 Python)
            print(f"  Parsing with StandardHprofParser (Python mode)...")
            std_parser = StandardHprofParser(std_path)
            std_parser.parse_strings_and_classes()
            print(f"  Strings: {len(std_parser.strings):,}, Classes: {len(std_parser.class_names):,}")
            std_parser.parse_heap_dump()
            print(f"  Objects: {len(std_parser.objects):,}, GC Roots: {len(std_parser.gc_roots):,}")

            # 写入 Parquet
            from .lib.writer import write_standard_parser_data
            for f in glob.glob(os.path.join(output_dir, '*.parquet')):
                os.remove(f)
            counts = write_standard_parser_data(std_parser, output_dir)
            print(f"  \u2713 Written {counts['classes']} class files, {counts['objects']:,} objects")
            counts['star_diver'] = False
            return counts
    else:
        # 标准 HPROF 格式, 直接解析
        from .lib.standard_parser import StandardHprofParser
        std_parser = StandardHprofParser(hprof_path)
        std_parser.parse_strings_and_classes()
        std_parser.parse_heap_dump()
        from .lib.writer import write_standard_parser_data
        for f in glob.glob(os.path.join(output_dir, '*.parquet')):
            os.remove(f)
        counts = write_standard_parser_data(std_parser, output_dir)
        counts['star_diver'] = False
        return counts


def get_timestamp_suffix() -> str:
    """Generate timestamp suffix for report filenames (YYYYmmdd_HHMMss)."""
    return time.strftime('%Y%m%d_%H%M%S')


def run_duckdb_analysis(parquet_dir: str) -> Dict[str, Any]:
    """Run all DuckDB queries and return analysis results.

    Implements A1-A5 from SKILL.md:
    - A1: Object type distribution
    - A2: GC Root distribution and holders
    - A3: Static field holders (if available)
    - A4: Class hierarchy
    - A5: Report generation (called by generate_report)
    """
    if not HAS_DUCKDB:
        print("⚠️  duckdb not installed, skipping analysis")
        return {}

    con = duckdb.connect()
    result = {'parquet_dir': parquet_dir}

    # 检测是否为 HeapDumpStarDiver 输出 (按类分文件)
    import glob as _glob
    star_diver_mode = any(
        not os.path.basename(f).startswith('_')
        for f in _glob.glob(os.path.join(parquet_dir, '*.parquet'))
    ) and os.path.isfile(os.path.join(parquet_dir, '_gc_roots.parquet'))

    if star_diver_mode:
        return _run_star_diver_analysis(con, parquet_dir, result)
    result['std_class_count'] = 0  # Will be set if standard parser was used

    # ── A1: Object type distribution ──────────────────────────────────
    obj_pattern = os.path.join(parquet_dir, '_object_index_chunk*.parquet')
    result['total_objects'] = con.execute(f"SELECT COUNT(*) FROM read_parquet('{obj_pattern}')").fetchone()[0]

    top_classes = con.execute(f"""
        SELECT class_name, COUNT(*) as instance_count
        FROM read_parquet('{obj_pattern}')
        GROUP BY class_name
        ORDER BY instance_count DESC
        LIMIT 50
    """).fetchall()
    result['top_classes'] = [{'name': r[0], 'count': r[1]} for r in top_classes]

    package_dist = con.execute(f"""
        SELECT
            CASE
                WHEN class_name LIKE 'com.taqu.%' THEN 'com.taqu.*'
                WHEN class_name LIKE 'com.xmhaihao.%' THEN 'com.xmhaihao.*'
                WHEN class_name LIKE 'hb.%' THEN 'hb.*'
                WHEN class_name LIKE 'androidx.%' THEN 'androidx.*'
                WHEN class_name LIKE 'android.%' THEN 'android.*'
                WHEN class_name LIKE 'java.%' THEN 'java.*'
                WHEN class_name LIKE 'kotlin.%' THEN 'kotlin.*'
                WHEN class_name LIKE 'org.%' THEN 'org.*'
                ELSE 'other'
            END as package_group,
            COUNT(*) as object_count
        FROM read_parquet('{obj_pattern}')
        GROUP BY package_group
        ORDER BY object_count DESC
        LIMIT 20
    """).fetchall()
    result['package_dist'] = [{'group': r[0], 'count': r[1]} for r in package_dist]

    # Name coverage stats
    coverage_rows = con.execute(f"""
        SELECT
            CASE
                WHEN class_name = 'class_0' THEN 'unresolved(0)'
                WHEN class_name LIKE 'class_%' THEN 'unmapped(class_X)'
                ELSE 'mapped'
            END as category,
            COUNT(*) as object_count
        FROM read_parquet('{obj_pattern}')
        GROUP BY category
    """).fetchall()
    result['name_coverage'] = {r[0]: r[1] for r in coverage_rows}
    result['shallow_size_estimate_mb'] = round(result['total_objects'] * 24 / (1024 * 1024), 2)

    # ── A2: GC Root distribution ─────────────────────────────────────
    gc_pattern = os.path.join(parquet_dir, '_gc_roots_*.parquet')
    has_gc = any('gc_roots' in f for f in os.listdir(parquet_dir))

    if has_gc:
        gc_dist = con.execute(f"""
            SELECT root_type, COUNT(*) as count
            FROM read_parquet('{gc_pattern}')
            GROUP BY root_type
            ORDER BY count DESC
        """).fetchall()
        # 标准化 root_type 名称以匹配报告检查
        type_map = {
            'Unknown': 'UNKNOWN', 'SystemClass': 'SYSTEM_CLASS',
            'JavaStackFrame': 'JAVA_STACK', 'JniGlobal': 'JNI_GLOBAL',
            'NativeStack': 'NATIVE_STACK', 'ThreadObj': 'THREAD_OBJ',
            'JniLocal': 'JNI_LOCAL',
        }
        result['gc_root_dist'] = [{'type': type_map.get(r[0], r[0].upper()), 'count': r[1]} for r in gc_dist]
        result['total_gc_roots'] = sum(r[1] for r in gc_dist)

        # JAVA_STACK holders
        java_stack_holders = con.execute(f"""
            SELECT oi.class_name, COUNT(*) as held_count
            FROM read_parquet('{gc_pattern}') gr
            JOIN read_parquet('{obj_pattern}') oi ON gr.obj_id = oi.obj_id
            WHERE gr.root_type = 'JAVA_STACK'
            GROUP BY oi.class_name
            ORDER BY held_count DESC
            LIMIT 30
        """).fetchall()
        result['java_stack_holders'] = [{'name': r[0], 'count': r[1]} for r in java_stack_holders]

        # SYSTEM_CLASS holders
        sys_class_holders = con.execute(f"""
            SELECT oi.class_name, COUNT(*) as held_count
            FROM read_parquet('{gc_pattern}') gr
            JOIN read_parquet('{obj_pattern}') oi ON gr.obj_id = oi.obj_id
            WHERE gr.root_type = 'SYSTEM_CLASS'
            GROUP BY oi.class_name
            ORDER BY held_count DESC
            LIMIT 20
        """).fetchall()
        result['system_class_holders'] = [{'name': r[0], 'count': r[1]} for r in sys_class_holders]

        # Frame/Monitor holders
        frame_holders = con.execute(f"""
            SELECT oi.class_name, COUNT(*) as held_count
            FROM read_parquet('{gc_pattern}') gr
            JOIN read_parquet('{obj_pattern}') oi ON gr.obj_id = oi.obj_id
            WHERE gr.root_type IN ('GC_LOCAL', 'GC_JAVA_FRAME')
            GROUP BY oi.class_name
            ORDER BY held_count DESC
            LIMIT 20
        """).fetchall()
        result['frame_holders'] = [{'name': r[0], 'count': r[1]} for r in frame_holders]

        # ThreadObj analysis
        thread_roots = con.execute(f"""
            SELECT thread_serial, COUNT(*) as held_count
            FROM read_parquet('{gc_pattern}')
            WHERE root_type = 'GC_JAVA_FRAME'
            GROUP BY thread_serial
            ORDER BY held_count DESC
            LIMIT 20
        """).fetchall()
        result['thread_roots'] = [{'thread_serial': r[0], 'count': r[1]} for r in thread_roots]
    else:
        result['gc_root_dist'] = []
        result['total_gc_roots'] = 0
        result['java_stack_holders'] = []
        result['system_class_holders'] = []
        result['frame_holders'] = []
        result['thread_roots'] = []

    # ── A3: Static field holders ─────────────────────────────────────
    has_sf = any('static_fields' in f for f in os.listdir(parquet_dir))
    result['has_static_fields'] = has_sf

    if has_sf:
        sf_pattern = os.path.join(parquet_dir, '_static_fields.parquet')
        sf_holders = con.execute(f"""
            SELECT sf.class_name, COUNT(*) as ref_count
            FROM read_parquet('{sf_pattern}') sf
            JOIN read_parquet('{obj_pattern}') oi ON sf.obj_id = oi.obj_id
            GROUP BY sf.class_name
            ORDER BY ref_count DESC
            LIMIT 50
        """).fetchall()
        result['static_field_holders'] = [
            {'class': r[0], 'count': r[1]} for r in sf_holders
        ]
    else:
        result['static_field_holders'] = []

    # ── A4: Class hierarchy ──────────────────────────────────────────
    hier_file = os.path.join(parquet_dir, '_class_hierarchy.parquet')
    if os.path.exists(hier_file):
        hierarchy = con.execute(f"""
            SELECT class_name, num_instances
            FROM read_parquet('{hier_file}')
            ORDER BY num_instances DESC
            LIMIT 30
        """).fetchall()
        result['hierarchy'] = [{'name': r[0], 'instances': r[1]} for r in hierarchy]
    else:
        result['hierarchy'] = []

    # ── Threads and frames ───────────────────────────────────────────
    threads_file = os.path.join(parquet_dir, '_threads.parquet')
    if os.path.exists(threads_file):
        threads = con.execute(f"SELECT * FROM read_parquet('{threads_file}')").fetchall()
        result['threads'] = [{'serial': r[0], 'name': r[1], 'suspend_type': r[2]} for r in threads]
    else:
        result['threads'] = []

    # ── A6: Object reference chain ──
    ref_file = os.path.join(parquet_dir, '_object_references.parquet')
    if os.path.exists(ref_file):
        result['has_object_refs'] = True
        ref_rows = con.execute(f"SELECT class_name, ref_class_name, COUNT(*) as ref_count FROM read_parquet('{ref_file}') GROUP BY class_name, ref_class_name ORDER BY ref_count DESC LIMIT 30").fetchall()
        result['ref_chain'] = [{'from': r[0], 'to': r[1], 'count': r[2]} for r in ref_rows]
        result['total_refs'] = con.execute(f"SELECT COUNT(*) FROM read_parquet('{ref_file}')").fetchone()[0]
    else:
        result['has_object_refs'] = False
        result['ref_chain'] = []
        result['total_refs'] = 0

    frames_file = os.path.join(parquet_dir, '_frames.parquet')
    if os.path.exists(frames_file):
        frames = con.execute(f"SELECT * FROM read_parquet('{frames_file}') LIMIT 50").fetchall()

        result['frames'] = [
            {'frame_id': r[0], 'class_serial': r[1], 'class_name': r[2],
             'method_index': r[3], 'line_number': r[4], 'type_code': r[5]}
            for r in frames
        ]
    else:
        result['frames'] = []

    con.close()
    return result


def _run_star_diver_analysis(con, parquet_dir, result):
    """Run DuckDB analysis on HeapDumpStarDiver output (per-class schema)."""
    import glob as _glob

    # 统计每类对象数
    class_files = [f for f in _glob.glob(os.path.join(parquet_dir, '*.parquet'))
                   if not os.path.basename(f).startswith('_')]
    class_counts = []
    total_objects = 0
    for f in class_files:
        bn = os.path.basename(f)
        parts = bn.rsplit('_', 1)
        if len(parts) == 2:
            cn = parts[0]
            cnt = con.execute(f"SELECT COUNT(*) FROM read_parquet('{f}')").fetchone()[0]
            class_counts.append((cn, cnt))
            total_objects += cnt
    class_counts.sort(key=lambda x: -x[1])

    result['total_objects'] = total_objects
    # 计算 per-class 内存占用 (field_data_size 之和 + 对象头 16 字节/对象)
    map_file = os.path.join(parquet_dir, '_obj_class_map.parquet')
    class_memory = {}
    if os.path.isfile(map_file):
        mem_rows = con.execute(f"""
            SELECT class_name, SUM(field_data_size) as total_field_bytes, COUNT(*) as obj_count
            FROM read_parquet('{map_file}')
            GROUP BY class_name
        """).fetchall()
        for cn, total_bytes, obj_count in mem_rows:
            # shallow size = field_data_bytes + object_header(16B) * count
            class_memory[cn] = total_bytes + 16 * obj_count
    
    result['top_classes'] = []
    for cn, cnt in class_counts[:50]:
        mem_bytes = class_memory.get(cn, 0)
        result['top_classes'].append({
            'name': cn,
            'count': cnt,
            'memory_bytes': mem_bytes,
            'memory_mb': round(mem_bytes / (1024 * 1024), 2),
        })

    # 包级别分布 (含内存)
    pkg_dist = []
    pkg_map = {}
    pkg_mem = {}
    for cn, cnt in class_counts:
        if cn.startswith('com.xingjiabi.'): pkg = 'com.xingjiabi.*'
        elif cn.startswith('com.xmhaihao.'): pkg = 'com.xmhaihao.*'
        elif cn.startswith('com.xmhaibao.'): pkg = 'com.xmhaibao.*'
        elif cn.startswith('cn.taqu.'): pkg = 'cn.taqu.*'
        elif cn.startswith('hb.'): pkg = 'hb.*'
        elif cn.startswith('android.'): pkg = 'android.*'
        elif cn.startswith('java.'): pkg = 'java.*'
        elif cn.startswith('kotlin.'): pkg = 'kotlin.*'
        elif cn.startswith('androidx.'): pkg = 'androidx.*'
        elif cn.startswith('com.android.'): pkg = 'com.android.*'
        else: pkg = 'other'
        pkg_map[pkg] = pkg_map.get(pkg, 0) + cnt
        pkg_mem[pkg] = pkg_mem.get(pkg, 0) + class_memory.get(cn, 0)
    for pkg, cnt in sorted(pkg_map.items(), key=lambda x: -x[1]):
        mem = pkg_mem.get(pkg, 0)
        pkg_dist.append({'group': pkg, 'count': cnt, 'memory_bytes': mem,
                         'memory_mb': round(mem / (1024*1024), 2)})
    result['package_dist'] = pkg_dist

    # Name coverage (all class names are real)
    result['name_coverage'] = {'mapped': total_objects, 'unmapped(class_X)': 0, 'unresolved(0)': 0}
    result['shallow_size_estimate_mb'] = round(total_objects * 24 / (1024*1024), 2)

    # GC Roots
    gc_file = os.path.join(parquet_dir, '_gc_roots.parquet')
    if os.path.isfile(gc_file):
        gc_dist = con.execute(f"""
            SELECT root_type, COUNT(*) as count
            FROM read_parquet('{gc_file}')
            GROUP BY root_type ORDER BY count DESC
        """).fetchall()
        # 标准化 root_type 名称以匹配报告检查
        type_map = {
            'Unknown': 'UNKNOWN', 'SystemClass': 'SYSTEM_CLASS',
            'JavaStackFrame': 'JAVA_STACK', 'JniGlobal': 'JNI_GLOBAL',
            'NativeStack': 'NATIVE_STACK', 'ThreadObj': 'THREAD_OBJ',
            'JniLocal': 'JNI_LOCAL',
        }
        result['gc_root_dist'] = [{'type': type_map.get(r[0], r[0].upper()), 'count': r[1]} for r in gc_dist]
        result['total_gc_roots'] = sum(r[1] for r in gc_dist)

        # GC Root → 类关联: 用 DuckDB JOIN _obj_class_map.parquet (快速)
        map_file = os.path.join(parquet_dir, '_obj_class_map.parquet')
        if os.path.isfile(map_file):
            # JAVA_STACK holders via DuckDB JOIN
            js_holders = con.execute(f"""
                SELECT m.class_name, COUNT(*) as held_count
                FROM read_parquet('{gc_file}') gr
                JOIN read_parquet('{map_file}') m ON gr.obj_id = m.obj_id
                WHERE gr.root_type = 'JavaStackFrame'
                GROUP BY m.class_name ORDER BY held_count DESC LIMIT 30
            """).fetchall()
            result['java_stack_holders'] = [{'name': r[0], 'count': r[1],
                'memory_bytes': class_memory.get(r[0], 0),
                'memory_mb': round(class_memory.get(r[0], 0) / (1024*1024), 2)} for r in js_holders]

            # SYSTEM_CLASS holders via DuckDB JOIN
            sc_holders = con.execute(f"""
                SELECT m.class_name, COUNT(*) as held_count
                FROM read_parquet('{gc_file}') gr
                JOIN read_parquet('{map_file}') m ON gr.obj_id = m.obj_id
                WHERE gr.root_type = 'SystemClass'
                GROUP BY m.class_name ORDER BY held_count DESC LIMIT 20
            """).fetchall()
            result['system_class_holders'] = [{'name': r[0], 'count': r[1],
                'memory_bytes': class_memory.get(r[0], 0),
                'memory_mb': round(class_memory.get(r[0], 0) / (1024*1024), 2)} for r in sc_holders]
        else:
            result['java_stack_holders'] = []
            result['system_class_holders'] = []
        result['frame_holders'] = result.get('java_stack_holders', [])[:20]
        result['thread_roots'] = []
    else:
        result['gc_root_dist'] = []
        result['total_gc_roots'] = 0

    # 静态字段
    sf_file = os.path.join(parquet_dir, '_static_fields.parquet')
    result['has_static_fields'] = os.path.isfile(sf_file)
    if result['has_static_fields']:
        sf_holders = con.execute(f"""
            SELECT sf.class_name, COUNT(*) as ref_count
            FROM read_parquet('{sf_file}') sf
            WHERE sf.ref_id > 0
            GROUP BY sf.class_name ORDER BY ref_count DESC LIMIT 50
        """).fetchall()
        # 计算每个持有者引用的对象总内存
        sf_mem_map = {}
        if os.path.isfile(map_file):
            sf_mem_rows = con.execute(f"""
                SELECT m2.class_name, SUM(m1.field_data_size + 16) as mem_bytes
                FROM read_parquet('{sf_file}') sf
                JOIN read_parquet('{map_file}') m1 ON sf.ref_id = m1.obj_id
                JOIN read_parquet('{map_file}') m2 ON sf.class_name = m2.class_name
                WHERE sf.ref_id > 0
                GROUP BY m2.class_name
            """).fetchall()
            for cn, mem_bytes in sf_mem_rows:
                sf_mem_map[cn] = mem_bytes
        result['static_field_holders'] = [{'class': r[0], 'count': r[1],
            'memory_bytes': sf_mem_map.get(r[0], 0),
            'memory_mb': round(sf_mem_map.get(r[0], 0) / (1024*1024), 2)} for r in sf_holders]
    else:
        result['static_field_holders'] = []

    # P1: 引用链分析
    ref_file = os.path.join(parquet_dir, '_object_refs.parquet')
    if os.path.isfile(ref_file):
        result['has_object_refs'] = True
        result['total_refs'] = con.execute(f"SELECT COUNT(*) FROM read_parquet('{ref_file}')").fetchone()[0]
        # 引用链: 哪些类引用最多其他对象
        ref_chain = con.execute(f"""
            SELECT m1.class_name as from_class, m2.class_name as to_class, COUNT(*) as cnt
            FROM read_parquet('{ref_file}') r
            JOIN read_parquet('{os.path.join(parquet_dir, '_obj_class_map.parquet')}') m1 ON r.obj_id = m1.obj_id
            JOIN read_parquet('{os.path.join(parquet_dir, '_obj_class_map.parquet')}') m2 ON r.ref_obj_id = m2.obj_id
            GROUP BY m1.class_name, m2.class_name ORDER BY cnt DESC LIMIT 30
        """).fetchall()
        result['ref_chain'] = [{'from': r[0], 'to': r[1], 'count': r[2]} for r in ref_chain]

        # P2: Retained size 估算 (从 GC Root 可达的对象数)
        gc_obj_ids = [r[0] for r in con.execute(f"SELECT DISTINCT obj_id FROM read_parquet('{gc_file}')").fetchall()]
        all_refs = con.execute(f"SELECT obj_id, ref_obj_id FROM read_parquet('{ref_file}')").fetchall()
        # 构建引用图
        ref_graph = {}
        for src, dst in all_refs:
            if src not in ref_graph:
                ref_graph[src] = []
            ref_graph[src].append(dst)
        # BFS from GC roots
        reachable = set()
        from collections import deque, Counter
        queue = deque(gc_obj_ids)
        for goid in gc_obj_ids:
            reachable.add(goid)
        while queue:
            current = queue.popleft()
            for ref in ref_graph.get(current, []):
                if ref not in reachable:
                    reachable.add(ref)
                    queue.append(ref)
        # 修正: 只统计 Instance 对象的可达性 (与 _obj_class_map 口径一致)
        instance_obj_ids = set(r[0] for r in con.execute(f"SELECT obj_id FROM read_parquet('{map_file}')").fetchall())
        reachable_instances = reachable & instance_obj_ids
        unreachable_instances = instance_obj_ids - reachable_instances
        result['reachable_objects'] = len(reachable_instances)
        result['unreachable_objects'] = len(unreachable_instances)
        # 补全对象总数 (Instance + PrimitiveArray + ObjectArray)
        total_heap_objects = result['total_objects']
        for suffix in ['_object_arrays.parquet']:
            arr_file = os.path.join(parquet_dir, suffix)
            if os.path.isfile(arr_file):
                total_heap_objects += con.execute(f"SELECT COUNT(*) FROM read_parquet('{arr_file}')").fetchone()[0]
        for suffix in ['byte', 'char', 'float', 'double', 'byte', 'short', 'int', 'long']:
            arr_file = os.path.join(parquet_dir, f'_primitive_arrays_{suffix}.parquet')
            if os.path.isfile(arr_file):
                total_heap_objects += con.execute(f"SELECT COUNT(*) FROM read_parquet('{arr_file}')").fetchone()[0]
        result['total_heap_objects'] = total_heap_objects
        
        # 不可达对象按类分组 + 分类标注
        if os.path.isfile(map_file):
            all_objs = con.execute(f"SELECT obj_id, class_name, field_data_size FROM read_parquet('{map_file}')").fetchall()
            unreachable_by_class = Counter()
            unreachable_mem = Counter()
            unreachable_category = Counter()  # temporary / leak / unknown
            for oid, cn, fds in all_objs:
                if oid not in reachable_instances:
                    unreachable_by_class[cn] += 1
                    unreachable_mem[cn] += fds + 16
                    # 分类
                    if is_system_class(cn):
                        unreachable_category['temporary'] += 1
                    elif is_app_class(cn):
                        unreachable_category['leak'] += 1
                    else:
                        unreachable_category['unknown'] += 1
            result['unreachable_by_class'] = [
                {'class': cn, 'count': cnt, 'memory_bytes': unreachable_mem[cn],
                 'category': 'temporary' if is_system_class(cn) else ('leak' if is_app_class(cn) else 'unknown')}
                for cn, cnt in unreachable_by_class.most_common(15)
            ]
            result['unreachable_summary'] = dict(unreachable_category)
        else:
            result['unreachable_by_class'] = []
        # Top retained: 哪些对象被最多其他对象引用
        from collections import Counter as _Counter2
        in_degree = _Counter2()
        for _, dst in all_refs:
            in_degree[dst] += 1
        result['top_retained'] = [{'obj_id': oid, 'in_degree': cnt} for oid, cnt in in_degree.most_common(20)]
    else:
        result['has_object_refs'] = False
        result['ref_chain'] = []
        result['total_refs'] = 0
        result['reachable_objects'] = 0
        result['unreachable_objects'] = 0

    # 可疑类引用链路 (incoming + outgoing)
    suspicious_ref_chains = {}
    if os.path.isfile(ref_file) and os.path.isfile(map_file):
        for cn, cnt in class_counts[:20]:  # Top 20 类的引用链路
            # 谁引用了这个类 (incoming)
            incoming = con.execute(f"""
                SELECT m1.class_name, COUNT(*) as cnt
                FROM read_parquet('{ref_file}') r
                JOIN read_parquet('{map_file}') m1 ON r.obj_id = m1.obj_id
                JOIN read_parquet('{map_file}') m2 ON r.ref_obj_id = m2.obj_id
                WHERE m2.class_name = '{cn}'
                GROUP BY m1.class_name ORDER BY cnt DESC LIMIT 10
            """).fetchall()
            # 这个类引用了谁 (outgoing)
            outgoing = con.execute(f"""
                SELECT m2.class_name, COUNT(*) as cnt
                FROM read_parquet('{ref_file}') r
                JOIN read_parquet('{map_file}') m1 ON r.obj_id = m1.obj_id
                JOIN read_parquet('{map_file}') m2 ON r.ref_obj_id = m2.obj_id
                WHERE m1.class_name = '{cn}'
                GROUP BY m2.class_name ORDER BY cnt DESC LIMIT 10
            """).fetchall()
            suspicious_ref_chains[cn] = {
                'incoming': [{'class': r[0], 'count': r[1]} for r in incoming],
                'outgoing': [{'class': r[0], 'count': r[1]} for r in outgoing],
            }
    result['suspicious_ref_chains'] = suspicious_ref_chains

    # 类层次
    result['hierarchy'] = [{'name': cn, 'instances': cnt,
        'memory_bytes': class_memory.get(cn, 0),
        'memory_mb': round(class_memory.get(cn, 0) / (1024*1024), 2)} for cn, cnt in class_counts[:30]]

    # 线程和栈帧
    result['threads'] = []
    result['frames'] = []
    result['std_class_count'] = len(class_files)

    return result


# 系统类前缀（不标记为泄漏）
SYSTEM_PREFIXES = (
    'java.', 'sun.', 'libcore.', 'dalvik.', 'android.', 'androidx.',
    'com.android.', 'com.google.', 'kotlin.', 'com.facebook.',
    'com.squareup.', 'io.reactivex.', 'io.grpc.', 'org.w3c.',
)

# App 类前缀（可能泄漏）
APP_PREFIXES = (
    'com.xingjiabi.', 'com.xmhaihao.', 'com.xmhaibao.',
    'cn.taqu.', 'hb.', 'com.ushengsheng.',
)

def is_app_class(class_name: str) -> bool:
    """判断是否为 App 类。"""
    return any(class_name.startswith(p) for p in APP_PREFIXES)

def is_system_class(class_name: str) -> bool:
    """判断是否为系统框架类。"""
    return any(class_name.startswith(p) for p in SYSTEM_PREFIXES)

def classify_leak_severity(class_name: str, instance_count: int, held_count: int = 0) -> tuple:
    """根据类名和实例数判断泄漏严重性 (P0-P3)。
    
    区分 App 类和系统类：
    - App 类：低阈值，更敏感地检测泄漏
    - 系统类：高阈值，仅信息性标注
    """
    suspicious_patterns = [
        ('Activity', 'Activity 泄漏'),
        ('Fragment', 'Fragment 泄漏'),
        ('WebView', 'WebView 泄漏'),
        ('Handler', 'Handler 泄漏'),
        ('Runnable', 'Runnable 泄漏'),
        ('Listener', '监听器未注销'),
        ('Callback', '回调未注销'),
        ('Cache', '缓存泄漏'),
        ('Manager', '单例持有泄漏'),
        ('Singleton', '单例持有泄漏'),
    ]
    
    is_app = is_app_class(class_name)
    is_system = is_system_class(class_name)
    
    # App 类：更严格的检测
    if is_app:
        for pattern, leak_type in suspicious_patterns:
            if pattern.lower() in class_name.lower():
                if instance_count > 100 or held_count > 10:
                    return 'P0', leak_type
                elif instance_count > 10 or held_count > 0:
                    return 'P1', leak_type
                else:
                    return 'P2', leak_type
        
        # App 类无名模式匹配，按实例数判定
        if instance_count > 1000 or held_count > 100:
            return 'P0', 'App 类高实例泄漏'
        elif instance_count > 100 or held_count > 10:
            return 'P1', 'App 类值得关注'
        elif instance_count > 10:
            return 'P2', 'App 类值得关注'
        else:
            return 'P3', '正常范围'
    
    # 系统类：仅信息性标注，不标记为泄漏
    if is_system:
        if instance_count > 100000:
            return 'P3', '系统基础设施（信息性）'
        elif instance_count > 10000:
            return 'P3', '系统类高实例（信息性）'
        else:
            return 'P3', '正常范围'
    
    # 其他类（第三方库等）
    for pattern, leak_type in suspicious_patterns:
        if pattern.lower() in class_name.lower():
            if instance_count > 1000 or held_count > 100:
                return 'P1', leak_type
            elif instance_count > 100:
                return 'P2', leak_type
            else:
                return 'P3', leak_type
    
    if instance_count > 50000 or held_count > 5000:
        return 'P2', '高实例类（信息性）'
    elif instance_count > 10000 or held_count > 1000:
        return 'P3', '高实例类（信息性）'
    elif instance_count > 1000 or held_count > 100:
        return 'P3', '值得关注'
    else:
        return 'P3', '正常范围'


def generate_report(analysis: Dict[str, Any], output_path: str):
    """根据 DuckDB 分析结果生成完整 Markdown 报告。"""
    lines = []

    total_objs = analysis.get('total_objects', 0)
    total_gc = analysis.get('total_gc_roots', 0)
    generated_at = time.strftime('%Y-%m-%d %H:%M:%S')
    parquet_dir = analysis.get('parquet_dir', '')

    # ── 标题 ─────────────────────────────────────────────────────────
    lines.append("# HPROF 内存分析报告\n")
    lines.append(f"**生成时间**: {generated_at}\n")
    lines.append(f"**Parquet 目录**: `{parquet_dir}`\n")

    # ── 概要 ─────────────────────────────────────────────────────────
    lines.append("## 概要\n")

    coverage = analysis.get('name_coverage', {})
    mapped = coverage.get('mapped', 0)
    unmapped = coverage.get('unmapped(class_X)', 0) + coverage.get('unresolved(0)', 0)
    cov_pct = mapped / total_objs * 100 if total_objs > 0 else 0

    lines.append("### 关键指标\n")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 总对象数 | {total_objs:,} |")
    lines.append(f"| 类名覆盖率 | {cov_pct:.1f}% ({mapped:,}/{total_objs:,}) |")
    lines.append(f"| GC Root 数量 | {total_gc} |")
    total_mem = sum(c.get('memory_bytes', 0) for c in analysis.get('top_classes', []))
    lines.append(f"| 浅大小估算 | {analysis.get('shallow_size_estimate_mb', 0):.2f} MB |")
    lines.append(f"| 实例内存占用 | {total_mem / (1024*1024):.2f} MB |")
    reachable = analysis.get('reachable_objects', 0)
    unreachable = analysis.get('unreachable_objects', 0)
    if reachable > 0 or unreachable > 0:
        # 估算可达/不可达内存 (按比例分配)
        total_mem_bytes = total_mem
        if total_objs > 0:
            reach_mem = total_mem_bytes * reachable / total_objs
            unreach_mem = total_mem_bytes * unreachable / total_objs
            lines.append(f"| 可达对象内存 | {reach_mem / (1024*1024):.2f} MB ({reachable:,} 对象) |")
            lines.append(f"| 不可达对象内存 | {unreach_mem / (1024*1024):.2f} MB ({unreachable:,} 对象) |")
    total_heap = analysis.get("total_heap_objects", total_objs)
    lines.append(f"| 堆对象总数 (含数组) | {total_heap:,} |")
    # 概要中增加线程数
    thread_count = len(analysis.get('threads', []))
    lines.append(f"| 线程快照数 | {thread_count} |")
    if not analysis.get('has_static_fields'):
        lines.append(f"| ⚠️ 静态字段分析 | {analysis.get('static_fields_note', '静态字段数据不可用')} |")
    lines.append("\n### 健康状况\n")
    if total_gc > 100:
        lines.append(f"🟡 **中等风险** — 检测到 {total_gc} 个 GC Root，建议进一步分析引用链。")
    elif total_gc > 0:
        lines.append(f"🟢 **低风险** — GC Root 数量正常（{total_gc} 个）。")
    else:
        lines.append(f"⚪ **未知** — 未找到 GC Root 数据。")

    # ── 堆分布 ───────────────────────────────────────────────────────
    lines.append("\n## 堆分布\n")

    lines.append("### 对象数量 Top 30\n")
    lines.append("| 排名 | 类名 | 实例数 | 占比 | 内存占用 |")
    lines.append("|------|------|--------|------|----------|")
    for i, cls in enumerate(analysis.get('top_classes', [])[:30], 1):
        pct = cls['count'] / total_objs * 100 if total_objs > 0 else 0
        name = cls['name']
        if '$' in name:
            name += " ← Kotlin synthetic"
        mem_mb = cls.get('memory_mb', 0)
        mem_str = f"{mem_mb:.2f} MB" if mem_mb >= 1 else f"{cls.get('memory_bytes', 0) / 1024:.1f} KB"
        lines.append(f"| {i} | `{name}` | {cls['count']:,} | {pct:.1f}% | {mem_str} |")

    lines.append("\n### 包级别分布\n")
    lines.append("| 包组 | 对象数 | 占比 | 内存占用 |")
    lines.append("|------|--------|------|----------|")
    for pkg in analysis.get('package_dist', []):
        pct = pkg['count'] / total_objs * 100 if total_objs > 0 else 0
        mem = pkg.get('memory_mb', 0)
        mem_str = f"{mem:.2f} MB" if mem >= 1 else f"{pkg.get('memory_bytes', 0) / 1024:.1f} KB"
        lines.append(f"| `{pkg['group']}` | {pkg['count']:,} | {pct:.1f}% | {mem_str} |")

    # ── GC Root 分析 ─────────────────────────────────────────────────
    lines.append("\n## GC Root 分析\n")

    if analysis.get('gc_root_dist'):
        lines.append("### Root 类型分布\n")
        lines.append("| Root 类型 | 数量 | 说明 |")
        lines.append("|-----------|------|------|")
        kind_desc = {
            'JAVA_STACK': 'Java 栈帧引用', 'NATIVE_STACK': 'Native 栈帧引用',
            'SYSTEM_CLASS': '系统类加载器引用', 'GC_STATIC_FIELD': '静态字段引用',
            'GC_LOCAL': '局部变量引用', 'GC_MONITOR': 'Monitor 锁引用',
            'GC_JAVA_FRAME': 'Java 帧引用', 'GC_NATIVE_FRAME': 'Native 帧引用',
            'UNREACHABLE': '不可达对象', 'DAEMON_WORKER': '守护线程工作器',
            'UNKNOWN': '未知来源',
        }
        for item in analysis['gc_root_dist']:
            desc = kind_desc.get(item['type'], '')
            lines.append(f"| {item['type']} | {item['count']} | {desc} |")

        if analysis.get('java_stack_holders'):
            lines.append("\n### JAVA_STACK 持有 Top 类\n")
            lines.append("| 类名 | 被持有数 | 内存占用 | 风险 |")
            lines.append("|------|----------|----------|------|")
            for item in analysis['java_stack_holders'][:20]:
                severity, _ = classify_leak_severity(item['name'], item['count'])
                icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(severity, '⚪')
                mem = item.get('memory_mb', 0)
                mem_str = f"{mem:.2f} MB" if mem >= 1 else f"{item.get('memory_bytes', 0) / 1024:.1f} KB"
                lines.append(f"| `{item['name']}` | {item['count']:,} | {mem_str} | {icon} {severity} |")

        if analysis.get('system_class_holders'):
            lines.append("\n### SYSTEM_CLASS 持有 Top 类\n")
            lines.append("| 类名 | 被持有数 | 内存占用 | 风险 |")
            lines.append("|------|----------|----------|------|")
            for item in analysis['system_class_holders'][:15]:
                severity, _ = classify_leak_severity(item['name'], item['count'])
                icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(severity, '⚪')
                mem = item.get('memory_mb', 0)
                mem_str = f"{mem:.2f} MB" if mem >= 1 else f"{item.get('memory_bytes', 0) / 1024:.1f} KB"
                lines.append(f"| `{item['name']}` | {item['count']:,} | {mem_str} | {icon} {severity} |")

        if analysis.get('frame_holders'):
            lines.append("\n### GC_LOCAL / GC_JAVA_FRAME 持有 Top 类\n")
            lines.append("| 类名 | 被持有数 | 风险 |")
            lines.append("|------|----------|------|")
            for item in analysis['frame_holders'][:15]:
                severity, _ = classify_leak_severity(item['name'], item['count'])
                icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(severity, '⚪')
                lines.append(f"| `{item['name']}` | {item['count']:,} | {icon} {severity} |")
    else:
        lines.append("⚠️ 未找到 GC Root 数据。\n")

    # ── 内存泄漏深度分析 ─────────────────────────────────────────────
    lines.append("\n## 内存泄漏深度分析\n")

    # 找出可疑泄漏类
    suspicious = []
    system_info = []
    for cls in analysis.get('top_classes', []):
        cn = cls['name']
        cnt = cls['count']
        is_app = is_app_class(cn)
        is_sys = is_system_class(cn)
        
        held = 0
        for item in analysis.get('java_stack_holders', []) + analysis.get('system_class_holders', []):
            if item['name'] == cn:
                held = max(held, item['count'])
        
        severity, leak_type = classify_leak_severity(cn, cnt, held)
        
        if is_app and (cnt > 10 or held > 0 or any(p in cn.lower() for p in ['activity', 'fragment', 'manager', 'singleton', 'cache', 'handler'])):
            suspicious.append({
                'name': cn, 'instances': cnt, 'held': held,
                'severity': severity, 'type': leak_type,
                'memory_mb': cls.get('memory_mb', 0),
                'memory_bytes': cls.get('memory_bytes', 0),
            })
        elif is_sys and cnt > 5000:
            system_info.append({
                'name': cn, 'instances': cnt, 'held': held,
                'type': '系统基础设施',
                'memory_mb': cls.get('memory_mb', 0),
                'memory_bytes': cls.get('memory_bytes', 0),
            })
        elif not is_sys and cnt > 1000:
            suspicious.append({
                'name': cn, 'instances': cnt, 'held': held,
                'severity': severity, 'type': leak_type,
                'memory_mb': cls.get('memory_mb', 0),
                'memory_bytes': cls.get('memory_bytes', 0),
            })

    if suspicious:
        lines.append("### 可疑泄漏类（高实例数 + GC Root 持有）\n")
        lines.append("| 类名 | 实例数 | 内存占用 | GC Root 持有 | 严重性 | 泄漏模式 |")
        lines.append("|------|--------|----------|--------------|--------|----------|")
        for s in sorted(suspicious, key=lambda x: (x['severity'], -x['held'])):
            cls_mem = next((c.get('memory_mb', 0) for c in analysis.get('top_classes', []) if c['name'] == s['name']), 0)
            cls_mem_bytes = next((c.get('memory_bytes', 0) for c in analysis.get('top_classes', []) if c['name'] == s['name']), 0)
            mem_str = f"{cls_mem:.2f} MB" if cls_mem >= 1 else f"{cls_mem_bytes / 1024:.1f} KB"
            icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(s['severity'], '⚪')
            lines.append(f"| `{s['name']}` | {s['instances']:,} | {mem_str} | {s['held']:,} | {icon} {s['severity']} | {s['type']} |")
    else:
        lines.append("✅ 未发现明显的高实例数泄漏类。\n")

    # 静态字段分析
    if analysis.get('has_static_fields'):
        lines.append("\n### 静态字段持有者 Top 30\n")
        lines.append("| 类名 | 持有引用数 | 被引用内存 |")
        lines.append("|------|------------|------------|")
        for sf in analysis.get('static_field_holders', [])[:30]:
            mem = sf.get('memory_bytes', 0)
            if mem > 0:
                mem_mb = mem / (1024*1024)
                mem_str = f"{mem_mb:.2f} MB" if mem_mb >= 1 else f"{mem / 1024:.1f} KB"
            else:
                mem_str = "—"
            lines.append(f"| `{sf['class']}` | {sf['count']:,} | {mem_str} |")

    # 类层次
    if analysis.get('hierarchy'):
        lines.append("\n### 类层次 Top 类（来自 CLASS_DUMP）\n")
        lines.append("| 类名 | 实例数 | 内存占用 |")
        lines.append("|------|--------|----------|")
        for h in analysis['hierarchy'][:20]:
            mem = h.get('memory_mb', 0)
            mem_str = f"{mem:.2f} MB" if mem >= 1 else f"{h.get('memory_bytes', 0) / 1024:.1f} KB"
            lines.append(f"| `{h['name']}` | {h['instances']:,} | {mem_str} |")

    # ── 线程快照 ─────────────────────────────────────────────────────
    lines.append("\n## 线程快照\n")
    if analysis.get('threads'):
        thread_count = len(analysis['threads'])
        lines.append(f"### 活跃线程 ({thread_count} 个)\n")
        lines.append("| 线程 Serial | 名称 | 挂起类型 |")
        lines.append("|-------------|------|----------|")
        for t in analysis['threads'][:50]:
            lines.append(f"| {t['serial']} | `{t['name'] or '(匿名)'}` | {t['suspend_type']} |")
        if thread_count > 50:
            lines.append(f"\n> 显示前 50 个，共 {thread_count} 个线程\n")
    else:
        lines.append("⚠️ 未找到线程数据。\n")

    if analysis.get('frames'):
        lines.append("\n### 栈帧样本\n")
        lines.append("| 帧 ID | 类名 | 类型码 |")
        lines.append("|-------|------|--------|")
        for f in analysis['frames'][:20]:
            lines.append(f"| {f['frame_id']} | `{f['class_name']}` | 0x{f['type_code']:02X} |")

    # ── 风险评级 ─────────────────────────────────────────────────────
    lines.append("\n## 风险评级\n")

    all_severities = []
    for s in suspicious:
        all_severities.append(s['severity'])
    for item in analysis.get('java_stack_holders', []):
        sev, _ = classify_leak_severity(item['name'], item['count'])
        all_severities.append(sev)
    for item in analysis.get('system_class_holders', []):
        sev, _ = classify_leak_severity(item['name'], item['count'])
        all_severities.append(sev)

    if 'P0' in all_severities:
        overall, overall_icon, overall_desc = 'P0', '🔴', '严重泄漏 — 需立即修复'
    elif 'P1' in all_severities:
        overall, overall_icon, overall_desc = 'P1', '🟠', '显著泄漏 — 应尽快修复'
    elif 'P2' in all_severities:
        overall, overall_icon, overall_desc = 'P2', '🟡', '中等泄漏 — 计划修复'
    else:
        overall, overall_icon, overall_desc = 'P3', '🟢', '轻微 — 值得关注但优先级低'

    lines.append("| 等级 | 条件 | 结论 |")
    lines.append("|------|------|------|")
    lines.append("| 🔴 P0 | > 50MB 泄漏对象 | 严重 — 需立即修复 |")
    lines.append("| 🟠 P1 | > 20MB 泄漏对象 | 显著 — 应尽快修复 |")
    lines.append("| 🟡 P2 | > 5MB 泄漏对象 | 中等 — 计划修复 |")
    lines.append("| 🟢 P3 | ≤ 5MB 泄漏对象 | 轻微 — 值得关注 |")
    lines.append(f"\n**总体评级**: {overall_icon} **{overall}** — {overall_desc}\n")

    # ── 下一步行动（动态生成）────────────────────────────────────────
    lines.append("## 下一步行动\n")
    action_num = 1

    # 1. 根据 P0/P1 可疑类给出具体建议
    p0_p1 = [s for s in suspicious if s['severity'] in ('P0', 'P1')]
    if p0_p1:
        top_susp = p0_p1[0]
        mem = next((c.get('memory_mb', 0) for c in analysis.get('top_classes', []) if c['name'] == top_susp['name']), 0)
        lines.append(f"{action_num}. **优先处理 {top_susp['severity']} 类 `{top_susp['name']}`** — {top_susp['instances']:,} 个实例")
        if mem > 0:
            lines.append(f"   占用 {mem:.2f} MB 内存")
        if top_susp['held'] > 0:
            lines.append(f"   被 GC Root 持有 {top_susp['held']:,} 个引用\n")
        else:
            lines.append("\n")
        action_num += 1

    # 2. 不可达对象分析
    unreachable = analysis.get('unreachable_objects', 0)
    if unreachable > 0:
        total_mem_bytes = sum(c.get('memory_bytes', 0) for c in analysis.get('top_classes', []))
        if total_objs > 0:
            unreach_mem = total_mem_bytes * unreachable / total_objs / (1024 * 1024)
        else:
            unreach_mem = 0
        lines.append(f"{action_num}. **检查不可达对象** — {unreachable:,} 个对象不可达（约 {unreach_mem:.2f} MB），可能需要 GC 回收\n")
        unreachable_classes = analysis.get("unreachable_by_class", [])
        unreach_summary = analysis.get("unreachable_summary", {})
        if unreach_summary:
            temp_cnt = unreach_summary.get('temporary', 0)
            leak_cnt = unreach_summary.get('leak', 0)
            unknown_cnt = unreach_summary.get('unknown', 0)
            lines.append(f"\n   **不可达对象分类:**\n")
            if temp_cnt > 0:
                lines.append(f"   - 🟢 系统临时对象（正常）: {temp_cnt:,} 个 — GC 将回收\n")
            if leak_cnt > 0:
                lines.append(f"   - 🔴 App 类不可达（潜在泄漏）: {leak_cnt:,} 个 — 需排查\n")
            if unknown_cnt > 0:
                lines.append(f"   - 🟡 第三方库不可达（待确认）: {unknown_cnt:,} 个\n")
            if leak_cnt == 0:
                lines.append(f"   ✅ 无 App 类不可达对象，App 对象全部可达 — 无内存泄漏迹象\n")
        if unreachable_classes:
            lines.append(f"\n   **不可达对象 Top 5 类:**\n")
            lines.append(f"   | 类名 | 实例数 | 内存 | 分类 |")
            lines.append(f"   |------|--------|------|------|")
            for uc in unreachable_classes[:5]:
                mem_mb = uc["memory_bytes"] / (1024*1024)
                mem_str = f"{mem_mb:.2f} MB" if mem_mb >= 1 else f"{uc['memory_bytes'] / 1024:.1f} KB"
                cat = uc.get('category', 'unknown')
                cat_icon = {'temporary': '🟢临时', 'leak': '🔴泄漏', 'unknown': '🟡待确认'}.get(cat, '🟡')
                lines.append(f"   | `{uc['class']}` | {uc['count']:,} | {mem_str} | {cat_icon} |")
            lines.append("\n")
        action_num += 1

    # 3. 引用链中的 App 类
    ref_chains = analysis.get('suspicious_ref_chains', {})
    app_incoming = []
    for cn, chain in ref_chains.items():
        for ref in chain.get('incoming', []):
            if any(p in ref['class'] for p in ['com.xingjiabi', 'com.xmhaihao', 'com.xmhaibao', 'cn.taqu', 'hb.']):
                app_incoming.append((ref['class'], ref['count'], cn))
    if app_incoming:
        app_incoming.sort(key=lambda x: -x[1])
        top_app_ref = app_incoming[0]
        lines.append(f"{action_num}. **检查引用链中的 App 类** — `{top_app_ref[0]}` ★ 持有 `{top_app_ref[2]}` 的 {top_app_ref[1]:,} 个引用\n")
        action_num += 1

    # 4. 静态字段持有者
    sf_holders = analysis.get('static_field_holders', [])
    if sf_holders:
        top_sf = sf_holders[0]
        lines.append(f"{action_num}. **检查静态字段持有者** — `{top_sf['class']}` 持有 {top_sf['count']:,} 个静态引用\n")
        action_num += 1

    # 5. 类名覆盖率建议
    if cov_pct < 80:
        lines.append(f"{action_num}. **提供 ProGuard/R8 mapping 文件** — 当前覆盖率仅 {cov_pct:.1f}%\n")
        action_num += 1

    # 6. 引用链数据可用性
    if analysis.get('has_object_refs'):
        total_refs = analysis.get('total_refs', 0)
        lines.append(f"{action_num}. **利用引用链深度排查** — 共 {total_refs:,} 条引用关系可用，可追溯任意对象的引用路径\n")
        action_num += 1

    if system_info:
        lines.append("\n### 系统类高实例（信息性，非泄漏）\n")
        lines.append("| 类名 | 实例数 | 内存占用 | 说明 |")
        lines.append("|------|--------|----------|------|")
        for s in system_info[:10]:
            mem = s.get('memory_mb', 0)
            mem_str = f"{mem:.2f} MB" if mem >= 1 else f"{s.get('memory_bytes', 0) / 1024:.1f} KB"
            lines.append(f"| `{s['name']}` | {s['instances']:,} | {mem_str} | {s['type']} |")

    if suspicious:
        lines.append("\n### 针对可疑类的具体排查建议\n")
        for s in sorted(suspicious, key=lambda x: (x['severity'], -x['instances']))[:5]:
            lines.append(f"#### `{s['name']}` ({s['severity']})\n")
            lines.append(f"- 实例数: {s['instances']:,}")
            lines.append(f"- 泄漏模式: {s['type']}")
            if s['held'] > 0:
                lines.append(f"- GC Root 持有: {s['held']:,} 个引用")
                # 引用链路展示
                ref_chains = analysis.get('suspicious_ref_chains', {})
                chain = ref_chains.get(s['name'], {})
                incoming = chain.get('incoming', [])
                outgoing = chain.get('outgoing', [])
                if incoming:
                    lines.append(f"\n**被以下类引用** (谁持有我 → 共 {len(incoming)} 类):\n")
                    lines.append("| 引用方 | 引用数 |")
                    lines.append("|--------|--------|")
                    for ref in incoming[:10]:
                        is_app_ref = any(p in ref['class'] for p in ['com.xingjiabi', 'com.xmhaihao', 'com.xmhaibao', 'cn.taqu', 'hb.'])
                        lines.append(f"| `{ref['class']}` {'★' if is_app_ref else ''} | {ref['count']:,} |")
                if outgoing:
                    lines.append(f"\n**引用以下类** (我持有谁 → 共 {len(outgoing)} 类):\n")
                    lines.append("| 被引用方 | 引用数 |")
                    lines.append("|----------|--------|")
                    for ref in outgoing[:10]:
                        is_app_ref = any(p in ref['class'] for p in ['com.xingjiabi', 'com.xmhaihao', 'com.xmhaibao', 'cn.taqu', 'hb.'])
                        lines.append(f"| `{ref['class']}` {'★' if is_app_ref else ''} | {ref['count']:,} |")
                if not incoming and not outgoing:
                    lines.append("\n*无引用链路数据*\n")
            lines.append("- 建议:\n  - 检查是否有静态集合持有此类\n  - 检查 Activity/Fragment 的生命周期管理\n  - 检查监听器/回调是否正确注销\n")

    # ── 质量评估 ─────────────────────────────────────────────────────
    lines.append("\n## 质量评估（对照 quality-standards.md）\n")

    if cov_pct >= 95:
        name_score, name_note = 4, "绝大多数类名可识别"
    elif cov_pct >= 80:
        name_score, name_note = 3, "大部分类名可识别"
    elif cov_pct >= 50:
        name_score, name_note = 2, "部分类名可识别"
    else:
        name_score, name_note = 1, "类名识别度低，需 ProGuard mapping"

    if total_gc > 0 and analysis.get('java_stack_holders'):
        # Check if 'unknown' holders < 20%
        js_unknown = sum(h['count'] for h in analysis.get('java_stack_holders', []) if h['name'] == 'unknown')
        js_total = sum(h['count'] for h in analysis.get('java_stack_holders', []))
        if js_total > 0 and js_unknown / js_total < 0.2:
            assoc_score, assoc_note = 4, "GC Root 精确关联 (unknown < 20%)"
        else:
            assoc_score, assoc_note = 3, "GC Root 与对象有关联"
    elif total_gc > 0 and (analysis.get('java_stack_holders') or analysis.get('system_class_holders')):
        assoc_score, assoc_note = 3, "GC Root 与对象有关联"
    else:
        assoc_score, assoc_note = 1, "GC Root 分析不完整"

    has_js = any(r['type'] == 'JAVA_STACK' for r in analysis.get('gc_root_dist', []))
    if has_js and analysis.get('java_stack_holders'):
        if analysis.get('has_object_refs'):
            gc_score, gc_note = 4, "JavaStackFrame + 引用链完整"
        else:
            gc_score, gc_note = 3, "JavaStackFrame 持有分析完整"
    elif has_js:
        gc_score, gc_note = 2, "有 GC Root 类型分布但持有分析不完整"
    else:
        gc_score, gc_note = 1, "GC Root 数据有限"

    if suspicious and analysis.get("reachable_objects", 0) > 0:
        leak_score, leak_note = 4, "可疑类 + retained size 分析"
    elif suspicious:
        leak_score, leak_note = 3, f"识别出 {len(suspicious)} 个可疑类并有风险评级"
    else:
        leak_score, leak_note = 2, "未发现明显泄漏，但需 ProGuard mapping 进一步验证"

    if analysis.get('has_static_fields'):
        data_score, data_note = 4, "所有数据源完整（含静态字段）"
    else:
        data_score, data_note = 2, "缺少 _static_fields，A3 分析不可用"

    total_score = name_score + assoc_score + gc_score + leak_score + data_score
    if total_score >= 17:
        rating = "B — 可用"
    elif total_score >= 13:
        rating = "C — 基本可用"
    elif total_score >= 9:
        rating = "D — 勉强可用"
    else:
        rating = "F — 不可用"

    lines.append("| 维度 | 得分 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| 类名可识别度 | {name_score}/4 | {name_note} ({cov_pct:.1f}%) |")
    lines.append(f"| 对象实例关联 | {assoc_score}/4 | {assoc_note} |")
    lines.append(f"| GC Root 分析深度 | {gc_score}/4 | {gc_note} |")
    lines.append(f"| 泄漏诊断 actionable | {leak_score}/4 | {leak_note} |")
    lines.append(f"| 数据完整性 | {data_score}/4 | {data_note} |")
    lines.append(f"| **总分** | **{total_score}/20** | **{rating}** |")

    # 写入文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path


def analyze_hprof(
    hprof_name: str,
    project_root: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Main analysis function — converts HPROF to Parquet, runs DuckDB analysis,
    and generates a comprehensive memory analysis report.

    Args:
        hprof_name: Filename without .hprof extension
        project_root: Project root (defaults to parent of this file)

    Returns:
        Dictionary containing analysis results with output paths
    """
    if project_root is None:
        project_root = Path(__file__).parents[2]

    hprof_path = str(project_root / "hprof" / f"{hprof_name}.hprof")
    basename = hprof_name
    output_base = project_root / "hprof_analysis" / basename
    output_base.mkdir(parents=True, exist_ok=True)

    # 目录结构: hprof_analysis/xxx/
    #   raw/        — 原始 hprof 副本
    #   standard/   — hprof-conv 转换的标准 HPROF
    #   parquet/    — Parquet 数据
    #   xxx_report.md — 分析报告
    raw_dir = output_base / "raw"
    raw_dir.mkdir(exist_ok=True)
    standard_dir = output_base / "standard"
    standard_dir.mkdir(exist_ok=True)
    parquet_dir = str(output_base / "parquet")
    os.makedirs(parquet_dir, exist_ok=True)

    # 复制原始 hprof 到 raw/ 目录
    import shutil
    raw_copy = str(raw_dir / f"{basename}.hprof")
    if not os.path.isfile(raw_copy):
        shutil.copy2(hprof_path, raw_copy)

    # Step 1: Convert HPROF to Parquet
    counts = ensure_parquet(hprof_path, parquet_dir)

    # Step 2: Run DuckDB analysis
    print(f"\nRunning DuckDB analysis...")
    analysis = run_duckdb_analysis(parquet_dir)

    # 如果是 HeapDumpStarDiver 模式，需要清理 parquet 子目录
    if counts.get('star_diver'):
        # parquet 数据在 parquet/parquet/ 下，需要调整路径
        inner_parquet = os.path.join(parquet_dir, 'parquet')
        if os.path.isdir(inner_parquet):
            analysis = run_duckdb_analysis(inner_parquet)

    # Step 3: Generate report
    timestamp = get_timestamp_suffix()
    report_path = str(output_base / f"{basename}_report.md")
    generate_report(analysis, report_path)
    print(f"  ✓ Report saved to {report_path}")

    result = {
        'input_hprof': hprof_path,
        'hprof_name': basename,
        'output_base': str(output_base),
        'parquet_dir': parquet_dir,
        'parquet_counts': counts,
        'analysis': analysis,
        'report_path': report_path,
        'report_filename': f"{basename}_report.md",
        'status': 'completed',
    }

    print(f"\n✅ Analysis completed successfully!")
    print(f"📁 Output location: {output_base}")
    print(f"📊 Total objects: {counts['objects']:,}")
    print(f"📊 GC Roots: {analysis.get('total_gc_roots', 0)}")
    print(f"📄 Report: {report_path}")

    return result


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <hprof_filename_without_extension>")
        print("Example: python analyzer.py taqu_android_client_logfile_401")
        sys.exit(1)

    hprof_file = sys.argv[1]
    result = analyze_hprof(hprof_file)
    print(f"\n📊 Final output directory: {result['output_base']}")
    print(f"📄 Report file: {result['report_path']}")
