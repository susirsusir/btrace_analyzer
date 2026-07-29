#!/usr/bin/env python3
"""hprof_to_parquet.writer — Writes parsed HPROF data to Apache Parquet files.

Uses PyArrow for efficient columnar storage and integrates with DuckDB
for downstream SQL querying. This matches the output schema of HeapDumpStarDiver.
"""

import pyarrow as pa
import pyarrow.parquet as pq
from typing import Dict, List, Optional
import os
from datetime import datetime
from collections import defaultdict  # <-- Missing import fixed


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
                'shallow_size': obj.get('shallow_size', 0),
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
        filename: str = "_class_hierarchy"
    ):
        """Write class hierarchy table (single file)."""
        rows = []
        for cls_id, cls_info in classes.items():
            rows.append({
                'class_id': cls_id,
                'class_name': cls_info.get('name', f'class_{cls_id}'),
                'super_class_id': cls_info.get('super_id', 0),
                'num_instances': cls_info.get('num_instances', 0),
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
                'root_id': hash((root.get('object_id', 0), root.get('root_info', 0))) % (10**9),
                'root_type': root_type,
                'root_type_raw': root.get('root_kind_raw', 0),
                'thread_id': root.get('root_info', 0),
                'object_id': root.get('object_id', 0),
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
        filename: str = "_thread_stacks"
    ):
        """Write thread stack table."""
        rows = []
        for thread_id, thread_info in threads.items():
            rows.append({
                'thread_id': thread_id,
                'thread_name': thread_info.get('name', ''),
                'suspend_type': thread_info.get('suspend_type', 0),
                'frame_ids': thread_info.get('frame_ids', []),
            })

        if rows:
            arr = pa.array([pa.array(r['frame_ids'], type=pa.int64()) for r in rows], type=pa.list_(pa.int64()))
            table = pa.Table.from_pylist({
                'thread_id': [r['thread_id'] for r in rows],
                'thread_name': [r['thread_name'] for r in rows],
                'suspend_type': [r['suspend_type'] for r in rows],
                'frame_ids': arr,
            })
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')

    def write_frames(
        self,
        frames: Dict[int, Dict],
        filename: str = "_stack_frames"
    ):
        """Write stack frames table (single file)."""
        rows = []
        for frame_id, frame_info in frames.items():
            rows.append({
                'frame_id': frame_id,
                'class_id': frame_info.get('class_serial', 0),
                'class_name': frame_info.get('class_name', ''),
                'method_id': frame_info.get('method_index', 0),
                'method_name': frame_info.get('method_name', ''),
                'line_number': frame_info.get('line_number', 0),
                'type_code': frame_info.get('type_code', 0),
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')

    def write_strings(
        self,
        strings: Dict[int, str],
        filename: str = "_java_strings"
    ):
        """Write Java strings table with buffer-encoded values."""
        rows = []
        for string_id, text in strings.items():
            rows.append({
                'string_id': string_id,
                'value': text.encode('utf-8'),
                'length': len(text),
            })

        if rows:
            table = pa.Table.from_pylist(rows)
            filepath = os.path.join(self.output_dir, f"{filename}.parquet")
            pq.write_table(table, filepath, compression='snappy')


def convert_hprof_to_parquet(
    hprof_path: str,
    output_dir: str,
    shard_size: int = 50000
) -> Dict[str, int]:
    """
    Main entry point: parse HPROF and write Parquet files.
    Returns dict of table counts for verification.
    """
    from .parser import HPROFParser

    # Parse
    parser = HPROFParser(hprof_path)
    data = parser.parse_all()

    # Build class name map (enhanced from CHUNK_HEADER parsing)
    class_name_map: Dict[int, str] = {}
    parser.parse_chunk_header_classnames(class_name_map)

    # Enhanced class names from string map
    for cls_id, cls_info in parser.class_map.items():
        if cls_id in class_name_map:
            continue
        for sid, txt in parser.string_map.items():
            if cls_id in str(sid) or txt.endswith(f'_{cls_id}') or f'_{cls_id}' in txt:
                class_name_map[cls_id] = txt
                break
        else:
            class_name_map[cls_id] = f'class_{cls_id}'

    # Initialize writer
    writer = ParquetWriter(output_dir, shard_size)

    # Write tables
    writer.write_objects(parser.object_list, class_name_map)
    writer.write_class_hierarchy(parser.class_map)
    writer.write_gc_roots(parser.gc_roots)
    writer.write_threads(parser.threads)
    writer.write_frames(parser.frames)
    writer.write_strings(parser.string_map)

    # Count records
    counts = {
        'objects': len(parser.object_list),
        'classes': len(parser.class_map),
        'gc_roots': len(parser.gc_roots),
        'threads': len(parser.threads),
        'frames': len(parser.frames),
        'strings': len(parser.string_map),
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
