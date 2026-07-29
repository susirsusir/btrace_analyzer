#!/usr/bin/env python3
"""Complete hprof-libs analyzer with all parsers and report generation.

Usage:
    python3 hprof_analyzer.py <hprof_file> [--output-dir hprof_analysis]

This script parses Android hprof-libs format files and generates binary path reports.
Supports both standard hprof-heap and Android hprof-libs (Android 7.0+) formats.
"""

import struct
import os
import sys
from collections import Counter, defaultdict

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


class HprofAnalyzer:
    """Main analyzer class for hprof-libs files."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.chunks = []
        self.string_table = {}
        self.classes = {}
        self.objects = []
        self.gc_roots = []
        self.threads = {}
        self.frames = {}
        self.field_layouts = {}
        self.class_name_map = {}  # class_serial -> class_name (from CHUNK_HEADER)

    def detect_format(self):
        """Detect hprof format based on magic and stated header size."""
        with open(self.filepath, 'rb') as f:
            magic = f.read(16)
            stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        return magic, stated_size

    def scan_chunks(self):
        """Scan all valid chunks from the file."""
        valid_tags = {
            0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005,
            0x0010, 0x0011, 0x0013, 0x0014, 0x0015,
            0x0016, 0x0017, 0x0019, 0x0030, 0x0031, 0x0032,
        }

        pos = 0x80
        while pos < self.file_size - 4:
            with open(self.filepath, 'rb') as f:
                f.seek(pos)
                header = f.read(4)
                if len(header) < 4:
                    break
                tag, length = struct.unpack_from('<HH', header, 0)

            if tag in valid_tags and 4 <= length <= self.file_size - pos:
                self.chunks.append((pos, tag, length))
                pos += length
            else:
                pos += 1

        return len(self.chunks)

    def parse_string_dumps(self):
        """Parse STRING_DUMP chunks to build string table."""
        strings = {}

        for pos, tag, length in self.chunks:
            if tag != 0x0010:
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

                # Validate printable ASCII
                if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                    meta = payload[sep+1:sep+13]
                    if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                        string_id = struct.unpack_from('<I', meta, 8)[0]
                        text = text_bytes.decode('utf-8')
                        strings[string_id] = text

                p = sep + 13

        self.string_table = strings
        return len(strings)

    def parse_chunk_header_class_names(self):
        """Parse CHUNK_HEADER chunks (tag=0x0000) to extract class_serial -> class_name mapping.
        
        hprof-libs format stores class name metadata in CHUNK_HEADER chunks.
        Each entry has format:
          [text] + 0x01 + [7_zero_bytes] + [class_serial(1B)] + [string_id(4B)]
        
        Returns: dict mapping class_serial (int) -> class_name (str)
        """
        class_map = {}
        total_entries = 0
        class_like_count = 0
        
        for pos, tag, length in self.chunks:
            if tag != 0x0000 or length <= 1000:
                continue
            
            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)
            
            # Parse all string entries in this chunk
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
                        string_id = struct.unpack_from('<I', meta, 8)[0]
                        text = text_bytes.decode('ascii')
                        total_entries += 1
                        
                        # Only keep actual class names (contain '/' or '.' or '$' and length > 8)
                        # Filter out field names, local variables, and non-class strings
                        # Common patterns: "com.xmhaibao...", "android....", "$Proxy1", etc.
                        if len(text) > 8 and ('.' in text or '/' in text or text.startswith('$Proxy') or text.startswith('$this$') or text.startswith('$class$')):
                            if not text.startswith('$SwitchMap') and not text.startswith('$$'):
                                if class_serial not in class_map:
                                    class_map[class_serial] = text
                                class_like_count += 1
                
                p = sep + 13
        
        self.class_name_map = class_map
        print(f"  Parsed {total_entries} entries from CHUNK_HEADER, {class_like_count} class-like")
        print(f"  Mapped {len(class_map)} unique class_serials to class names")
        return len(class_map)

    def detect_dense_packed_format(self, payload):
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
            return ('unknown', None, None)

    def find_sync_point(self, payload, validator, step=4, scan_limit=None):
        """Find sync point in payload for large chunk parsing."""
        if scan_limit is None:
            scan_limit = max(4096, len(payload) // 4)
        scan_limit = min(scan_limit, len(payload))

        p = 0
        while p + step <= scan_limit:
            if validator(p):
                return p
            p += step
        return None

    def parse_class_dumps(self):
        """Parse CLASS_DUMP chunks to extract class metadata."""
        classes = {}
        stats = {'format_b': 0, 'format_c': 0, 'format_a': 0}

        VALID_TYPE_CODES = (
            0x02, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x10, 0x18,
            0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58,
            0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98,
            0xA0, 0xA8, 0xB0, 0xB8, 0xC0, 0xC8, 0xD0, 0xD8,
            0xE0, 0xE8, 0xF0, 0xF8, 0xFF,
        )

        for pos, tag, length in self.chunks:
            if tag != 0x0001:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            fmt = self.detect_dense_packed_format(payload)
            count_0040 = sum(1 for i in range(len(payload)-2) if payload[i:i+3] == b'\x00\x40\x00')

            if fmt and fmt[0] == '89_6f' and length > 1000:
                stats['format_b'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+5] == b'\x89\x6f')
                p = sync if sync is not None else 0
                parsed = 0
                while p + 5 <= len(payload):
                    class_serial = payload[p]
                    instance_count = payload[p+1]
                    type_code = payload[p+2]

                    if type_code in VALID_TYPE_CODES and instance_count != 0xFF:
                        classes[class_serial] = {
                            'serial': class_serial,
                            'num_instances': instance_count,
                            'type_code': type_code,
                        }
                        parsed += 1
                    p += 5

            elif fmt and fmt[0] == '89_14_cb' and length > 1000:
                stats['format_c'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+6] == b'\x89\x14\xcb')
                p = sync if sync is not None else 0
                parsed = 0
                while p + 6 <= len(payload):
                    if payload[p+3:p+6] == b'\x89\x14\xcb':
                        class_serial = payload[p]
                        instance_count = payload[p+1]
                        type_code = payload[p+2]
                        if instance_count != 0xFF:
                            classes[class_serial] = {
                                'serial': class_serial,
                                'num_instances': instance_count,
                                'type_code': type_code,
                            }
                            parsed += 1
                        p += 6
                    else:
                        p += 1

            elif count_0040 > len(payload) // 20 or length <= 1000:
                stats['format_a'] += 1
                first_marker = payload.find(b'\x00\x40\x00')
                if first_marker == -1:
                    continue

                p = first_marker + 4
                parsed = 0
                while p < len(payload) - 4:
                    next_marker = payload.find(b'\x00\x40\x00', p)
                    if next_marker == -1 or next_marker + 4 >= len(payload):
                        break

                    entry_data = payload[p:next_marker]
                    class_serial = payload[next_marker + 3]

                    if len(entry_data) >= 2:
                        instance_count = entry_data[1]

                        if instance_count != 0xFF:
                            classes[class_serial] = {
                                'serial': class_serial,
                                'num_instances': instance_count,
                            }
                            parsed += 1

                    p = next_marker + 4

        self.classes = classes
        return len(classes), stats

    def parse_object_dumps(self):
        """Parse OBJECT_DUMP chunks to extract object instances."""
        objects = []
        stats = {'format_b': 0, 'format_a': 0}

        for pos, tag, length in self.chunks:
            if tag != 0x0004:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            fmt = self.detect_dense_packed_format(payload)
            count_0040 = sum(1 for i in range(len(payload)-2) if payload[i:i+3] == b'\x00\x40\x00')

            if fmt and fmt[0] == '89_6f' and length > 1000:
                stats['format_b'] += 1
                sync = self.find_sync_point(payload, lambda p: payload[p+3:p+5] == b'\x89\x6f')
                p = sync if sync is not None else 0
                parsed = 0
                while p + 7 <= len(payload):
                    if payload[p+3:p+5] == b'\x89\x6f':
                        obj_id = struct.unpack_from('<I', payload, p)[0]
                        type_code = payload[p+2]

                        if obj_id > 0:
                            objects.append({
                                'object_id': obj_id,
                                'class_serial': type_code,
                                'payload': payload[p+7:p+11],
                            })
                            parsed += 1
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

            else:
                stats['format_a'] += 1
                first_marker = payload.find(b'\x00\x40\x00')
                if first_marker == -1:
                    continue

                p = first_marker + 4
                parsed = 0
                while p < len(payload) - 4:
                    next_marker = payload.find(b'\x00\x40\x00', p)
                    if next_marker == -1 or next_marker + 4 >= len(payload):
                        break

                    entry_data = payload[p:next_marker]
                    class_serial = payload[next_marker + 3]

                    if len(entry_data) >= 4:
                        obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                        objects.append({
                            'object_id': obj_id,
                            'class_serial': class_serial,
                            'payload': entry_data[4:],
                        })
                        parsed += 1

                    p = next_marker + 4

        self.objects = objects
        return len(objects), stats

    def parse_gc_heap_samples(self):
        """Parse SAMPLE_GC_HEAP chunks to extract GC roots."""
        gc_roots = []
        kind_names = {
            0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
            3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
            6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
            9: 'DAEMON_WORKER', 10: 'UNKNOWN',
        }
        stats = {'small': 0, 'large': 0, 'parsed': 0}

        for pos, tag, length in self.chunks:
            if tag != 0x0005:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            if length <= 200:
                stats['small'] += 1
            else:
                stats['large'] += 1

            sync_pos = self.find_sync_point(
                payload,
                lambda p: p + 10 <= len(payload) and
                          struct.unpack_from('<I', payload, p)[0] > 0 and
                          struct.unpack_from('<H', payload, p + 8)[0] <= 10,
                step=4,
            )

            if sync_pos is not None:
                p = sync_pos
                parsed = 0
                while p + 20 <= len(payload):
                    object_id = struct.unpack_from('<I', payload, p)[0]
                    root_info = struct.unpack_from('<I', payload, p+4)[0]
                    root_kind = struct.unpack_from('<H', payload, p+8)[0]
                    class_serial = struct.unpack_from('<I', payload, p+10)[0]

                    if root_kind <= 10 and object_id > 0:
                        gc_roots.append({
                            'kind': kind_names.get(root_kind, f'0x{root_kind:04X}'),
                            'root_kind_raw': root_kind,
                            'root_info': root_info,
                            'object_id': object_id,
                            'class_serial': class_serial,
                        })
                        parsed += 1
                        p += 20
                    else:
                        p += 4
                        resync = self.find_sync_point(
                            payload[p:],
                            lambda q: struct.unpack_from('<I', payload, p + q)[0] > 0 and
                                      struct.unpack_from('<H', payload, p + q + 8)[0] <= 10,
                            step=4,
                        )
                        if resync is not None:
                            p += resync
                        else:
                            break
                stats['parsed'] += parsed

        self.gc_roots = gc_roots
        return len(gc_roots), stats

    def parse_thread_suspended(self):
        """Parse THREAD_SUSPEND chunks to extract thread information."""
        threads = {}

        for pos, tag, length in self.chunks:
            if tag != 0x0003:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            # Format: [thread_obj_id(2B LE)] [0x0A] [0x7F] [suspend_type(1B)] [counter(1B)] [pad(2B)] [extra(1B)]
            p = 0
            parsed = 0
            while p + 9 <= len(payload):
                thread_obj_id = struct.unpack_from('<H', payload, p)[0]
                b2 = payload[p+2]
                b3 = payload[p+3]
                b4 = payload[p+4]  # suspend_type
                counter = payload[p+5]
                pad = struct.unpack_from('<H', payload, p+6)[0]

                if b2 == 0x0A and b3 == 0x7F:
                    if thread_obj_id > 0:
                        threads[thread_obj_id] = {
                            'name': '',
                            'suspend_type': b4,
                            'frame_ids': [],
                            'class_serial': counter,
                        }
                        parsed += 1
                    p += 9
                else:
                    p += 1

        self.threads = threads
        return len(threads)

    def parse_stack_frames(self):
        """Parse STACK_FRAME chunks to extract frame details.
        
        STACK_FRAME uses two formats in pre-marker block:
        1. 7-byte records: [frame_id(2B LE)] [class_serial(2B LE)] [type_code(1B)] [pad(2B)]
        2. Marker-based table: [entry_data(variable)] [00 40 00 class_serial(1B)]
           Entry: [frame_id(4B LE)] [type_code(1B)]
        """
        frames = {}

        for pos, tag, length in self.chunks:
            if tag != 0x0002:
                continue

            with open(self.filepath, 'rb') as f:
                f.seek(pos + 4)
                payload = f.read(length - 4)

            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue

            # Parse pre-marker block as 7-byte records
            pre_data = payload[:first_marker]
            p = 0
            while p + 7 <= len(pre_data):
                rec = pre_data[p:p+7]
                # Format: [frame_id(2B LE)] [class_serial(2B LE)] [type_code(1B)] [pad(2B)]
                frame_id = struct.unpack_from('<H', rec, 0)[0]
                class_serial = struct.unpack_from('<H', rec, 2)[0]
                type_code = rec[4]
                
                if frame_id > 0 and frame_id < 10000000 and class_serial > 0:
                    frames[frame_id] = {
                        'class_serial': class_serial,
                        'class_name': f'class_serial_{class_serial}',
                        'method_index': 0,
                        'method_name': '<unknown>',
                        'line_number': -1,
                        'type_code': type_code,
                    }
                p += 7

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
                        if frame_id in frames:
                            frames[frame_id]['type_code'] = type_code
                            frames[frame_id]['class_serial'] = class_serial
                        else:
                            frames[frame_id] = {
                                'class_serial': class_serial,
                                'class_name': f'class_serial_{class_serial}',
                                'method_index': 0,
                                'method_name': '<unknown>',
                                'line_number': -1,
                                'type_code': type_code,
                            }

                p = next_marker + 4

        self.frames = frames
        return len(frames)

    def build_reference_chains(self):
        """Build GC Root reference chains."""
        chains = []

        for root in self.gc_roots:
            chain = {
                'root_kind': root['kind'],
                'target_class': 'unknown',
                'target_instance_count': '?',
                'stack_trace': [],
                'decoded_context': None,
            }

            kind = root['root_kind_raw']
            root_info = root['root_info']

            if kind == 0:  # JAVA_STACK
                if root_info in self.threads:
                    thread = self.threads[root_info]
                    chain['decoded_context'] = {
                        'type': 'thread',
                        'name': thread['name'],
                        'obj_id': root_info,
                        'frame_ids': thread['frame_ids'],
                    }

            elif kind == 4:  # GC_LOCAL
                if root_info in self.frames:
                    chain['decoded_context'] = {
                        'type': 'local_var',
                        'frame': self.frames[root_info],
                    }

            elif kind == 6:  # GC_JAVA_FRAME
                if root_info in self.frames:
                    chain['decoded_context'] = {
                        'type': 'java_frame',
                        'frame': self.frames[root_info],
                    }

            obj_id = root['object_id']
            target_class = None
            for obj in self.objects:
                if obj['object_id'] == obj_id:
                    target_class = obj['class_serial']
                    break

            if target_class is not None:
                if target_class in self.classes:
                    cls = self.classes[target_class]
                    chain['target_class'] = f'class_serial_{cls["serial"]}'
                    chain['target_instance_count'] = cls['num_instances']
                else:
                    chain['target_class'] = f'class_serial_{target_class}'

            chains.append(chain)

        return chains

    def generate_binary_report(self, output_path):
        """Generate binary path report in Markdown format."""
        report = []
        report.append("# HPROF 内存分析报告（二进制路径）\n")
        report.append(f"**文件**: {self.filepath}\n")
        report.append(f"**大小**: {self.file_size / (1024*1024):.2f} MB\n")
        report.append("**格式**: hprof-libs (Android 7.0+)\n")
        report.append("**日期**: 2026-07-21\n")

        report.append("\n## 概要\n")
        report.append("本报告基于二进制直接解析方法生成。\n")

        report.append("**关键指标：**\n")
        report.append("| 指标 | 数值 |")
        report.append("|------|------|")
        report.append(f"| 总 Chunk 数 | {len(self.chunks)} |")
        report.append(f"| 类数量 | {len(self.classes)} |")
        report.append(f"| 对象数量 | {len(self.objects)} |")
        report.append(f"| GC Root 数量 | {len(self.gc_roots)} |")
        report.append(f"| 线程数量 | {len(self.threads)} |")
        report.append(f"| 栈帧数量 | {len(self.frames)} |")
        report.append(f"| 字符串表条目 | {len(self.string_table)} |")

        report.append("\n## 堆分布\n")
        report.append("### 类实例 Top 20\n")
        sorted_classes = sorted(self.classes.values(), key=lambda x: x['num_instances'], reverse=True)
        report.append("| 排名 | Class Serial | 实例数 | Type Code |")
        report.append("|------|--------------|--------|-----------|")
        for i, cls in enumerate(sorted_classes[:20], 1):
            tc = cls.get('type_code', '?')
            if isinstance(tc, int):
                tc_str = f"0x{tc:02X}"
            else:
                tc_str = str(tc)
            report.append(f"| {i} | {cls['serial']} | {cls['num_instances']} | {tc_str} |")

        report.append("\n## GC Root 分析\n")
        report.append("### Root 类型分布\n")
        kind_counter = Counter(root['kind'] for root in self.gc_roots)
        report.append("| Root 类型 | 数量 |")
        report.append("|-----------|------|")
        for kind, count in kind_counter.most_common():
            report.append(f"| {kind} | {count} |")

        report.append("\n## 质量评估（对照 quality-standards.md）\n")
        report.append("| 维度 | 得分 | 说明 |")
        report.append("|------|------|------|")
        report.append("| 类名可识别度 | 1/4 | 类名无法解析为 `serial_X`，缺少 ProGuard mapping |")
        report.append("| 对象实例关联 | 1/4 | OBJECT_DUMP 大 chunk 未完全解析 |")
        report.append("| GC Root 分析深度 | 1/4 | 仅小 chunk GC Root 可解析 |")
        report.append("| 泄漏诊断 actionable | 0/4 | 类名丢失导致无法生成有意义诊断 |")
        report.append("| 数据完整性 | 1/4 | 大 chunk 部分解析 |")
        report.append("| **总分** | **4/20** | **F — 不可用，需要修复大 chunk 解析器** |")

        with open(output_path, 'w') as f:
            f.write('\n'.join(report))

        return output_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Analyze hprof-libs files')
    parser.add_argument('filepath', help='Path to .hprof file')
    parser.add_argument('--output-dir', default='./hprof_analysis', help='Output directory')
    args = parser.parse_args()

    filepath = args.filepath
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    analyzer = HprofAnalyzer(filepath)

    print(f"=== Analyzing {filepath} ===")
    print(f"File size: {analyzer.file_size / (1024*1024):.2f} MB")

    magic, stated_size = analyzer.detect_format()
    print(f"Magic: {magic}")
    print(f"Stated header size: {stated_size}")

    if stated_size > 2000:
        print("Format: hprof-libs (Android 7.0+)")
    else:
        print("Format: hprof-heap (standard)")

    print("\nScanning chunks...")
    chunk_count = analyzer.scan_chunks()
    print(f"Found {chunk_count} chunks")

    print("\nParsing STRING_DUMP...")
    string_count = analyzer.parse_string_dumps()
    print(f"Parsed {string_count} strings")

    print("\nParsing CLASS_DUMP...")
    class_count, class_stats = analyzer.parse_class_dumps()
    print(f"Parsed {class_count} classes")
    print(f"Stats: {class_stats}")

    print("\nParsing OBJECT_DUMP...")
    object_count, obj_stats = analyzer.parse_object_dumps()
    print(f"Parsed {object_count} objects")
    print(f"Stats: {obj_stats}")

    print("\nParsing SAMPLE_GC_HEAP...")
    gc_count, gc_stats = analyzer.parse_gc_heap_samples()
    print(f"Parsed {gc_count} GC roots")
    print(f"Stats: {gc_stats}")

    print("\nParsing THREAD_SUSPEND...")
    thread_count = analyzer.parse_thread_suspended()
    print(f"Parsed {thread_count} threads")

    print("\nParsing STACK_FRAME...")
    frame_count = analyzer.parse_stack_frames()
    print(f"Parsed {frame_count} frames")

    print("\nParsing CHUNK_HEADER for class names...")
    class_name_count = analyzer.parse_chunk_header_class_names()
    print(f"Mapped {class_name_count} class_serials to class names")

    print("\nGenerating binary report...")
    filename = os.path.splitext(os.path.basename(filepath))[0]
    output_path = os.path.join(output_dir, f"{filename}_binary_report.md")
    analyzer.generate_binary_report(output_path)
    print(f"Report written to: {output_path}")

    print("\n=== Analysis Complete ===")


if __name__ == '__main__':
    main()
