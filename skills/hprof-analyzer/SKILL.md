---
name: hprof-analyzer
description: Analyze Android hprof heap dump files to detect memory leaks, analyze object distribution, and generate comprehensive memory reports. Use when the user provides a .hprof file path, wants to analyze memory leaks, or needs heap dump inspection. Supports both standard hprof-heap and Android hprof-libs formats.
---

## Overview

This skill analyzes Android `.hprof` heap dump files to detect memory leaks, analyze object distribution, and generate comprehensive memory reports. It supports both the **standard hprof-heap format** and **Android hprof-libs format** (Android 7.0+, the default format on modern Android devices).

## Input Requirements

- A `.hprof` file path (local file)

## Analysis Workflow

### Step 1: Verify File Format

Check the binary header to determine the hprof format variant:

```bash
xxd -l 32 "<hprof_file>"
```

**Both formats share the `JAVA PROFILE 1.0` magic** in the first 12 bytes. The distinction is made by the stated header size at offset 16:

- **hprof-heap (standard)**: stated header size is small (typically 128)
- **hprof-libs (Android 7.0+)**: stated header size is large (typically 13102+)

### Step 2: Detect Format and Record Layout

```python
import struct, sys, os

filepath = sys.argv[1]
with open(filepath, 'rb') as f:
    magic = f.read(16)
    stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
    file_size = os.path.getsize(filepath)

if stated_size > 2000:
    fmt = "hprof-libs (Android 7.0+)"
    record_start = 0x80
    tag_size = 2  # 2-byte tag
else:
    fmt = "hprof-heap (standard)"
    record_start = 16 + stated_size
    tag_size = 4  # 4-byte tag

print(f"Format: {fmt}, Records start at: 0x{record_start:X}")
```

### Step 3: Scan All Chunks/Records

#### For hprof-libs format (Android 7.0+):

Records use `tag(2B LE) + length(2B LE) + payload(length-4 bytes)`:

```python
import struct
from collections import Counter, defaultdict

def scan_hprof_libs_chunks(filepath):
    """Scan Android hprof-libs format file for all valid chunks."""
    valid_tags = {
        0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005,
        0x0010, 0x0011, 0x0013, 0x0014, 0x0015,
        0x0016, 0x0017, 0x0019, 0x0030, 0x0031, 0x0032,
    }
    
    file_size = os.path.getsize(filepath)
    chunks = []
    pos = 0x80
    
    while pos < file_size - 4:
        with open(filepath, 'rb') as f:
            f.seek(pos)
            header = f.read(4)
            if len(header) < 4:
                break
            tag, length = struct.unpack_from('<HH', header, 0)
        
        if tag in valid_tags and 4 <= length <= file_size - pos:
            chunks.append((pos, tag, length))
            pos += length
        else:
            pos += 1  # Scan forward for next valid chunk
    
    return chunks
```

#### For standard hprof-heap format:

Records use `tag(4B LE) + length(4B LE) + payload(length-8 bytes)`:

```python
def scan_standard_chunks(filepath):
    """Scan standard hprof-heap format file."""
    valid_tags = {0x01, 0x02, 0x03, 0x04, 0x05,
                  0x10, 0x11, 0x13, 0x14, 0x15, 0x19}
    
    file_size = os.path.getsize(filepath)
    chunks = []
    
    with open(filepath, 'rb') as f:
        f.read(16)  # magic
        stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        f.seek(16 + stated_size)
        
        while f.tell() < file_size - 8:
            pos = f.tell()
            tag = struct.unpack_from('<I', f.read(4), 0)[0]
            length = struct.unpack_from('<I', f.read(4), 0)[0]
            
            if tag in valid_tags and 8 <= length <= file_size - pos:
                chunks.append((pos, tag, length))
                f.seek(pos + length)
            else:
                f.seek(pos + 1)
    
    return chunks
```

### Step 4: Parse STRING_DUMP Chunks

Extract the string table (class names, field names, constant strings):

```python
def parse_string_dumps(chunks, filepath):
    """Parse STRING_DUMP chunks and build string table."""
    strings = {}  # string_id -> text
    
    for pos, tag, length in chunks:
        if tag not in (0x0010, 0x10):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # Format: string_id(4B LE) + string_length(2B LE) + string_data
        p = 0
        while p + 6 <= len(payload):
            string_id = struct.unpack_from('<I', payload, p)[0]
            string_len = struct.unpack_from('<H', payload, p+4)[0]
            
            if string_len > 5000 or string_len < 0:
                break
            
            if p + 6 + string_len <= len(payload):
                try:
                    text = payload[p+6:p+6+string_len].decode('utf-8')
                    strings[string_id] = text
                except UnicodeDecodeError:
                    pass
            
            p += 6 + string_len
    
    return strings
```

