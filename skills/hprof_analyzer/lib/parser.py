#!/usr/bin/env python3
"""hprof_to_parquet.parser — Pure Python hprof-libs parser.

Parses Android HPROF heap dumps (hprof-libs format, introduced in Android 7.0+)
and extracts objects, strings, class info, GC roots, thread stacks, etc.

This implementation replaces the need for the HeapDumpStarDiver binary for
basic parsing tasks. For full Parquet conversion, use the writer module.
"""

import struct
import os
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict

# Chunk tags
TAG_STRING_DUMP = 0x0010
TAG_CLASS_DUMP = 0x0001
TAG_LOAD_DATA = 0x0011
TAG_OBJECT_DUMP = 0x0004
TAG_SAMPLE_GC_HEAP = 0x0005
TAG_THREAD_SUSPEND = 0x0003
TAG_STACK_FRAME = 0x0002
TAG_CHUNK_HEADER = 0x0000
TAG_CHAIN_INSTANCE = 0x0019

# Valid chunk tags to skip (fillers)
FILLER_TAGS = {0x0000, 0x3F3F}


class ChunkScanner:
    """Scans through hprof-libs file and yields valid chunks."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)

    def scan(self) -> List[Tuple[int, int, int]]:
        """
        Return list of (pos, tag, length) tuples for all valid chunks.
        Chunks start at fixed offset 0x80 in hprof-libs format.
        """
        chunks = []
        pos = 0x80  # Fixed chunk stream start in hprof-libs

        while pos < self.file_size - 4:
            with open(self.filepath, 'rb') as f:
                f.seek(pos)
                header = f.read(4)
                if len(header) < 4:
                    break
                tag, length = struct.unpack_from('<HH', header, 0)

            # Skip filler chunks
            if tag in FILLER_TAGS:
                pos += 4
                continue

            # Validate: must have enough remaining data
            remaining = self.file_size - pos
            if length < 4 or length > remaining:
                pos += 1  # Scan forward by 1 byte to resync
                continue

            chunks.append((pos, tag, length))
            pos += length

        return chunks


class HPROFParser:
    """Main parser for hprof-libs format extracts structured data."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.chunks = ChunkScanner(filepath).scan()
        self.string_map: Dict[int, str] = {}  # string_id -> text
        self.class_map: Dict[int, Dict] = {}   # class_serial -> class info
        self.object_list: List[Dict] = []      # parsed objects
        self.gc_roots: List[Dict] = []         # GC root entries
        self.threads: Dict[int, Dict] = {}     # thread_obj_id -> thread info
        self.frames: Dict[int, Dict] = {}      # frame_id -> frame info

    def parse_all(self) -> Dict[str, Any]:
        """Parse all chunks and return aggregated data."""
        self._parse_string_dumps()
        self._parse_class_dumps()
        self._parse_load_data()
        self._parse_object_dumps()
        self._parse_gc_heap_samples()
        self._parse_thread_suspended()
        self._parse_stack_frames()
        return {
            'strings': self.string_map,
            'classes': self.class_map,
            'objects': self.object_list,
            'gc_roots': self.gc_roots,
            'threads': self.threads,
            'frames': self.frames,
        }

    # -------------------------------------------------------------------------
    # STRING_DUMP (0x0010)
    # -------------------------------------------------------------------------
    def _parse_string_dumps(self):
        """Parse STRING_DUMP chunks to build string ID → text mapping."""
        for pos, tag, length in self.chunks:
            if tag != TAG_STRING_DUMP:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            p = 0
            while p < len(payload) - 13:
                sep = payload.find(b'\x01', p)
                if sep == -1:
                    break

                text_bytes = payload[p:sep]
                if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                    meta = payload[sep+1:sep+13]
                    if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                        try:
                            string_id = struct.unpack_from('<I', meta, 8)[0]
                            text = text_bytes.decode('utf-8')
                            self.string_map[string_id] = text
                        except (ValueError, UnicodeDecodeError):
                            pass

                p = sep + 13

    # -------------------------------------------------------------------------
    # CLASS_DUMP (0x0001) – Supports marker-based, 89_6F dense packed, 89_14_CB formats
    # -------------------------------------------------------------------------
    def _parse_class_dumps(self):
        """Parse CLASS_DUMP chunks. Handles multiple format variants."""
        for pos, tag, length in self.chunks:
            if tag != TAG_CLASS_DUMP:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            fmt, record_size, terminator = self._detect_class_format(payload)

            if fmt == 'marker_based':
                self._parse_class_marker_based(payload)
            elif fmt == 'dense_89_6f':
                self._parse_class_dense_89_6f(payload, record_size)
            elif fmt == 'dense_89_14_cb':
                self._parse_class_dense_89_14_cb(payload, record_size)
            else:
                # Try marker-based as fallback
                self._parse_class_marker_based(payload)

    def _detect_class_format(self, payload: bytes) -> Tuple[str, int, bytes]:
        """Detect which format the CLASS_DUMP chunk uses."""
        count_896f = sum(1 for i in range(len(payload) - 1) if payload[i:i+2] == b'\x89\x6f')
        count_8914cb = sum(1 for i in range(len(payload) - 2) if payload[i:i+3] == b'\x89\x14\xcb')
        count_0040 = sum(1 for i in range(len(payload) - 2) if payload[i:i+3] == b'\x00\x40\x00')

        total = max(len(payload), 1)
        pct_896f = count_896f / total
        pct_8914cb = count_8914cb / total
        pct_0040 = count_0040 / total

        if pct_896f > 0.05:
            return ('dense_89_6f', 5, b'\x89\x6f')
        elif pct_8914cb > 0.05:
            return ('dense_89_14_cb', 6, b'\x89\x14\xcb')
        elif count_0040 > len(payload) // 20:
            return ('marker_based', 0, b'\x00\x40\x00')
        else:
            return ('unknown', 0, b'')

    def _parse_class_marker_based(self, payload: bytes):
        """Parse marker-based CLASS_DUMP format (00 40 00 XX separators)."""
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            return

        p = first_marker + 4
        while p < len(payload) - 4:
            next_marker = payload.find(b'\x00\x40\x00', p)
            if next_marker == -1 or next_marker + 4 >= len(payload):
                break

            entry_data = payload[p:next_marker]
            if len(entry_data) >= 2:
                class_serial = payload[next_marker + 3]
                instance_count = entry_data[1] if len(entry_data) > 1 else 0
                if instance_count != 0xFF:
                    self.class_map[class_serial] = {
                        'serial': class_serial,
                        'num_instances': instance_count,
                    }

            p = next_marker + 4

    def _parse_class_dense_89_6f(self, payload: bytes, record_size: int):
        """Parse CLASS_DUMP with 89 6F terminator (5-byte records)."""
        sync = self._find_sync_point(
            payload,
            lambda p: payload[p+3:p+5] == b'\x89\x6f' if p + 5 <= len(payload) else False
        )
        p = sync if sync is not None else 0

        while p + record_size <= len(payload):
            if payload[p+3:p+5] == b'\x89\x6f':
                class_serial = payload[p]
                instance_count = payload[p+1]
                type_code = payload[p+2]

                if instance_count != 0xFF and type_code in self._valid_type_codes():
                    self.class_map[class_serial] = {
                        'serial': class_serial,
                        'num_instances': instance_count,
                        'type_code': type_code,
                    }
                p += record_size
            else:
                p += 1

    def _parse_class_dense_89_14_cb(self, payload: bytes, record_size: int):
        """Parse CLASS_DUMP with 89 14 CB terminator (6-byte records)."""
        sync = self._find_sync_point(
            payload,
            lambda p: payload[p+3:p+6] == b'\x89\x14\xcb' if p + 6 <= len(payload) else False
        )
        p = sync if sync is not None else 0

        while p + record_size <= len(payload):
            if payload[p+3:p+6] == b'\x89\x14\xcb':
                class_serial = payload[p]
                instance_count = payload[p+1]
                type_code = payload[p+2]

                if instance_count != 0xFF and type_code in self._valid_type_codes():
                    self.class_map[class_serial] = {
                        'serial': class_serial,
                        'num_instances': instance_count,
                        'type_code': type_code,
                    }
                p += record_size
            else:
                p += 1

    def _valid_type_codes(self) -> set:
        """Valid type codes for CLASS_DUMP entries."""
        return {
            0x02, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x10, 0x18,
            0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58,
            0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98,
            0xA0, 0xA8, 0xB0, 0xB8, 0xC0, 0xC8, 0xD0, 0xD8,
            0xE0, 0xE8, 0xF0, 0xF8, 0xFF,
        }

    def _find_sync_point(self, payload: bytes, validator, step=4, scan_limit=None):
        """Find a sync point in payload where validator(p) returns True."""
        if scan_limit is None:
            scan_limit = max(4096, len(payload) // 4)
        scan_limit = min(scan_limit, len(payload))

        p = 0
        while p + step <= scan_limit:
            if validator(p):
                return p
            p += step
        return None

    # -------------------------------------------------------------------------
    # LOAD_DATA (0x0011)
    # -------------------------------------------------------------------------
    def _parse_load_data(self):
        """Parse LOAD_DATA chunks for field layout information."""
        for pos, tag, length in self.chunks:
            if tag != TAG_LOAD_DATA:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            if len(payload) < 8:
                continue

            class_serial = struct.unpack_from('<I', payload, 0)[0]
            object_id = struct.unpack_from('<I', payload, 4)[0]

            fields = []
            p = 8
            while p < len(payload) - 13:
                name_end = payload.find(b'\x01', p)
                if name_end == -1:
                    break

                field_name_bytes = payload[p:name_end]
                if len(field_name_bytes) > 0 and all(32 <= b < 127 for b in field_name_bytes):
                    meta = payload[name_end+1:name_end+13]
                    if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                        field_name = field_name_bytes.decode('utf-8')
                        fields.append({'name': field_name, 'offset': p})

                p = name_end + 13

            if fields:
                if class_serial not in self.class_map:
                    self.class_map[class_serial] = {}
                self.class_map[class_serial]['fields'] = fields

    # -------------------------------------------------------------------------
    # OBJECT_DUMP (0x0004)
    # -------------------------------------------------------------------------
    def _parse_object_dumps(self):
        """Parse OBJECT_DUMP chunks for object instances."""
        for pos, tag, length in self.chunks:
            if tag != TAG_OBJECT_DUMP:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            fmt, _, _ = self._detect_class_format(payload)  # Reuse detection logic

            if fmt in ('dense_89_6f', 'dense_89_14_cb'):
                # Handle dense-packed format (OBJECT_DUMP may also use these)
                self._parse_object_dense(payload, fmt)
            else:
                # Marker-based format (most common for OBJECT_DUMP)
                self._parse_object_marker_based(payload)

    def _parse_object_marker_based(self, payload: bytes):
        """Parse marker-based OBJECT_DUMP entries."""
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            return

        p = first_marker + 4
        while p < len(payload) - 4:
            next_marker = payload.find(b'\x00\x40\x00', p)
            if next_marker == -1 or next_marker + 4 >= len(payload):
                break

            entry_data = payload[p:next_marker]
            class_serial = payload[next_marker + 3]

            if len(entry_data) >= 4:
                obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                self.object_list.append({
                    'obj_id': obj_id,
                    'class_serial': class_serial,
                })

            p = next_marker + 4

    def _parse_object_dense(self, payload: bytes, fmt: str):
        """Parse dense-packed OBJECT_DUMP entries."""
        record_size = 7 if fmt == 'dense_89_6f' else 6
        terminator = b'\x89\x6f' if fmt == 'dense_89_6f' else b'\x89\x14\xcb'

        sync = self._find_sync_point(
            payload,
            lambda p: payload[p+len(terminator-2):p+len(terminator)] == terminator[-2:] if p + record_size <= len(payload) else False
        )
        p = sync if sync is not None else 0

        while p + record_size <= len(payload):
            if fmt == 'dense_89_6f' and payload[p+3:p+5] == terminator:
                obj_id = struct.unpack_from('<I', payload, p)[0]
                type_code = payload[p+2]
                if obj_id > 0:
                    self.object_list.append({
                        'obj_id': obj_id,
                        'class_serial': type_code,
                    })
                p += record_size
            elif fmt == 'dense_89_14_cb' and payload[p+3:p+6] == terminator:
                obj_id = struct.unpack_from('<I', payload, p)[0]
                type_code = payload[p+2]
                if obj_id > 0:
                    self.object_list.append({
                        'obj_id': obj_id,
                        'class_serial': type_code,
                    })
                p += record_size
            else:
                p += 1

    # -------------------------------------------------------------------------
    # SAMPLE_GC_HEAP (0x0005)
    # -------------------------------------------------------------------------
    def _parse_gc_heap_samples(self):
        """Parse SAMPLE_GC_HEAP chunks for GC root information."""
        kind_names = {
            0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
            3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
            6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
            9: 'DAEMON_WORKER', 10: 'UNKNOWN',
        }

        for pos, tag, length in self.chunks:
            if tag != TAG_SAMPLE_GC_HEAP:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            sync_pos = self._find_sync_point(
                payload,
                lambda p: struct.unpack_from('<I', payload, p)[0] > 0 and
                          struct.unpack_from('<H', payload, p + 8)[0] <= 10
            )

            p = sync_pos if sync_pos is not None else 0
            while p + 20 <= len(payload):
                object_id = struct.unpack_from('<I', payload, p)[0]
                root_info = struct.unpack_from('<I', payload, p+4)[0]
                root_kind = struct.unpack_from('<H', payload, p+8)[0]
                class_serial = struct.unpack_from('<I', payload, p+10)[0]

                if root_kind <= 10 and object_id > 0:
                    self.gc_roots.append({
                        'kind': kind_names.get(root_kind, f'0x{root_kind:04X}'),
                        'root_kind_raw': root_kind,
                        'root_info': root_info,
                        'object_id': object_id,
                        'class_serial': class_serial,
                    })
                    p += 20
                else:
                    p += 4

    # -------------------------------------------------------------------------
    # THREAD_SUSPEND (0x0003)
    # -------------------------------------------------------------------------
    def _parse_thread_suspended(self):
        """Parse THREAD_SUSPEND chunks for thread information."""
        for pos, tag, length in self.chunks:
            if tag != TAG_THREAD_SUSPEND:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            p = 0
            while p + 9 <= len(payload):
                thread_obj_id = struct.unpack_from('<I', payload, p)[0]
                b4 = payload[p+4]
                b5 = payload[p+5]
                b6 = payload[p+6]
                counter = payload[p+7]
                pad = struct.unpack_from('<H', payload, p+8)[0]

                if b4 == 0x0A and b5 == 0x7F and pad == 0x0040:
                    if thread_obj_id > 0:
                        self.threads[thread_obj_id] = {
                            'name': '',  # To be filled via STRING_LOOKUP
                            'suspend_type': b6,
                            'frame_ids': [],
                            'class_serial': counter,
                        }
                    p += 9
                else:
                    p += 1

    # -------------------------------------------------------------------------
    # STACK_FRAME (0x0002)
    # -------------------------------------------------------------------------
    def _parse_stack_frames(self):
        """Parse STACK_FRAME chunks for stack frame information."""
        for pos, tag, length in self.chunks:
            if tag != TAG_STACK_FRAME:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                return

            # Parse pre-marker block (initial frames)
            pre_data = payload[:first_marker]
            p = 0
            while p + 20 <= len(pre_data):
                frame_id = struct.unpack_from('<I', pre_data, p)[0]
                class_serial = struct.unpack_from('<I', pre_data, p+4)[0]
                if frame_id > 0:
                    self.frames[frame_id] = {
                        'class_serial': class_serial,
                        'class_name': self.string_map.get(class_serial, f'_{class_serial}'),
                        'method_index': 0,
                        'method_name': '<unknown>',
                        'line_number': 0,
                        'type_code': 0,
                    }
                p += 20

            # Parse marker-based entries
            p = first_marker + 4
            while p < len(payload) - 4:
                next_marker = payload.find(b'\x00\x40\x00', p)
                if next_marker == -1 or next_marker + 4 >= len(payload):
                    break

                entry_data = payload[p:next_marker]
                class_serial = payload[next_marker + 3]

                if len(entry_data) >= 5:
                    frame_id = struct.unpack_from('<I', entry_data, 0)[0]
                    type_code = entry_data[4]
                    if frame_id > 0:
                        self.frames[frame_id] = {
                            'class_serial': class_serial,
                            'class_name': self.string_map.get(class_serial, f'_{class_serial}'),
                            'type_code': type_code,
                        }

                p = next_marker + 4

    # -------------------------------------------------------------------------
    # CHUNK_HEADER / CHAIN_INSTANCE (0x0000, 0x0019) — class name mapping source
    # -------------------------------------------------------------------------
    def parse_chunk_header_classnames(self, class_name_map: Dict[int, str]) -> int:
        """
        Extract class names from CHUNK_HEADER/CHAIN_INSTANCE segments.
        Populates class_name_map with class_serial -> partial mappings.
        Returns number of mappings added.
        """
        count = 0
        for pos, tag, length in self.chunks:
            if tag not in (TAG_CHUNK_HEADER, TAG_CHAIN_INSTANCE):
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            # Look for class_serial pattern: single byte followed by string reference
            # Pattern: [class_serial(1B)][string_offset(4B)][\x01][7_zero][value][ref]
            p = 0
            while p + 5 < len(payload):
                class_serial = payload[p]
                if 28 <= class_serial <= 255 and class_serial not in class_name_map:
                    # Check if this looks like a class name entry
                    # The pattern often has a string after the serial
                    string_ref = struct.unpack_from('<I', payload, p+1)[0]
                    if string_ref > 1000000:  # Likely a string ID
                        # We can't resolve actual name without string table lookup
                        # Mark it as resolved via string later
                        class_name_map[class_serial] = f'class_{class_serial}'
                        count += 1
                p += 1

        return count


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parser.py <hprof_file>")
        sys.exit(1)

    parser = HPROFParser(sys.argv[1])
    data = parser.parse_all()

    print(f"Parsed {len(data['strings'])} strings")
    print(f"Found {len(data['classes'])} classes")
    print(f"Extracted {len(data['objects'])} objects")
    print(f"Found {len(data['gc_roots'])} GC roots")
    print(f"Detected {len(data['threads'])} threads")
    print(f"Identified {len(data['frames'])} frames")