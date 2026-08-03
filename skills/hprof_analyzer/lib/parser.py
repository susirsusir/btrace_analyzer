#!/usr/bin/env python3
"""hprof_to_parquet.parser — Complete hprof-libs parser with dense packed format support.

Handles Android hprof-libs files with multiple chunk formats:
- Marker-based tables (00 40 00 XX)
- Dense packed 89_6f format (5-byte records)
- Dense packed 89_14_cb format (6-byte records)
"""

import struct
import os
import json
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, Counter


class HPROFParser:
    """Parser for Android hprof-libs format with dense packed support."""

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
        self.static_field_refs = []  # class_serial → list of (obj_id, type_code)

    def _detect_record_start(self) -> int:
        """Detect record start from hprof header.

        hprof-libs (Android 7.0+): chunks start at fixed offset 0x80
        hprof-heap (standard): records start at 16 + stated_size
        """
        with open(self.filepath, 'rb') as f:
            f.read(16)
            stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        if stated_size > 2000:
            # hprof-libs format: chunks start at fixed offset 0x80
            return 0x80
        else:
            # standard hprof-heap format
            return 16 + stated_size

    def scan_chunks(self) -> List[Dict]:
        """Scan all chunks, reading payload into memory.

        Supports both marker-based format (0x00 0x40 + type + length)
        and dense packed format (89_6f, 89_14_cb).
        """
        with open(self.filepath, 'rb') as f:
            f.seek(self.record_start)
            data = f.read()

        chunks = []
        pos = 0
        while pos < len(data) - 4:
            # Check for marker-based format: 0x00 0x40 + type(1B) + length(2B)
            if data[pos] == 0x00 and data[pos+1] == 0x40:
                chunk_type = data[pos+2]
                chunk_len = struct.unpack_from('<H', data, pos+3)[0]

                # Validate chunk length
                if chunk_len > 0 and pos + 5 + chunk_len + 4 <= len(data):
                    chunks.append({
                        'tag': chunk_type,
                        'abs_pos': self.record_start + pos,
                        'offset': pos,
                        'length': chunk_len,
                        'payload': data[pos+5:pos+5+chunk_len]
                    })
                    pos += 5 + chunk_len + 4  # marker(2) + type(1) + len(2) + payload + id(4)
                else:
                    pos += 1
            else:
                pos += 1

        self.chunks = chunks
        return chunks

    # =========================================================================
    # Dense packed format detection (from Path B)
    # =========================================================================

    def detect_dense_packed_format(self, payload: bytes) -> Optional[Tuple[str, int, bytes]]:
        """Detect dense packed format type in large chunks."""
        if len(payload) < 100:
            return None

        scan_limit = max(512, len(payload) // 2)
        count_896f = sum(1 for i in range(scan_limit - 1) if payload[i:i+2] == b'\x89\x6f')
        count_8914cb = sum(1 for i in range(scan_limit - 2) if payload[i:i+3] == b'\x89\x14\xcb')
        count_004000 = sum(1 for i in range(scan_limit - 2) if payload[i:i+3] == b'\x00\x40\x00')

        if count_896f > scan_limit // 20:
            return ('89_6f', 7, b'\x89\x6f')
        elif count_8914cb > scan_limit // 20:
            return ('89_14_cb', 6, b'\x89\x14\xcb')
        elif count_004000 > scan_limit // 20:
            return ('marker_004000', None, b'\x00\x40\x00')
        else:
            return None

    def find_sync_point(self, payload: bytes, validator, step: int = 4, scan_limit: int = None) -> Optional[int]:
        """Find first valid entry sync point in large chunk payload."""
        if scan_limit is None:
            scan_limit = max(4096, len(payload) // 4)
        scan_limit = min(scan_limit, len(payload))

        p = 0
        while p + step <= scan_limit:
            if validator(p):
                return p
            p += step
        return None

    # =========================================================================
    # Helper: Parse marker-based entries
    # =========================================================================

    def _parse_marker_based_entries(self, payload: bytes) -> List[Dict]:
        """Parse marker-based entries from payload.

        Returns list of {obj_id, class_serial, type_code} dicts.
        Entry data is located AFTER each marker (00 40 00).
        """
        entries = []
        markers = []
        p = 0
        while True:
            pos = payload.find(b'\x00\x40\x00', p)
            if pos == -1:
                break
            markers.append(pos)
            p = pos + 1

        for i, marker_pos in enumerate(markers):
            entry_start = marker_pos + 4
            entry_end = markers[i+1] if i+1 < len(markers) else len(payload)
            entry_data = payload[entry_start:entry_end]

            # Safety check
            if marker_pos + 3 >= len(payload):
                continue

            class_serial = payload[marker_pos + 3]

            if len(entry_data) >= 5:
                obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                type_code = entry_data[4]

                if obj_id > 0 and obj_id < 0x10000000:  # Filter invalid IDs
                    entries.append({
                        'obj_id': obj_id,
                        'class_serial': class_serial,
                        'type_code': type_code,
                    })

        return entries

    # =========================================================================
    # Core parsers (enhanced with Path B logic)
    # =========================================================================

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
        """Extract class information from CLASS_DUMP chunks.

        Supports three formats:
        1. 89_6f dense packed: 5-byte records [serial(1B)] [count(1B)] [type(1B)] [0x89][0x6F]
        2. 89_14_cb dense packed: 6-byte records [serial(1B)] [count(1B)] [type(1B)] [0x89][0x14][0xCB]
        3. Marker-based: 00 40 00 XX entries
        """
        count = 0
        stats = {'format_89_6f': 0, 'format_89_14_cb': 0, 'format_marker': 0}

        VALID_TYPE_CODES = (
            0x02, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x10, 0x18,
            0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58,
            0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98,
            0xA0, 0xA8, 0xB0, 0xB8, 0xC0, 0xC8, 0xD0, 0xD8,
            0xE0, 0xE8, 0xF0, 0xF8, 0xFF,
        )

        for c in self.chunks:
            if c['tag'] != 0x0001:
                continue

            payload = c['payload']
            fmt = self.detect_dense_packed_format(payload)
            count_0040 = sum(1 for i in range(len(payload)-2) if payload[i:i+3] == b'\x00\x40\x00')

            # Format B: 89_6f dense packed (large chunks)
            if fmt and fmt[0] == '89_6f' and c['length'] > 1000:
                stats['format_89_6f'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+5] == b'\x89\x6f')
                p = sync if sync is not None else 0
                while p + 5 <= len(payload):
                    if payload[p+3:p+5] == b'\x89\x6f':
                        class_serial = payload[p]
                        instance_count = payload[p+1]
                        type_code = payload[p+2]
                        if type_code in VALID_TYPE_CODES and instance_count != 0xFF:
                            self.class_map[class_serial] = {
                                'serial': class_serial,
                                'num_instances': instance_count,
                                'type_code': type_code,
                            }
                            count += 1
                        p += 5
                    else:
                        p += 1

            # Format C: 89_14_cb dense packed (large chunks)
            elif fmt and fmt[0] == '89_14_cb' and c['length'] > 1000:
                stats['format_89_14_cb'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+6] == b'\x89\x14\xcb')
                p = sync if sync is not None else 0
                while p + 6 <= len(payload):
                    if payload[p+3:p+6] == b'\x89\x14\xcb':
                        class_serial = payload[p]
                        instance_count = payload[p+1]
                        type_code = payload[p+2]
                        if instance_count != 0xFF:
                            self.class_map[class_serial] = {
                                'serial': class_serial,
                                'num_instances': instance_count,
                                'type_code': type_code,
                            }
                            count += 1
                        p += 6
                    else:
                        p += 1

            # Format A: Marker-based (small chunks or fallback)
            elif count_0040 > len(payload) // 20 or c['length'] <= 1000:
                stats['format_marker'] += 1
                first_marker = payload.find(b'\x00\x40\x00')
                if first_marker == -1:
                    continue

                p = first_marker + 4
                while p < len(payload) - 4:
                    next_marker = payload.find(b'\x00\x40\x00', p)
                    if next_marker == -1 or next_marker + 4 >= len(payload):
                        break

                    entry_data = payload[p:next_marker]
                    class_serial = payload[next_marker + 3]

                    if len(entry_data) >= 2 and entry_data[1] != 0xFF:
                        self.class_map[class_serial] = {
                            'serial': class_serial,
                            'num_instances': entry_data[1],
                        }
                        count += 1

                    p = next_marker + 4

        return count

    def parse_objects(self) -> int:
        """Extract object instances from OBJECT_DUMP and marker-based chunks.

        Supports:
        1. OBJECT_DUMP (0x0004) with 89_6f or marker-based format
        2. Unknown marker-based tags: 0x6F00, 0x1500, 0xE56F, 0x1400, 0x0100, 0x000A, etc.
        """
        count = 0
        stats = {'format_89_6f': 0, 'format_marker': 0, 'unknown_tags': 0}

        # 1. Parse OBJECT_DUMP (0x0004) chunks
        for c in self.chunks:
            if c['tag'] != 0x0004:
                continue

            payload = c['payload']
            fmt = self.detect_dense_packed_format(payload)

            # Format B: 89_6f dense packed (large chunks)
            if fmt and fmt[0] == '89_6f' and c['length'] > 1000:
                stats['format_89_6f'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+5] == b'\x89\x6f')
                p = sync if sync is not None else 0
                while p + 7 <= len(payload):
                    if payload[p+3:p+5] == b'\x89\x6f':
                        obj_id = struct.unpack_from('<I', payload, p)[0]
                        type_code = payload[p+2]
                        if obj_id > 0:
                            self.object_list.append({
                                'obj_id': obj_id,
                                'class_serial': type_code,
                            })
                            count += 1
                        p += 7
                    else:
                        next_pos = payload.find(b'\x89\x6f', p + 1)
                        if next_pos == -1:
                            break
                        candidate = next_pos - 3
                        if candidate >= 0 and candidate % 7 == 0:
                            p = candidate
                        else:
                            p = next_pos - 3 if next_pos >= 3 else p + 1

            # Format A: Marker-based
            else:
                stats['format_marker'] += 1
                entries = self._parse_marker_based_entries(payload)
                for entry in entries:
                    self.object_list.append({
                        'obj_id': entry['obj_id'],
                        'class_serial': entry['class_serial'],
                    })
                    count += 1

        # 2. Parse unknown marker-based tags
        # 已知的 marker-based tag
        known_marker_tags = {0x6F00, 0x1500, 0xE56F, 0x1400, 0x0100, 0x000A, 
                             0x1521, 0x7002, 0x2300, 0xFFFF, 0x2200, 0x023E, 
                             0x728D, 0x3F08, 0x0200, 0xEA6F, 0x0C00, 0x1510,
                             0xFF0A, 0x1470, 0xEF6F, 0x708D, 0x7200, 0x8D3F,
                             0x2170, 0x4100, 0x8613, 0xDF6F, 0xE070, 0xE07E,
                             0xE089, 0xE0B2, 0xE76F, 0xE8CF, 0xE96F, 0xEE6F,
                             0xEF6F, 0xF148, 0xF1D8}
        
        # 动态检测其他 marker-based tag
        marker_tags = list(known_marker_tags)
        
        # 扫描所有 chunks，检测包含 0x004000 的未知 tag
        for c in self.chunks:
            tag = c['tag']
            if tag in known_marker_tags or tag in {0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 
                                                    0x0010, 0x0011, 0x0013, 0x0014, 0x0015, 0x0016, 0x0017, 0x0019,
                                                    0x0030, 0x0031, 0x0032, 0x3F3F, 0x3F00, 0x003F, 0x0040}:
                continue
            if b'\x00\x40\x00' in c['payload']:
                if tag not in marker_tags:
                    marker_tags.append(tag)

        for tag in marker_tags:
            tag_chunks = [c for c in self.chunks if c['tag'] == tag]

            for c in tag_chunks:
                entries = self._parse_marker_based_entries(c['payload'])
                for entry in entries:
                    self.object_list.append({
                        'obj_id': entry['obj_id'],
                        'class_serial': entry['class_serial'],
                    })
                    count += 1
                stats['unknown_tags'] += len(entries)

        return count

    def parse_gc_roots(self) -> int:
        """Extract GC root information from SAMPLE_GC_HEAP chunks (tag 0x0005).

        Supports two formats:
        1. Marker-based (00 40 00 XX): small chunks with 5-byte entries
           Entry: obj_id(2B LE) + root_kind(1B) + root_info(2B) + [optional extra]
        2. Dense 9-byte entries: large chunks without 00 40 00 markers
           Entry: class_serial(1B) + obj_id(4B LE) + root_kind(2B LE) + marker(1B) + extra(1B)
        """
        count = 0
        kind_names = {
            0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
            3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
            6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
            9: 'DAEMON_WORKER', 10: 'UNKNOWN',
        }

        def _kind_str(rk):
            return kind_names.get(rk, 'UNKNOWN') if 0 <= rk <= 10 else 'UNKNOWN'

        for c in self.chunks:
            if c['tag'] != 0x0005:
                continue

            payload = c['payload']
            has_markers = b'\x00\x40\x00' in payload

            # Strategy 1: Marker-based (00 40 00 XX) for small chunks
            if has_markers:
                markers = []
                p = 0
                while True:
                    pos = payload.find(b'\x00\x40\x00', p)
                    if pos == -1:
                        break
                    markers.append(pos)
                    p = pos + 1

                for i, marker_pos in enumerate(markers):
                    if marker_pos + 3 >= len(payload):
                        continue
                    class_serial = payload[marker_pos + 3]

                    entry_start = marker_pos + 4
                    entry_end = markers[i+1] if i+1 < len(markers) else len(payload)
                    entry_data = payload[entry_start:entry_end]

                    if len(entry_data) >= 5:
                        obj_id = struct.unpack_from('<H', entry_data, 0)[0]
                        root_kind = entry_data[2]
                        root_info = struct.unpack_from('<H', entry_data, 3)[0] if len(entry_data) >= 5 else 0

                        if obj_id > 0:
                            self.gc_roots.append({
                                'kind': _kind_str(root_kind),
                                'root_info': root_info,
                                'object_id': obj_id,
                                'class_serial': class_serial,
                            })
                            count += 1

            # Strategy 2: Dense 9-byte entries for large chunks without markers
            elif c['length'] > 1000:
                p = 0
                while p + 9 <= len(payload):
                    class_serial = payload[p]
                    obj_id = struct.unpack_from('<I', payload, p + 1)[0]
                    root_kind = struct.unpack_from('<H', payload, p + 5)[0]
                    marker = payload[p + 7]
                    extra = payload[p + 8]

                    if marker in (0x40, 0x41) and root_kind <= 10 and obj_id > 0:
                        self.gc_roots.append({
                            'kind': _kind_str(root_kind),
                            'root_info': extra,
                            'object_id': obj_id,
                            'class_serial': class_serial,
                        })
                        count += 1
                        p += 9
                    else:
                        p += 1

            # Strategy 3: Fallback to 20-byte scan
            else:
                p = 0
                while p + 20 <= len(payload):
                    object_id = struct.unpack_from('<I', payload, p)[0]
                    root_info = struct.unpack_from('<I', payload, p+4)[0]
                    root_kind = struct.unpack_from('<H', payload, p+8)[0]

                    if root_kind <= 10 and object_id > 0:
                        self.gc_roots.append({
                            'kind': _kind_str(root_kind),
                            'root_info': root_info,
                            'object_id': object_id,
                            'class_serial': 0,
                        })
                        count += 1
                        p += 20
                    else:
                        p += 4

        return count

    def parse_threads(self) -> int:
        """Extract thread information from THREAD_SUSPEND chunks.

        Supports three formats:
        1. Small chunks with 0x004000 markers (marker-based format)
        2. Large chunks with 0x6FE55848 pattern (fixed records)
        3. Large chunks with variable-length records (flag=0x00000000 padding)
        """
        count = 0

        for c in self.chunks:
            if c['tag'] != 0x0003:
                continue

            payload = c['payload']

            # Strategy 1: Parse 0x004000 marker-based format (small chunks)
            if b'\x00\x40\x00' in payload:
                markers = []
                p = 0
                while True:
                    pos = payload.find(b'\x00\x40\x00', p)
                    if pos == -1:
                        break
                    markers.append(pos)
                    p = pos + 1

                for i, marker_pos in enumerate(markers):
                    entry_start = marker_pos + 4
                    entry_end = markers[i+1] if i+1 < len(markers) else len(payload)
                    entry_data = payload[entry_start:entry_end]

                    if marker_pos + 3 >= len(payload):
                        continue

                    class_serial = payload[marker_pos + 3]

                    if len(entry_data) >= 4:
                        tid = struct.unpack_from('<I', entry_data, 0)[0]
                        if tid > 0 and tid < 0x10000000:
                            self.threads[tid] = {
                                'name': '',
                                'class_serial': class_serial,
                            }
                            count += 1

            # Strategy 2: Parse large chunk with 0x6FE55848 pattern
            if b'\x6f\xe5\x58\x48' in payload:
                pattern_positions = []
                p = 0
                while True:
                    pos = payload.find(b'\x6f\xe5\x58\x48', p)
                    if pos == -1:
                        break
                    pattern_positions.append(pos)
                    p = pos + 1

                if len(pattern_positions) > 1:
                    # Analyze intervals to find record size
                    intervals = [pattern_positions[i+1] - pattern_positions[i]
                                 for i in range(min(50, len(pattern_positions)-1))]
                    from collections import Counter
                    interval_counts = Counter(intervals)
                    most_common_interval = interval_counts.most_common(1)[0][0]

                    # Parse all records using the most common interval
                    for pos in pattern_positions:
                        if pos + most_common_interval > len(payload):
                            continue
                        rec = payload[pos:pos + most_common_interval]

                        # Try to extract thread_serial from different offsets
                        # Format: [magic(4B)] [flag(4B)] [thread_serial(4B)] [class_serial(4B)] ...
                        if len(rec) >= 12:
                            flag = struct.unpack_from('<I', rec, 4)[0]
                            thread_serial = struct.unpack_from('<I', rec, 8)[0]

                            # flag=335544320 (0x14000000) indicates valid thread record
                            # flag=0 indicates padding/null record
                            if flag == 0x14000000 and thread_serial != 0:
                                if thread_serial not in self.threads:
                                    self.threads[thread_serial] = {
                                        'name': '',
                                        'class_serial': 0,
                                    }
                                    count += 1
                            elif flag == 0 and thread_serial != 0:
                                # Some records have flag=0 but still contain thread info
                                # Try reading class_serial at offset 8 as thread_serial
                                candidate = thread_serial
                                if candidate > 0 and candidate not in self.threads:
                                    self.threads[candidate] = {
                                        'name': '',
                                        'class_serial': 0,
                                    }
                                    count += 1

        # Resolve thread names from STRING_DUMP table
        # Thread names are typically short identifiers like "main", "Binder-1", etc.
        # Only accept strings that look like thread names (not random field/constant names)
        for tid, info in self.threads.items():
            if not info.get('name'):
                for shift in [14, 13, 0]:
                    sid = tid << shift
                    if sid in self.strings:
                        candidate = self.strings[sid]
                        # Only accept short names (< 50 chars) without underscores
                        # or common thread name patterns (main, binder, gc, etc.)
                        if candidate and len(candidate) < 50 and '_' not in candidate:
                            lower_cand = candidate.lower()
                            if any(kw in lower_cand for kw in ['main', 'binder', 'gc', 'thread', 'queue', 'handler', 'pool', 'worker', 'render', 'finalizer', 'watchdog', 'process']):
                                info['name'] = candidate
                                break

        return count

    def parse_frames(self) -> int:
        """Extract stack frame information from STACK_FRAME chunks (tag 0x0002).

        Supports:
        1. Pre-marker block with 5-byte records (frame_id + type_code)
        2. Marker-based entries: frame_id(4B LE) + type_code(1B) after 00 40 00 marker
        3. Dense 8-byte entries: class_serial(1B) + frame_id(4B LE) + type_code(2B LE) + marker(1B)
        """
        count = 0

        for c in self.chunks:
            if c['tag'] != 0x0002:
                continue

            payload = c['payload']
            first_marker = payload.find(b'\x00\x40\x00')

            # Strategy 1: Marker-based format
            if first_marker != -1:
                # Parse pre-marker block (5-byte records)
                pre = payload[:first_marker]
                p = 0
                while p + 5 <= len(pre):
                    fid = struct.unpack_from('<I', pre, p)[0]
                    tc = pre[p+4] if p + 5 <= len(pre) else 0

                    if fid > 0 and fid < 10000000:
                        self.frames[fid] = {
                            'class_serial': 0,
                            'method_index': 0,
                            'line': -1,
                            'type_code': tc,
                        }
                        count += 1
                    p += 5

                # Parse marker-based entries
                # In STACK_FRAME, entry_data after 00 40 00 class_serial is:
                # frame_id(4B LE) + type_code(1B)
                markers = []
                p = 0
                while True:
                    pos = payload.find(b'\x00\x40\x00', p)
                    if pos == -1:
                        break
                    markers.append(pos)
                    p = pos + 1

                for i, marker_pos in enumerate(markers):
                    if marker_pos + 3 >= len(payload):
                        continue
                    class_serial = payload[marker_pos + 3]

                    entry_start = marker_pos + 4
                    entry_end = markers[i+1] if i+1 < len(markers) else len(payload)
                    entry_data = payload[entry_start:entry_end]

                    if len(entry_data) >= 5:
                        fid = struct.unpack_from('<I', entry_data, 0)[0]
                        tc = entry_data[4]

                        if fid > 0 and fid < 10000000:
                            self.frames[fid] = {
                                'class_serial': class_serial,
                                'method_index': 0,
                                'line': -1,
                                'type_code': tc,
                            }
                            count += 1

            # Strategy 2: Dense 8-byte entries (for chunks without 00 40 00 markers)
            elif c['length'] > 1000:
                p = 0
                while p + 8 <= len(payload):
                    class_serial = payload[p]
                    fid = struct.unpack_from('<I', payload, p + 1)[0]
                    tc = struct.unpack_from('<H', payload, p + 5)[0] if p + 7 <= len(payload) else 0
                    marker = payload[p + 7] if p + 8 <= len(payload) else 0

                    if marker in (0x40, 0x41) and 0 < fid < 10000000:
                        self.frames[fid] = {
                            'class_serial': class_serial,
                            'method_index': 0,
                            'line': -1,
                            'type_code': tc,
                        }
                        count += 1
                        p += 8
                    else:
                        p += 1

        return count

    def parse_static_fields(self) -> int:
        """Extract static field references from 0x1400 and 0x6F00 chunks.

        These chunks contain class_serial -> object_id references representing
        static fields that hold references to other objects.

        Each entry format (marker-based):
          [marker 00 40 00 class_serial(1B)] [entry_data]
          entry_data[0:4] = obj_id (LE uint32)
          entry_data[4]   = type_code (field type)
        """
        count = 0
        source_tags = {0x1400, 0x6F00}

        for c in self.chunks:
            if c['tag'] not in source_tags:
                continue

            payload = c['payload']
            if b'\x00\x40\x00' not in payload:
                continue

            p = 0
            while True:
                pos = payload.find(b'\x00\x40\x00', p)
                if pos == -1:
                    break
                # Safety check: need at least 4 bytes after marker for class_serial
                if pos + 4 > len(payload):
                    break
                class_serial = payload[pos + 3]
                next_pos = payload.find(b'\x00\x40\x00', pos + 1)
                if next_pos == -1:
                    next_pos = len(payload)
                entry_data = payload[pos + 4:next_pos]

                if len(entry_data) >= 5 and 0 < class_serial < 256:
                    obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                    type_code = entry_data[4]

                    if obj_id > 0:
                        self.static_field_refs.append({
                            'class_serial': class_serial,
                            'obj_id': obj_id,
                            'type_code': type_code,
                        })
                        count += 1

                p = pos + 1

        return count

    # =========================================================================
    # Class name resolution (enhanced)
    # =========================================================================

    def build_class_name_map(self) -> Dict[int, str]:
        """Build class_serial to class_name mapping.

        Uses multiple strategies in priority order:
        1. CHUNK_HEADER (tag=0x0000) parsing — direct class_serial → class_name mapping
        2. String tags (0x4000, 0x2100, 0x1000) parsing — additional class names
        3. Serial-based lookup (serial << 14, serial << 13)
        4. Fallback to class_serial placeholder
        """
        class_name_map = {}

        # Strategy 1: Parse CHUNK_HEADER chunks for direct mapping
        for c in self.chunks:
            if c['tag'] != 0x0000:
                continue

            payload = c['payload']
            p = 0
            while p < len(payload) - 13:
                sep = payload.find(b'\x01', p)
                if sep == -1:
                    break

                text_bytes = payload[p:sep]
                if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                    meta = payload[sep+1:sep+13]
                    if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                        class_serial = meta[7]
                        text = text_bytes.decode('ascii', errors='replace')

                        # Accept class names: len > 6, contains package-like patterns
                        # Relaxed from len > 8 + requires '.' to also accept '_' and '$' patterns
                        if len(text) > 6 and (
                            '.' in text or '/' in text
                            or text.startswith('$Proxy') or text.startswith('$this$') or text.startswith('$class$')
                            # Removed: or '_' in text (was too permissive, picked up field names)
                        ):
                            if not text.startswith('$SwitchMap') and not text.startswith('$$'):
                                if class_serial not in class_name_map:
                                    class_name_map[class_serial] = text

                p = sep + 13

        # Strategy 2: Parse string tags (0x4000, 0x2100, 0x1000) for additional class names
        string_tags = [0x4000, 0x2100, 0x1000]
        for tag in string_tags:
            tag_chunks = [c for c in self.chunks if c['tag'] == tag]
            for c in tag_chunks:
                payload = c['payload']
                p = 0
                while p < len(payload) - 13:
                    sep = payload.find(b'\x01', p)
                    if sep == -1:
                        break

                    text_bytes = payload[p:sep]
                    if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                        meta = payload[sep+1:sep+13]
                        if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                            class_serial = meta[7]
                            text = text_bytes.decode('ascii', errors='replace')

                            # Relaxed filter: accept underscore-separated names
                            if len(text) > 6 and (
                                '.' in text or '/' in text
                                or text.startswith('$Proxy') or text.startswith('$this$') or text.startswith('$class$')
                                # Removed: or '_' in text (too permissive)
                            ):
                                if not text.startswith('$SwitchMap') and not text.startswith('$$'):
                                    if class_serial not in class_name_map:
                                        class_name_map[class_serial] = text

                    p = sep + 13

        # Strategy 3: Serial-based heuristic lookup for all unmapped serials
        # Collect all serials that appear anywhere (class_map + object_list)
        all_serials = set(self.class_map.keys()) | {
            o['class_serial'] for o in self.object_list
        }
        for serial in all_serials:
            if serial in class_name_map:
                continue

            # Try serial << 14
            sid = serial << 14
            if sid in self.strings:
                class_name_map[serial] = self.strings[sid]
                continue

            # Try serial << 13
            sid = serial << 13
            if sid in self.strings:
                class_name_map[serial] = self.strings[sid]
                continue

            # Try direct serial lookup
            if serial in self.strings:
                class_name_map[serial] = self.strings[serial]
                continue

            # Fallback to class_serial placeholder
            class_name_map[serial] = f'class_{serial}'

        return class_name_map

    # =========================================================================
    # Main entry point
    # =========================================================================

    def parse_all(self) -> Dict[str, Any]:
        """Parse all chunk types and return aggregated data."""
        self.scan_chunks()

        string_count = self.parse_strings()
        class_count = self.parse_classes()
        object_count = self.parse_objects()
        gc_count = self.parse_gc_roots()
        thread_count = self.parse_threads()
        frame_count = self.parse_frames()
        sf_count = self.parse_static_fields()

        # Build class name map
        class_name_map = self.build_class_name_map()

        return {
            'strings': self.strings,
            'classes': self.class_map,
            'objects': self.object_list,
            'gc_roots': self.gc_roots,
            'threads': self.threads,
            'frames': self.frames,
            'static_field_refs': self.static_field_refs,
            'class_name_map': class_name_map,
            'stats': {
                'chunks': len(self.chunks),
                'strings': string_count,
                'classes': class_count,
                'objects': object_count,
                'gc_roots': gc_count,
                'threads': thread_count,
                'frames': frame_count,
                'static_fields': sf_count,
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