### Step 5: Parse CLASS_DUMP Chunks

Extract class metadata (serial numbers, instance counts):

```python
def parse_class_dumps(chunks, filepath):
    """Parse CLASS_DUMP chunks to get class metadata."""
    classes = {}  # class_serial -> {num_instances, ...}
    
    for pos, tag, length in chunks:
        if tag not in (0x0001, 0x01):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        if len(payload) < 8:
            continue
        
        class_serial = struct.unpack_from('<I', payload, 0)[0]
        
        # In standard hprof-heap: num_instances at offset 8
        # In hprof-libs: format differs; try offset 8 first
        num_instances = struct.unpack_from('<I', payload, 8)[0]
        
        # Validate: if num_instances looks like garbage (>100M),
        # try alternative offsets
        if num_instances > 100_000_000:
            # Try reading as 2B at offset 4
            num_instances = struct.unpack_from('<H', payload, 4)[0]
        
        classes[class_serial] = {
            'serial': class_serial,
            'num_instances': num_instances,
        }
    
    return classes
```

### Step 6: Parse LOAD_DATA Chunks

Extract class field layouts (maps field names to offsets):

```python
def parse_load_data(chunks, filepath):
    """Parse LOAD_DATA chunks to get class field layouts."""
    field_layouts = {}  # class_serial -> list of field info
    
    for pos, tag, length in chunks:
        if tag not in (0x0011, 0x11):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        if len(payload) < 8:
            continue
        
        class_serial = struct.unpack_from('<I', payload, 0)[0]
        object_id = struct.unpack_from('<I', payload, 4)[0]
        
        # Field data starts at offset 8
        # In hprof-libs, fields use: field_name + 0x01 + metadata
        fields = []
        p = 8
        while p + 9 <= len(payload):
            name_end = payload.find(b'\x01', p)
            if name_end == -1:
                break
            
            field_name = payload[p:name_end].decode('utf-8', errors='replace')
            
            # Parse metadata after 0x01 separator
            meta = payload[name_end+1:name_end+9]
            
            fields.append({
                'name': field_name,
                'offset': p,
                'meta': meta.hex() if len(meta) >= 8 else meta.hex(),
            })
            
            p = name_end + 9
        
        if fields:
            field_layouts[class_serial] = fields
    
    return field_layouts
```

### Step 7: Parse OBJECT_DUMP Chunks

Extract object instances and their field values:

```python
def parse_object_dumps(chunks, filepath):
    """Parse OBJECT_DUMP chunks to get object instances."""
    objects = []  # list of {object_id, class_serial, payload}
    
    for pos, tag, length in chunks:
        if tag not in (0x0004, 0x04):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        if len(payload) < 8:
            continue
        
        obj_id = struct.unpack_from('<I', payload, 0)[0]
        class_serial = struct.unpack_from('<I', payload, 4)[0]
        
        objects.append({
            'object_id': obj_id,
            'class_serial': class_serial,
            'payload': payload[8:],
        })
    
    return objects
```

### Step 8: Parse SAMPLE_GC_HEAP Chunks

Extract GC Root information and reachable objects:

```python
def parse_gc_heap_samples(chunks, filepath):
    """Parse SAMPLE_GC_HEAP chunks for GC root information."""
    gc_roots = []
    
    for pos, tag, length in chunks:
        if tag not in (0x0005, 0x05):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # Standard format: root_kind(4B) + root_info(4B) + object_id(4B) = 12 bytes
        # Android hprof-libs may use different entry sizes.
        # Heuristic: try 12-byte entries first, then fall back to analyzing
        # the raw binary for patterns (e.g., repeated class_serial values).
        
        p = 0
        valid_entries = 0
        
        while p + 12 <= len(payload):
            root_kind = struct.unpack_from('<I', payload, p)[0]
            root_info = struct.unpack_from('<I', payload, p+4)[0]
            object_id = struct.unpack_from('<I', payload, p+8)[0]
            
            # Validate: root_kind should be 0-10 for standard format
            if root_kind <= 10 and object_id > 0:
                kind_names = {
                    0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
                    3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
                    6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
                    9: 'DAEMON_WORKER', 10: 'UNKNOWN',
                }
                
                gc_roots.append({
                    'kind': kind_names.get(root_kind, f'0x{root_kind:08X}'),
                    'root_kind_raw': root_kind,
                    'root_info': root_info,
                    'object_id': object_id,
                })
                valid_entries += 1
                p += 12
            else:
                # Not a valid 12-byte entry; try smaller entry size
                # In hprof-libs, entries may be 8 bytes or variable size
                break
        
        # If no valid 12-byte entries, the data may use hprof-libs format
        # Report raw chunk size for manual analysis
        if valid_entries == 0:
            gc_roots.append({
                'kind': 'HPROF_LIBS_BINARY',
                'root_kind_raw': -1,
                'root_info': length,
                'object_id': 0,
                'raw_size': len(payload),
            })
    
    return gc_roots
```

