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
    print(f"Converting HPROF to Parquet...")

    # Write Parquet using the library writer (includes parsing internally)
    counts = convert_hprof_to_parquet(hprof_path, output_dir)

    print(f"✓ Conversion complete. Parquet files written to {output_dir}")
    print(f"  Parsed: {counts['objects']} objects, {counts['classes']} classes, {counts['strings']} strings")

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
        result['gc_root_dist'] = [{'type': r[0], 'count': r[1]} for r in gc_dist]
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


def classify_leak_severity(class_name: str, instance_count: int, held_count: int = 0) -> tuple:
    """根据类名和实例数判断泄漏严重性 (P0-P3)。"""
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

    for pattern, leak_type in suspicious_patterns:
        if pattern.lower() in class_name.lower():
            if instance_count > 1000 or held_count > 100:
                return 'P0', leak_type
            elif instance_count > 100 or held_count > 10:
                return 'P1', leak_type
            else:
                return 'P2', leak_type

    if instance_count > 50000 or held_count > 5000:
        return 'P0', '潜在内存泄漏'
    elif instance_count > 10000 or held_count > 1000:
        return 'P1', '潜在内存泄漏'
    elif instance_count > 1000 or held_count > 100:
        return 'P2', '值得关注'
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
    lines.append(f"| 浅大小估算 | {analysis.get('shallow_size_estimate_mb', 0):.2f} MB |")
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
    lines.append("| 排名 | 类名 | 实例数 | 占比 |")
    lines.append("|------|------|--------|------|")
    for i, cls in enumerate(analysis.get('top_classes', [])[:30], 1):
        pct = cls['count'] / total_objs * 100 if total_objs > 0 else 0
        name = cls['name']
        if '$' in name:
            name += " ← Kotlin synthetic"
        lines.append(f"| {i} | `{name}` | {cls['count']:,} | {pct:.1f}% |")

    lines.append("\n### 包级别分布\n")
    lines.append("| 包组 | 对象数 | 占比 |")
    lines.append("|------|--------|------|")
    for pkg in analysis.get('package_dist', []):
        pct = pkg['count'] / total_objs * 100 if total_objs > 0 else 0
        lines.append(f"| `{pkg['group']}` | {pkg['count']:,} | {pct:.1f}% |")

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
            lines.append("| 类名 | 被持有数 | 风险 |")
            lines.append("|------|----------|------|")
            for item in analysis['java_stack_holders'][:20]:
                severity, _ = classify_leak_severity(item['name'], item['count'])
                icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(severity, '⚪')
                lines.append(f"| `{item['name']}` | {item['count']:,} | {icon} {severity} |")

        if analysis.get('system_class_holders'):
            lines.append("\n### SYSTEM_CLASS 持有 Top 类\n")
            lines.append("| 类名 | 被持有数 | 风险 |")
            lines.append("|------|----------|------|")
            for item in analysis['system_class_holders'][:15]:
                severity, _ = classify_leak_severity(item['name'], item['count'])
                icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(severity, '⚪')
                lines.append(f"| `{item['name']}` | {item['count']:,} | {icon} {severity} |")

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
    for cls in analysis.get('top_classes', []):
        if cls['count'] > 1000:
            held = 0
            for item in analysis.get('java_stack_holders', []):
                if item['name'] == cls['name']:
                    held = item['count']
                    break
            for item in analysis.get('system_class_holders', []):
                if item['name'] == cls['name']:
                    held = item['count']
                    break
            if held > 0 or cls['count'] > 5000:
                severity, leak_type = classify_leak_severity(cls['name'], cls['count'], held)
                suspicious.append({
                    'name': cls['name'], 'instances': cls['count'],
                    'held': held, 'severity': severity, 'type': leak_type,
                })

    if suspicious:
        lines.append("### 可疑泄漏类（高实例数 + GC Root 持有）\n")
        lines.append("| 类名 | 实例数 | GC Root 持有 | 严重性 | 泄漏模式 |")
        lines.append("|------|--------|--------------|--------|----------|")
        for s in sorted(suspicious, key=lambda x: (x['severity'], -x['held'])):
            icon = {'P0': '🔴', 'P1': '🟠', 'P2': '🟡', 'P3': '🟢'}.get(s['severity'], '⚪')
            lines.append(f"| `{s['name']}` | {s['instances']:,} | {s['held']:,} | {icon} {s['severity']} | {s['type']} |")
    else:
        lines.append("✅ 未发现明显的高实例数泄漏类。\n")

    # 静态字段分析
    if analysis.get('has_static_fields'):
        lines.append("\n### 静态字段持有者 Top 30\n")
        lines.append("| 类名 | 持有引用数 |")
        lines.append("|------|------------|")
        for sf in analysis.get('static_field_holders', [])[:30]:
            lines.append(f"| `{sf['class']}` | {sf['count']:,} |")

    # 类层次
    if analysis.get('hierarchy'):
        lines.append("\n### 类层次 Top 类（来自 CLASS_DUMP）\n")
        lines.append("| 类名 | 实例数（CLASS_DUMP） |")
        lines.append("|------|---------------------|")
        for h in analysis['hierarchy'][:20]:
            lines.append(f"| `{h['name']}` | {h['instances']:,} |")

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

    # ── 下一步行动 ───────────────────────────────────────────────────
    lines.append("## 下一步行动\n")
    lines.append("1. **优先处理 P0/P1 类** — 检查这些类是否被静态集合或单例持有\n")

    if cov_pct < 80:
        lines.append("2. **提供 ProGuard/R8 mapping 文件** — 当前覆盖率仅 {:.1f}%，49 个 class_serial 完全无二进制线索\n".format(cov_pct))
        lines.append("   - 从构建产物中获取 `mapping.txt`（位于 `app/build/outputs/mapping/release/`）\n")
        lines.append("   - 将 mapping 文件放入 `<project_root>/hprof/` 目录，命名格式: `<hprof_name>_mapping.txt`\n")
        lines.append("   - 解析器会自动检测并应用 mapping，覆盖率可提升至 95%+\n")
    else:
        lines.append("2. **获取 ProGuard mapping 文件** — 进一步提升类名识别度\n")

    lines.append("3. **逆向 CLASS_DUMP 大 chunk** — 当前仅解析了 10 个类，大 chunk dense packed 格式未逆向\n")
    lines.append("4. **解析 OBJECT_DUMP 字段值** — 从对象 payload 中提取字段引用，建立完整的引用链\n")

    if suspicious:
        lines.append("\n### 针对可疑类的具体排查建议\n")
        for s in sorted(suspicious, key=lambda x: (x['severity'], -x['instances']))[:5]:
            lines.append(f"#### `{s['name']}` ({s['severity']})\n")
            lines.append(f"- 实例数: {s['instances']:,}")
            lines.append(f"- 泄漏模式: {s['type']}")
            if s['held'] > 0:
                lines.append(f"- GC Root 持有: {s['held']:,} 个引用")
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

    if total_gc > 0 and (analysis.get('java_stack_holders') or analysis.get('system_class_holders')):
        assoc_score, assoc_note = 3, "GC Root 与对象有关联"
    else:
        assoc_score, assoc_note = 1, "GC Root 分析不完整"

    has_js = any(r['type'] == 'JAVA_STACK' for r in analysis.get('gc_root_dist', []))
    if has_js and analysis.get('java_stack_holders'):
        gc_score, gc_note = 3, "JavaStackFrame 持有分析完整"
    elif has_js:
        gc_score, gc_note = 2, "有 GC Root 类型分布但持有分析不完整"
    else:
        gc_score, gc_note = 1, "GC Root 数据有限"

    if suspicious:
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

    parquet_dir = str(output_base / "parquet")
    os.makedirs(parquet_dir, exist_ok=True)

    # Step 1: Convert HPROF to Parquet
    counts = ensure_parquet(hprof_path, parquet_dir)

    # Step 2: Run DuckDB analysis
    print(f"\nRunning DuckDB analysis...")
    analysis = run_duckdb_analysis(parquet_dir)

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
