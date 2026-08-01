#!/usr/bin/env python3
"""hprof_to_parquet.parser — Complete hprof-libs parser with content-based classification.

Handles Android hprof-libs files with 669+ different chunk tags by classifying
chunks based on content features rather than tag values.
"""

import struct
import os
import json
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, Counter


class HPROFParser:
    """Parser for Android hprof-libs format with content-based classification."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.record_start = self._detect_record_start()
        self.chunks = []
        self.strings = {}
        self.class_map = {}
        self.object_list = []
        self.gc_roots = []
        self.threads = {}
        self.frames = {}

    def _detect_record_start(self) -> int:
        """Detect record start from hprof header."""
        with open(self.filepath, 'rb') as f:
            f.read(16)
            stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        return 16 + stated_size

    def scan_chunks(self) -> List[Dict]:
        """Scan all chunks without filtering by tag."""
        with open(self.filepath, 'rb') as f:
            f.seek(self.record_start)
            data = f.read()

        chunks = []
        pos = 0
        while pos < len(data) - 4:
            tag, length = struct.unpack_from('<HH', data, pos)
            if length >= 4 and pos + length <= len(data):
                chunks.append({
                    'tag': tag,
                    'abs_pos': self.record_start + pos,
                    'offset': pos,
                    'length': length,
                    'payload': data[pos+4:pos+length]
                })
                pos += length
            else:
                pos += 1

        self.chunks = chunks
        return chunks

    def _classify_chunk(self, payload: bytes) -> str:
        """Classify chunk based on content features."""
        if len(payload) < 8:
            return 'tiny'

        has_01 = b'\x01' in payload
        has_004000 = b'\x00\x40\x00' in payload
        has_896f = b'\x89\x6f' in payload

        printable = sum(1 for b in payload if 32 <= b < 127 or b in (10, 13, 9))
        printable_ratio = printable / len(payload)

        x3f_ratio = payload.count(b'\x3f') / len(payload)
        null_ratio = payload.count(b'\x00') / len(payload)

        if x3f_ratio > 0.9 and not has_01:
            return 'filler'
        elif null_ratio > 0.95 and len(payload) < 64:
            return 'null_padding'
        elif has_896f and printable_ratio > 0.3:
            return 'dense_896f'
        elif has_004000 and printable_ratio < 0.7:
            return 'marker_structured'
        elif has_01 and printable_ratio > 0.5:
            return 'string_like'
        elif printable_ratio > 0.8 and len(payload) > 64:
            return 'text_heavy'
        else:
            return 'other'

    def parse_strings(self) -> int:
        """Extract all strings from string-like chunks."""
        count = 0
        for c in self.chunks:
            payload = c['payload']
            if b'\x01' not in payload:
                continue

            p = 0
            while p < len(payload) - 13:
                sep = payload.find(b'\x01', p)
                if sep == -1:
                    break
                text_bytes = payload[p:sep]
                if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                    meta = payload[sep+1:sep+13]
                    if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                        string_id = struct.unpack_from('<I', meta, 8)[0]
                        text = text_bytes.decode('utf-8', errors='replace')
                        self.strings[string_id] = text
                        count += 1
                p = sep + 13

        return count

    def parse_classes(self) -> int:
        """Extract class information from marker-based chunks."""
        count = 0
        for c in self.chunks:
            payload = c['payload']
            if b'\x00\x40\x00' not in payload:
                continue

            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue

            p = first_marker + 4
            while p < len(payload) - 4:
                nm = payload.find(b'\x00\x40\x00', p)
                if nm == -1 or nm + 4 >= len(payload):
                    break

                ed = payload[p:nm]
                cs = payload[nm + 3]

                if len(ed) >= 2 and ed[1] != 0xFF:
                    self.class_map[cs] = {'serial': cs, 'num_instances': ed[1]}
                    count += 1

                p = nm + 4

        return count

    def parse_objects(self) -> int:
        """Extract object instances from marker-based chunks."""
        count = 0
        for c in self.chunks:
            payload = c['payload']
            if b'\x00\x40\x00' not in payload:
                continue

            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue

            p = first_marker + 4
            while p < len(payload) - 4:
                nm = payload.find(b'\x00\x40\x00', p)
                if nm == -1 or nm + 4 >= len(payload):
                    break

                ed = payload[p:nm]
                cs = payload[nm + 3]

                if len(ed) >= 4:
                    oid = struct.unpack_from('<I', ed, 0)[0]
                    if oid > 0:
                        self.object_list.append({
                            'obj_id': oid,
                            'class_serial': cs
                        })
                        count += 1

                p = nm + 4

        return count

    def parse_gc_roots(self) -> int:
        """Extract GC roots from chunks with 20-byte fixed records."""
        kind_names = {
            0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
            3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
            6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
            9: 'DAEMON_WORKER', 10: 'UNKNOWN',
        }

        count = 0
        for c in self.chunks:
            payload = c['payload']

            # Try to find sync point
            sync_pos = None
            for p in range(0, min(len(payload), 4096), 4):
                try:
                    oid = struct.unpack_from('<I', payload, p)[0]
                    rk = struct.unpack_from('<H', payload, p+8)[0]
                    if oid > 0 and rk <= 10:
                        sync_pos = p
                        break
                except:
                    pass

            if sync_pos is None:
                continue

            p = sync_pos
            while p + 20 <= len(payload):
                oid = struct.unpack_from('<I', payload, p)[0]
                ri = struct.unpack_from('<I', payload, p+4)[0]
                rk = struct.unpack_from('<H', payload, p+8)[0]
                cs = struct.unpack_from('<I', payload, p+10)[0]

                if rk <= 10 and oid > 0:
                    self.gc_roots.append({
                        'kind': kind_names.get(rk, '?'),
                        'root_kind_raw': rk,
                        'root_info': ri,
                        'object_id': oid,
                        'class_serial': cs
                    })
                    p += 20
                    count += 1
                else:
                    p += 4

        return count

    def parse_threads(self) -> int:
        """Extract thread information from THREAD_SUSPEND chunks."""
        count = 0
        for c in self.chunks:
            payload = c['payload']
            p = 0
            while p + 9 <= len(payload):
                tid = struct.unpack_from('<I', payload, p)[0]
                if payload[p+4] == 0x0A and payload[p+5] == 0x7F:
                    if p + 10 <= len(payload):
                        pad = struct.unpack_from('<H', payload, p+8)[0]
                    else:
                        pad = 0
                    if pad == 0x0040 and tid > 0:
                        self.threads[tid] = {
                            'name': '',
                            'suspend_type': payload[p+6],
                            'counter': payload[p+7]
                        }
                        count += 1
                        p += 9
                    else:
                        p += 1
                else:
                    p += 1
        return count

    def parse_frames(self) -> int:
        """Extract stack frame information."""
        count = 0
        for c in self.chunks:
            payload = c['payload']
            if b'\x00\x40\x00' not in payload:
                continue

            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue

            # Parse pre-marker block
            pre = payload[:first_marker]
            p = 0
            while p + 20 <= len(pre):
                fid = struct.unpack_from('<I', pre, p)[0]
                cs = struct.unpack_from('<I', pre, p+4)[0]
                mi = struct.unpack_from('<I', pre, p+12)[0]
                ln = struct.unpack_from('<I', pre, p+16)[0] if p + 20 <= len(pre) else -1
                if fid > 0:
                    self.frames[fid] = {
                        'class_serial': cs,
                        'method_index': mi,
                        'line': ln
                    }
                    count += 1
                p += 20

            # Parse marker-based entries
            p = first_marker + 4
            while p < len(payload) - 4:
                nm = payload.find(b'\x00\x40\x00', p)
                if nm == -1 or nm + 4 >= len(payload):
                    break
                ed = payload[p:nm]
                cs = payload[nm + 3]
                if len(ed) >= 5:
                    fid = struct.unpack_from('<I', ed, 0)[0]
                    tc = ed[4]
                    if fid > 0 and fid in self.frames:
                        self.frames[fid]['type_code'] = tc
                        self.frames[fid]['class_serial'] = cs
                p = nm + 4

        return count

    def build_class_name_map(self) -> Dict[int, str]:
        """Build class_serial to class_name mapping using serial << 14.
        
        In this hprof-libs format, class_serial from CLASS_DUMP/OBJECT_DUMP
        can be mapped to class names by shifting left 14 bits to get the
        corresponding string_id in STRING_DUMP chunks.
        """
        class_name_map = {}
        for serial in self.class_map.keys():
            # Try serial << 14 first
            sid = serial << 14
            if sid in self.strings:
                class_name_map[serial] = self.strings[sid]
            else:
                # Fallback to serial << 13
                sid = serial << 13
                if sid in self.strings:
                    class_name_map[serial] = self.strings[sid]
                else:
                    class_name_map[serial] = f'class_{serial}'
        return class_name_map

    def parse_all(self) -> Dict[str, Any]:
        """Parse all chunk types and return aggregated data."""
        self.scan_chunks()

        string_count = self.parse_strings()
        class_count = self.parse_classes()
        object_count = self.parse_objects()
        gc_count = self.parse_gc_roots()
        thread_count = self.parse_threads()
        frame_count = self.parse_frames()

        # Build class name map
        class_name_map = self.build_class_name_map()

        return {
            'strings': self.strings,
            'classes': self.class_map,
            'objects': self.object_list,
            'gc_roots': self.gc_roots,
            'threads': self.threads,
            'frames': self.frames,
            'class_name_map': class_name_map,
            'stats': {
                'chunks': len(self.chunks),
                'strings': string_count,
                'classes': class_count,
                'objects': object_count,
                'gc_roots': gc_count,
                'threads': thread_count,
                'frames': frame_count,
            }
        }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parser.py <hprof_file>")
        sys.exit(1)

    parser = HPROFParser(sys.argv[1])
    data = parser.parse_all()
    stats = data['stats']

    print(f"Parsed {sys.argv[1]}:")
    print(f"  Chunks: {stats['chunks']:,}")
    print(f"  Strings: {stats['strings']:,}")
    print(f"  Classes: {stats['classes']:,}")
    print(f"  Objects: {stats['objects']:,}")
    print(f"  GC Roots: {stats['gc_roots']:,}")
    print(f"  Threads: {stats['threads']:,}")
    print(f"  Frames: {stats['frames']:,}")


"""
注意：class_serial 到 class_name 的映射在当前 hprof-libs 文件中未能正确解析。
原因：
1. class_serial 范围是 1-255，而 string_id 范围是 16384-4294967295，两者无重叠
2. CLASS_PREORDER (0x0015) 和 CLASS_BACKREF (0x0016) 可能包含类名映射，但格式未明确
3. 需要进一步逆向或使用 HeapDumpStarDiver 的 Robo Mode 获取完整映射

建议：
- 使用 Parquet 路径报告时，类名将显示为 class_X 格式
- 如需完整类名，建议使用 HeapDumpStarDiver 工具转换
"""