### Step 9: Generate Comprehensive Report

Combine all parsed data into a Markdown report:

```python
import datetime

def generate_report(strings, classes, objects, gc_roots, field_layouts, filepath, fmt):
    """Generate a comprehensive memory analysis report."""
    
    # Count objects by class (from OBJECT_DUMP)
    object_class_counts = Counter()
    for obj in objects:
        object_class_counts[obj['class_serial']] += 1
    
    # Map class_serial to names using STRING_DUMP
    # In hprof-libs, class names may be embedded in LOAD_DATA or CHAIN_INSTANCE
    def get_class_name(serial):
        # Direct lookup in strings
        for sid, text in strings.items():
            if str(serial) in text:
                return text[:100]
        # Fallback
        return f"class_serial_{serial}"
    
    # GC Root analysis
    root_kind_counts = Counter()
    for r in gc_roots:
        root_kind_counts[r['kind']] += 1
    
    # Class instance distribution from CLASS_DUMP
    instance_distribution = Counter()
    for cs, info in classes.items():
        ni = info['num_instances']
        if ni == 0:
            instance_distribution['0 instances'] += 1
        elif ni < 10:
            instance_distribution['1-9'] += 1
        elif ni < 100:
            instance_distribution['10-99'] += 1
        elif ni < 1000:
            instance_distribution['100-999'] += 1
        elif ni < 10000:
            instance_distribution['1K-10K'] += 1
        elif ni < 100000:
            instance_distribution['10K-100K'] += 1
        else:
            instance_distribution['100K+'] += 1
    
    # Build report
    report_lines = []
    report_lines.append(f"# HPROF 内存分析报告")
    report_lines.append("")
    report_lines.append(f"**文件**: {os.path.basename(filepath)}")
    report_lines.append(f"**大小**: {os.path.getsize(filepath):,} bytes ({os.path.getsize(filepath)/1024/1024:.1f} MB)")
    report_lines.append(f"**格式**: {fmt}")
    report_lines.append(f"**日期**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append("")
    
    # Summary
    report_lines.append("## 概要")
    report_lines.append("")
    report_lines.append(f"- 类数量: {len(classes)}")
    report_lines.append(f"- 对象实例: {len(objects)}")
    report_lines.append(f"- GC Root 条目: {len([r for r in gc_roots if r['root_kind_raw'] >= 0])}")
    report_lines.append(f"- 字符串表: {len(strings)} 项")
    report_lines.append("")
    
    # Class instance distribution
    report_lines.append("## 类实例分布")
    report_lines.append("")
    report_lines.append("### CLASS_DUMP 统计")
    report_lines.append("| 实例范围 | 类数量 |")
    report_lines.append("|----------|--------|")
    for bucket in ['0 instances', '1-9', '10-99', '100-999', '1K-10K', '10K-100K', '100K+']:
        count = instance_distribution.get(bucket, 0)
        if count > 0:
            report_lines.append(f"| {bucket:>12s} | {count} |")
    report_lines.append("")
    
    # Objects by class
    report_lines.append("## 对象实例分布 (来自 OBJECT_DUMP)")
    report_lines.append("")
    report_lines.append("### Top 20 对象类")
    report_lines.append("| 类名 | 实例数 |")
    report_lines.append("|------|--------|")
    for cs, count in object_class_counts.most_common(20):
        name = get_class_name(cs)
        report_lines.append(f"| {name} | {count} |")
    report_lines.append("")
    
    # GC Root analysis
    report_lines.append("## GC Root 分析")
    report_lines.append("")
    report_lines.append("### Root 类型分布")
    report_lines.append("| 类型 | 数量 |")
    report_lines.append("|------|------|")
    for kind, count in root_kind_counts.most_common():
        report_lines.append(f"| {kind} | {count} |")
    report_lines.append("")
    
    # Memory leak detection
    report_lines.append("## 内存泄漏检测")
    report_lines.append("")
    report_lines.append("### 高实例数类 (潜在泄漏)")
    report_lines.append("")
    report_lines.append("以下类在 CLASS_DUMP 中有大量实例，可能存在内存泄漏：")
    report_lines.append("")
    high_instance_classes = [(cs, info) for cs, info in classes.items() if info['num_instances'] > 1000]
    high_instance_classes.sort(key=lambda x: -x[1]['num_instances'])
    for cs, info in high_instance_classes[:20]:
        name = get_class_name(cs)
        report_lines.append(f"- **{name}** (serial={cs}): {info['num_instances']:,} 实例")
    report_lines.append("")
    
    # Field layout samples
    report_lines.append("## 类字段布局示例 (来自 LOAD_DATA)")
    report_lines.append("")
    for cs, fields in list(field_layouts.items())[:5]:
        report_lines.append(f"### Class serial {cs} ({len(fields)} fields)")
        for f in fields[:10]:
            report_lines.append(f"  - `{f['name']}` (offset={f['offset']})")
        report_lines.append("")
    
    # Thread info
    report_lines.append("## 线程快照")
    report_lines.append("")
    thread_chunks = [c for c in chunks if c[1] in (0x0003, 0x0002)]
    report_lines.append(f"- THREAD_SUSPEND chunks: {len([c for c in chunks if c[1] == 0x0003])}")
    report_lines.append(f"- STACK_FRAME chunks: {len([c for c in chunks if c[1] == 0x0002])}")
    report_lines.append("")
    
    # Severity assessment
    report_lines.append("## 风险评级")
    report_lines.append("")
    total_leaked = sum(info['num_instances'] * 1024 for info in classes.values() if info['num_instances'] > 10000)
    if total_leaked > 50 * 1024 * 1024:
        report_lines.append("**[P0] 严重**: 检测到超过 50MB 的潜在泄漏对象")
    elif total_leaked > 20 * 1024 * 1024:
        report_lines.append("**[P1] 显著**: 检测到超过 20MB 的潜在泄漏对象")
    elif total_leaked > 5 * 1024 * 1024:
        report_lines.append("**[P2] 中等**: 检测到超过 5MB 的潜在泄漏对象")
    else:
        report_lines.append("**[P3] 轻微**: 未发现明显的内存泄漏")
    report_lines.append("")
    
    return '\n'.join(report_lines)
```

