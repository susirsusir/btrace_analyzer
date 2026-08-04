#!/usr/bin/env python3
"""hprof_to_parquet.writer — Writes parsed HPROF data to Apache Parquet files."""

import pyarrow as pa
import pyarrow.parquet as pq
from typing import Dict, List, Optional
import os
from datetime import datetime
from collections import defaultdict

def _estimate_shallow_size(class_name: str) -> int:
    """Estimate shallow size based on class name heuristics."""
    if not class_name or class_name.startswith('class_'):
        return 24
    if '[]' in class_name or class_name.endswith('[]'):
        return 40
    if '$' in class_name.split('.')[-1]:
        return 16
    if class_name.startswith('android.') or class_name.startswith('com.android.'):
        return 32
    if class_name.startswith('java.'):
        return 24
    if class_name.startswith('kotlin.'):
        return 16
    return 24



class ParquetWriter:
    """Writes HPROF-derived tables to Parquet files in chunks."""

    def __init__(self, output_dir: str, shard_size: int = 50000):
        self.output_dir = output_dir
        self.shard_size = shard_size
        os.makedirs(output_dir, exist_ok=True)

    def write_objects(
        self,
        objects: List[Dict],
        class_names: Dict[int, str],
        filename: str = "_object_index"
    ):
        """Write object index table with sharding."""
        rows = []
        shard_counter = 0
        current_shard = []

        for obj in objects:
            class_id = obj.get('class_serial', 0)
            class_name = class_names.get(class_id, f'class_{class_id}')

            row = {
                'obj_id': obj['obj_id'],
                'class_id': class_id,
                'class_name': class_name,
                'shallow_size': _estimate_shallow_size(class_names.get(class_id, f'class_{class_id}')),
            }
            current_shard.append(row)

            if len(current_shard) >= self.shard_size:
                self._write_shard(current_shard, f"{filename}_chunk{shard_counter}.parquet")
                current_shard = []
                shard_counter += 1

        if current_shard:
            self._write_shard(current_shard, f"{filename}_chunk{shard_counter}.parquet")

    def _write_shard(self, rows: List[Dict], filename: str):
        """Write a single Parquet shard."""
        table = pa.Table.from_pylist(rows)
        filepath = os.path.join(self.output_dir, filename)
        pq.write_table(table, filepath, compression='snappy')

    def write_class_hierarchy(
        self,
        classes: Dict[int, Dict],
        class_name_map: Dict[int, str],
        filename: str = "_class_hierarchy"
    ):
        """Write class hierarchy table (single file)."""
        rows = []
        for cls_id, cls_info in classes.items():
            instance_count = cls_info.get('num_instances', 0) if isinstance(cls_info, dict) else cls_info
            rows.append({
                'class_id': cls_id,
                'class_name': class_name_map.get(cls_id, f'class_{cls_id}'),
                'super_class_id': 0,
                'num_instances': instance_count,
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')

    def write_gc_roots(
        self,
        gc_roots: List[Dict],
        filename: str = "_gc_roots"
    ):
        """Write GC roots with sharding by root type."""
        shards = defaultdict(list)
        for root in gc_roots:
            root_type = root.get('kind', 'UNKNOWN')
            shards[root_type].append({
                'root_type': root_type,
                'obj_id': root.get('object_id', 0),
                'thread_serial': root.get('root_info', 0),
                'frame_index': 0,
            })

        for root_type, root_list in shards.items():
            shard_counter = 0
            for i in range(0, len(root_list), self.shard_size):
                shard = root_list[i:i+self.shard_size]
                table = pa.Table.from_pylist(shard)
                filename_part = root_type.replace(' ', '_').replace('/', '_')
                filepath = os.path.join(
                    self.output_dir,
                    f"{filename}_{filename_part}_chunk{shard_counter}.parquet"
                )
                pq.write_table(table, filepath, compression='snappy')
                shard_counter += 1

    def write_threads(
        self,
        threads: Dict[int, Dict],
        filename: str = "_threads"
    ):
        """Write thread table."""
        rows = []
        for thread_id, thread_info in threads.items():
            rows.append({
                'thread_serial': thread_id,
                'name': thread_info.get('name', ''),
                'suspend_type': thread_info.get('suspend_type', 0),
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')

    def write_frames(
        self,
        frames: Dict[int, Dict],
        class_name_map: Dict[int, str],
        filename: str = "_frames"
    ):
        """Write stack frames table."""
        rows = []
        for frame_id, frame_info in frames.items():
            serial = frame_info.get('class_serial', 0)
            rows.append({
                'frame_id': frame_id,
                'class_serial': serial,
                'class_name': class_name_map.get(serial, f'class_{serial}'),
                'method_index': frame_info.get('method_index', 0),
                'line_number': frame_info.get('line', -1),
                'type_code': frame_info.get('type_code', 0),
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')


    def write_static_fields(
        self,
        static_field_refs: List[Dict],
        class_name_map: Dict[int, str],
        filename: str = "_static_fields"
    ):
        """Write static field references table."""
        rows = []
        for ref in static_field_refs:
            class_serial = ref['class_serial']
            rows.append({
                'class_serial': class_serial,
                'class_name': class_name_map.get(class_serial, f'class_{class_serial}'),
                'obj_id': ref['obj_id'],
                'type_code': ref['type_code'],
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')

        return len(rows)


def write_standard_parser_data(parser, output_dir: str) -> dict:
    """Write StandardHprofParser output to Parquet files.

    Produces HeapDumpStarDiver-compatible schema:
    - Per-class files: ClassName_classobjid.parquet (with obj_id column)
    - _gc_roots.parquet
    - _static_fields.parquet
    - _object_arrays.parquet
    - _primitive_arrays_*.parquet
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    import os
    from collections import defaultdict

    os.makedirs(output_dir, exist_ok=True)

    # GC Root kind name mapping (to match HeapDumpStarDiver output)
    kind_map = {
        'UNKNOWN': 'Unknown', 'THREAD_OBJ': 'ThreadObj',
        'JNI_GLOBAL': 'JniGlobal', 'JNI_LOCAL': 'JniLocal',
        'JAVA_STACK': 'JavaStackFrame', 'NATIVE_STACK': 'NativeStack',
        'SYSTEM_CLASS': 'SystemClass', 'THREAD_BLOCK': 'ThreadBlock',
        'BUSY_MONITOR': 'BusyMonitor',
    }

    # 1. Write per-class files
    objects_by_class = defaultdict(list)
    class_obj_ids = {}
    for obj in parser.objects:
        cn = obj.get('class_name') or f'class_{obj.get("class_serial", 0)}'
        objects_by_class[cn].append(obj)
        if cn not in class_obj_ids:
            class_obj_ids[cn] = obj.get('class_obj_id', 0)

    class_count = 0
    for cn, objs in objects_by_class.items():
        rows = [{'obj_id': o['obj_id']} for o in objs]
        table = pa.Table.from_pylist(rows)
        coid = class_obj_ids.get(cn, 0)
        # Clean class name for filename
        safe_cn = cn.replace('/', '_').replace('$', '_')
        filename = f"{safe_cn}_{coid}.parquet"
        filepath = os.path.join(output_dir, filename)
        pq.write_table(table, filepath, compression='snappy')
        class_count += 1

    # 2. Write _gc_roots.parquet
    if parser.gc_roots:
        root_rows = []
        for r in parser.gc_roots:
            root_rows.append({
                'root_type': kind_map.get(r.get('kind', 'UNKNOWN'), 'Unknown'),
                'obj_id': r.get('object_id', 0),
                'thread_serial': 0,
                'frame_index': 0,
            })
        table = pa.Table.from_pylist(root_rows)
        pq.write_table(table, os.path.join(output_dir, '_gc_roots.parquet'), compression='snappy')

    # 3. Write _static_fields.parquet
    if parser.static_fields:
        sf_rows = []
        for sf in parser.static_fields:
            sf_rows.append({
                'class_obj_id': 0,
                'class_name': sf.get('class_name', ''),
                'field_name': sf.get('field_name', ''),
                'field_type': str(sf.get('field_type', 0)),
                'primitive_value': '',
                'ref_id': sf.get('ref_id', 0),
                'ref_type': '',
            })
        table = pa.Table.from_pylist(sf_rows)
        pq.write_table(table, os.path.join(output_dir, '_static_fields.parquet'), compression='snappy')

    # 4. Write _object_arrays.parquet
    if parser.object_arrays:
        oa_rows = []
        for oa in parser.object_arrays:
            oa_rows.append({
                'obj_id': oa.get('obj_id', 0),
                'class_name': oa.get('class_name', '[object'),
                'elements': oa.get('elements', []),
            })
        table = pa.Table.from_pylist(oa_rows)
        pq.write_table(table, os.path.join(output_dir, '_object_arrays.parquet'), compression='snappy')

    # 5. Write _primitive_arrays_*.parquet (group by type)
    type_suffix = {4: 'byte', 5: 'char', 6: 'float', 7: 'double', 8: 'byte', 9: 'short', 10: 'int', 11: 'long'}
    prim_by_type = defaultdict(list)
    for pa_item in parser.primitive_arrays:
        prim_by_type[pa_item['type']].append(pa_item)

    for ptype, items in prim_by_type.items():
        suffix = type_suffix.get(ptype, 'byte')
        rows = [{'obj_id': item['obj_id']} for item in items]
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, os.path.join(output_dir, f'_primitive_arrays_{suffix}.parquet'), compression='snappy')

    counts = {
        'objects': len(parser.objects),
        'classes': class_count,
        'gc_roots': len(parser.gc_roots),
        'static_fields': len(parser.static_fields),
        'object_arrays': len(parser.object_arrays),
        'primitive_arrays': len(parser.primitive_arrays),
        'strings': len(parser.strings),
        'object_refs': 0,
    }
    return counts


def convert_hprof_to_parquet(
    hprof_path: str,
    output_dir: str,
    shard_size: int = 50000,
    std_class_names: dict = None
) -> Dict[str, int]:
    """
    Main entry point: parse HPROF and write Parquet files.
    Returns dict of table counts for verification.
    """
    import glob
    # Clean old parquet files to prevent stale data mixing
    os.makedirs(output_dir, exist_ok=True)
    for old_file in glob.glob(os.path.join(output_dir, "*.parquet")):
        os.remove(old_file)

    from .parser import HPROFParser

    # Parse
    parser = HPROFParser(hprof_path)
    data = parser.parse_all()

    # Use parser's class name map (built from CHUNK_HEADER + string tags + heuristic lookup)
    class_name_map = parser.build_class_name_map()
    
    # P0: Override with standard HPROF class names if available
    if std_class_names:
        # Standard parser has 21K class names with full package paths
        # Override only when the standard name is better (has '.')
        for serial, name in std_class_names.items():
            old = class_name_map.get(serial, '')
            if '.' in name and (not old or '.' not in old or old.startswith('class_')):
                class_name_map[serial] = name

    # Initialize writer
    writer = ParquetWriter(output_dir, shard_size)

    # Write tables
    writer.write_objects(parser.object_list, class_name_map)
    writer.write_class_hierarchy(parser.class_map, class_name_map)
    writer.write_gc_roots(parser.gc_roots)
    writer.write_threads(parser.threads)
    writer.write_frames(parser.frames, class_name_map)
    writer.write_static_fields(parser.static_field_refs, class_name_map)

    # Write object references (field reference chain)
    if hasattr(parser, 'object_refs') and parser.object_refs:
        refs_table = []
        for ref in parser.object_refs:
            refs_table.append({
                'obj_id': ref['obj_id'],
                'class_serial': ref['class_serial'],
                'class_name': class_name_map.get(ref['class_serial'], 'class_' + str(ref['class_serial'])),
                'ref_obj_id': ref['ref_obj_id'],
                'ref_class_serial': ref['ref_class_serial'],
                'ref_class_name': class_name_map.get(ref['ref_class_serial'], 'class_' + str(ref['ref_class_serial'])),
            })
        if refs_table:
            import pyarrow as pa
            table = pa.Table.from_pylist(refs_table)
            filepath = os.path.join(output_dir, '_object_references.parquet')
            pq.write_table(table, filepath, compression='snappy')

    # Count records
    counts = {
        'objects': len(parser.object_list),
        'classes': len(parser.class_map),
        'gc_roots': len(parser.gc_roots),
        'threads': len(parser.threads),
        'frames': len(parser.frames),
        'strings': len(parser.strings),
        'object_refs': len(getattr(parser, 'object_refs', [])),
    }

    return counts


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python writer.py <hprof_file> <output_dir>")
        sys.exit(1)

    hprof_path = sys.argv[1]
    output_dir = sys.argv[2]
    shard_size = int(sys.argv[3]) if len(sys.argv) > 3 else 50000

    counts = convert_hprof_to_parquet(hprof_path, output_dir, shard_size)
    print(f"Conversion complete. Object count: {counts['objects']}")