### Step 10: Cross-validate with hprof-conv (optional)

For verification, use Android SDK's `hprof-conv` to convert the file and compare key statistics:

```bash
# Convert hprof-libs to standard format
hprof-conv -z <input.hprof> <output_converted.hprof>

# Parse the converted file with standard hprof-heap parser
# Compare class counts and object counts
```

**Important**: `hprof-conv` may lose significant data when converting hprof-libs format. Use it only for cross-validation of what it CAN parse. The direct parsing approach (Steps 3-9) extracts far more data from Android hprof-libs files.

## Execution Constraints

- NEVER create temporary files in the user's workspace directory. Use inline Python or write temporary files to `/tmp/` only.
- All intermediate files (parsed JSON, analysis results) should go to `/tmp/hprof_analysis/`.
- After analysis is complete, the workspace should remain clean.
- The hprof file may be large (100MB+). Parse efficiently using seek-based access, not full file read.
- If the file is too large for a single pass, process chunks incrementally.

## Severity Classification for Memory Issues

| Severity | Threshold | Meaning |
|----------|-----------|---------|
| P0 | > 50MB leaked | Critical leak — immediate action required |
| P1 | > 20MB leaked | Significant leak — should be fixed soon |
| P2 | > 5MB leaked | Moderate leak — plan to fix |
| P3 | ≤ 5MB leaked | Minor — worth noting but low priority |

## Notes

- **Kotlin synthetic fields**: Class names with `$` (e.g., `MyClass$onCreate$1`) are Kotlin-generated. Annotate with `← Kotlin synthetic` in reports.
- **ProGuard obfuscation**: Single-letter method names (e.g., `a()`, `b()`) with digit suffixes (e.g., `a3()`) are ProGuard-obfuscated. Do NOT guess original names.
- **DroidPlugin**: Classes with `msdocker.*` or unusual naming like `Ill111l` are DroidPlugin internals.
- **Coroutines**: `$this$coroutineScope`, `$this$launchWhenResumed` are Kotlin coroutine synthetic fields.
- **Android hprof-libs format**: The CLASS_DUMP, LOAD_DATA, and SAMPLE_GC_HEAP chunk formats differ from standard hprof-heap. When parsing produces obviously invalid values (e.g., instance counts > 100M), try alternative field offsets or formats.
- If you cannot find expected data in the file, it may mean your parsing approach is wrong — try a different method rather than concluding the data doesn't exist.
