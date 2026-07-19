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

> **Detailed format comparison**: See [references/hprof-libs-vs-heap.md](references/hprof-libs-vs-heap.md) for a thorough breakdown of differences between the two formats, including why `hprof-conv` loses significant data on hprof-libs files.

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
        
        # hprof-libs STRING_DUMP format:
        # string_text + 0x01 + 7_zero_bytes + 1B_value + 4B_ref
        # The 7 zeros and 1B_value are metadata; the 4B_ref is a string_id/pointer.
        # IMPORTANT: The first entry in a chunk may have a garbage first byte
        # (carry-over from previous chunk's ref), so validate text is printable.
        
        p = 0
        while p < len(payload) - 13:
            sep = payload.find(b'\x01', p)
            if sep == -1:
                break
            
            text_bytes = payload[p:sep]
            
            # Validate: all bytes must be printable ASCII
            if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                meta = payload[sep+1:sep+13]
                if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                    string_id = struct.unpack_from('<I', meta, 8)[0]
                    text = text_bytes.decode('utf-8')
                    strings[string_id] = text
            
            p = sep + 13
    
    return strings
```

### Step 5: Parse CLASS_DUMP Chunks

Extract class metadata (serial numbers, instance counts):

```python
def parse_class_dumps(chunks, filepath):
    """Parse CLASS_DUMP chunks to get class metadata.
    
    hprof-libs CLASS_DUMP format:
    - Each chunk contains a table of class entries
    - Entry format: [type_code(1B)] [instance_count(1B)] [field_info(variable)] [marker(4B: 0x004000XX)]
    - The marker's XX byte is the NEXT class_serial (sequential counter)
    - type_code values: 0x02 = has instances, 0x0A = no instances, 0x0B = static/other
    - instance_count: 0xFF = unknown/special, otherwise actual count
    - field_info: variable-length data encoding class properties
    - A 7-byte header precedes the first entry in each chunk
    """
    classes = {}  # class_serial -> {num_instances, ...}
    
    for pos, tag, length in chunks:
        if tag not in (0x0001, 0x01):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # Find first marker to skip header
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            continue
        
        p = first_marker + 4  # Skip past first marker
        
        while p < len(payload) - 4:
            next_marker = payload.find(b'\x00\x40\x00', p)
            if next_marker == -1:
                break
            
            entry_data = payload[p:next_marker]
            class_serial = payload[next_marker + 3]  # The XX byte of marker
            
            if len(entry_data) >= 2:
                instance_count = entry_data[1]
                
                # 0xFF means unknown/special, skip
                if instance_count != 0xFF:
                    classes[class_serial] = {
                        'serial': class_serial,
                        'num_instances': instance_count,
                    }
            
            p = next_marker + 4
    
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
        while p < len(payload) - 13:
            name_end = payload.find(b'\x01', p)
            if name_end == -1:
                break
            
            field_name_bytes = payload[p:name_end]
            
            # Validate: field name must be printable ASCII
            if len(field_name_bytes) > 0 and all(32 <= b < 127 for b in field_name_bytes):
                meta = payload[name_end+1:name_end+13]
                if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                    field_name = field_name_bytes.decode('utf-8')
                    fields.append({
                        'name': field_name,
                        'offset': p,
                    })
            
            p = name_end + 13
        
        if fields:
            field_layouts[class_serial] = fields
    
    return field_layouts
```

### Step 7: Parse OBJECT_DUMP Chunks

Extract object instances and their field values:

```python
def parse_object_dumps(chunks, filepath):
    """Parse OBJECT_DUMP chunks to get object instances.
    
    hprof-libs OBJECT_DUMP uses the same marker-based table format as CLASS_DUMP:
    - Each entry: [data_before_marker] [00 40 00 XX marker]
    - The marker's XX byte is the class_serial
    - Entry data before marker contains object_id and field values
    """
    objects = []  # list of {object_id, class_serial, payload}
    
    for pos, tag, length in chunks:
        if tag not in (0x0004, 0x04):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # Find first marker to skip any header
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            continue
        
        p = first_marker + 4  # Skip past first marker
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
            
            p = next_marker + 4
    
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
        
        # hprof-libs SAMPLE_GC_HEAP format:
        # Each entry is 20 bytes:
        #   object_id(4B LE) + root_info(4B LE) + root_kind(2B LE) +
        #   class_serial(4B LE) + pad(4B LE) + extra(2B LE)
        # root_kind values: 0=JAVA_STACK, 1=NATIVE_STACK, 2=SYSTEM_CLASS,
        #   3=GC_STATIC_FIELD, 4=GC_LOCAL, 5=GC_MONITOR, 6=GC_JAVA_FRAME,
        #   7=GC_NATIVE_FRAME, 8=UNREACHABLE, 9=DAEMON_WORKER, 10=UNKNOWN
        # class_serial is often a constant system class (e.g., 0x78c6096f = java/lang/Object)
        
        p = 0
        while p + 20 <= len(payload):
            object_id = struct.unpack_from('<I', payload, p)[0]
            root_info = struct.unpack_from('<I', payload, p+4)[0]
            root_kind = struct.unpack_from('<H', payload, p+8)[0]
            class_serial = struct.unpack_from('<I', payload, p+10)[0]
            
            if root_kind <= 10 and object_id > 0:
                kind_names = {
                    0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
                    3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
                    6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
                    9: 'DAEMON_WORKER', 10: 'UNKNOWN',
                }
                
                gc_roots.append({
                    'kind': kind_names.get(root_kind, f'0x{root_kind:04X}'),
                    'root_kind_raw': root_kind,
                    'root_info': root_info,
                    'object_id': object_id,
                    'class_serial': class_serial,
                })
                p += 20
            else:
                break
    
    return gc_roots
```

### Step 8.5: Parse THREAD_SUSPEND Chunks

Extract thread suspension snapshots — thread names, object IDs, and stack frame ID lists. These are essential for building GC Root → Thread → Stack Frame reference chains.

```python
def parse_thread_suspended(chunks, filepath):
    """Parse THREAD_SUSPEND chunks to extract thread info and stack frame IDs.
    
    hprof-libs THREAD_SUSPEND uses a marker-based table format, same as CLASS_DUMP/OBJECT_DUMP:
      [entry_data(variable)] [00 40 00 class_serial(1B)]
    
    Each entry contains (variable length, typically ~10-20 bytes):
      - thread_obj_id(4B LE) — object ID used to link from SAMPLE_GC_HEAP.root_info when root_kind=JAVA_STACK
      - pad(4B LE)
      - suspend_type(1B) — 0=suspend, 1=yield, etc.
      - pad(1B)
      - thread_name_len(2B LE)
      - thread_name(thread_name_len bytes UTF-8)
      - stack_frame_count(4B LE)
      - frame_ids(stack_frame_count × 4B LE) — frame IDs that reference STACK_FRAME chunks
    
    Returns: dict mapping thread_obj_id -> {
        'name': str,
        'suspend_type': int,
        'frame_ids': [int, ...]
    }
    """
    threads = {}  # thread_obj_id -> thread info
    
    for pos, tag, length in chunks:
        if tag not in (0x0003, 0x03):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # Find first marker to skip any header
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            continue
        
        p = first_marker + 4  # Skip past first marker
        
        while p < len(payload) - 4:
            next_marker = payload.find(b'\x00\x40\x00', p)
            if next_marker == -1 or next_marker + 4 >= len(payload):
                break
            
            entry_data = payload[p:next_marker]
            class_serial = payload[next_marker + 3]  # The XX byte of marker
            
            if len(entry_data) >= 8:
                thread_obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                pad = struct.unpack_from('<I', entry_data, 4)[0]
                
                offset = 8
                if offset >= len(entry_data):
                    p = next_marker + 4
                    continue
                    
                suspend_type = entry_data[offset]
                offset += 1
                pad2 = entry_data[offset] if offset < len(entry_data) else 0
                offset += 1
                
                if offset + 2 > len(entry_data):
                    p = next_marker + 4
                    continue
                    
                name_len = struct.unpack_from('<H', entry_data, offset)[0]
                offset += 2
                
                if offset + name_len > len(entry_data):
                    # Name extends beyond entry — use rest of data
                    thread_name = entry_data[offset:].decode('utf-8', errors='replace')
                    remaining = 0
                else:
                    thread_name = entry_data[offset:offset+name_len].decode('utf-8', errors='replace')
                    remaining = name_len
                offset += remaining
                
                if offset + 4 > len(entry_data):
                    frame_count = 0
                    frame_ids = []
                else:
                    frame_count = struct.unpack_from('<I', entry_data, offset)[0]
                    offset += 4
                    
                    if offset + frame_count * 4 <= len(entry_data) and frame_count > 0:
                        frame_ids = []
                        for i in range(frame_count):
                            fid = struct.unpack_from('<I', entry_data, offset + i*4)[0]
                            frame_ids.append(fid)
                    else:
                        frame_ids = []
                
                if thread_obj_id > 0:
                    threads[thread_obj_id] = {
                        'name': thread_name,
                        'suspend_type': suspend_type,
                        'frame_ids': frame_ids,
                        'class_serial': class_serial,
                    }
            
            p = next_marker + 4
    
    return threads
```

### Step 8.6: Parse STACK_FRAME Chunks

Extract stack frame details — class/method names and line numbers per frame ID. Used to resolve `root_info` references from SAMPLE_GC_HEAP entries.

```python
def parse_stack_frames(chunks, filepath, string_table):
    """Parse STACK_FRAME chunks to extract frame details.
    
    hprof-libs STACK_FRAME uses the same marker-based table format:
      [entry_data(variable)] [00 40 00 class_serial(1B)]
    
    Each entry contains (typically 5 bytes before marker):
      - frame_id(4B LE) — unique frame identifier
      - type_code(1B) — 0x02 = has instances, 0x0A = no instances, etc.
    
    The actual frame data is at the beginning of the chunk, before the first marker:
      frame_id(4B LE) + class_serial(4B LE) + pad(4B LE) + method_index(4B LE) + line_number(4B LE)
    
    Returns: dict mapping frame_id -> {
        'class_serial': int,
        'class_name': str,       # resolved via string_table
        'method_index': int,
        'method_name': str,      # resolved via string_table
        'line_number': int,
        'type_code': int
    }
    """
    frames = {}  # frame_id -> frame info
    
    for pos, tag, length in chunks:
        if tag not in (0x0002, 0x02):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # First, parse the pre-marker data (before first 00 40 00)
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            continue
        
        # Pre-marker block may contain initial frame data
        pre_data = payload[:first_marker]
        p = 0
        while p + 16 <= len(pre_data):
            frame_id = struct.unpack_from('<I', pre_data, p)[0]
            class_serial = struct.unpack_from('<I', pre_data, p+4)[0]
            pad = struct.unpack_from('<I', pre_data, p+8)[0]
            method_index = struct.unpack_from('<I', pre_data, p+12)[0]
            line_number = struct.unpack_from('<I', pre_data, p+16)[0] if p + 20 <= len(pre_data) else -1
            
            if frame_id > 0:
                class_name = string_table.get(class_serial, f'class_serial_{class_serial}')
                method_name = _resolve_method_index(method_index, string_table)
                
                frames[frame_id] = {
                    'class_serial': class_serial,
                    'class_name': class_name,
                    'method_index': method_index,
                    'method_name': method_name,
                    'line_number': line_number,
                }
            p += 20
        
        # Then parse marker-based entries
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
                
                if frame_id > 0 and frame_id in frames:
                    frames[frame_id]['type_code'] = type_code
                    frames[frame_id]['class_serial'] = class_serial
            
            p = next_marker + 4
    
    return frames


def _resolve_method_index(method_index, string_table):
    """Resolve a method index to a readable name using the string table.
    
    In hprof-libs, method_index maps to a string_id in STRING_DUMP chunks.
    The string_table maps string_id -> text.
    """
    if method_index == 0:
        return '<unknown>'
    
    # method_index is often a string_id; look it up directly
    if method_index in string_table:
        return string_table[method_index]
    
    # Try as a serial number (some implementations use serial lookup)
    if method_index < 256:
        return f'method_index_{method_index}'
    
    return f'method_index_{method_index}'
```

### Step 8.7: Decode root_info and Build Reference Chains

Correlate SAMPLE_GC_HEAP entries with THREAD_SUSPEND and STACK_FRAME data to produce human-readable reference chains.

```python
def decode_root_info(gc_root, thread_map, frame_map):
    """Interpret root_info field based on root_kind and produce a human-readable context.
    
    root_kind -> root_info interpretation:
      0 (JAVA_STACK):  root_info = thread object ID → look up in thread_map
      1 (NATIVE_STACK): root_info = native stack pointer → keep raw
      2 (SYSTEM_CLASS): root_info = 0 typically → keep as-is
      3 (GC_STATIC_FIELD): root_info = static field reference → need class + field
      4 (GC_LOCAL): root_info = local var ref → look up in frame_map
      5 (GC_MONITOR): root_info = monitor ID → keep raw
      6 (GC_JAVA_FRAME): root_info = Java frame info → look up in frame_map
      7+ (others): keep raw
    """
    kind = gc_root['root_kind_raw']
    root_info = gc_root['root_info']
    decoded = {'raw': root_info, 'context': None}
    
    if kind == 0:  # JAVA_STACK
        if root_info in thread_map:
            t = thread_map[root_info]
            decoded['context'] = {
                'type': 'thread',
                'name': t['name'],
                'obj_id': root_info,
                'frame_ids': t['frame_ids'],
            }
    elif kind == 4:  # GC_LOCAL
        # root_info may be a frame_id
        if root_info in frame_map:
            decoded['context'] = {
                'type': 'local_var',
                'frame': frame_map[root_info],
            }
    elif kind == 6:  # GC_JAVA_FRAME
        if root_info in frame_map:
            decoded['context'] = {
                'type': 'java_frame',
                'frame': frame_map[root_info],
            }
    
    return decoded


def build_reference_chain(gc_root, thread_map, frame_map, class_map, string_table):
    """Build a single GC Root → Thread/Local → Stack Frame → Target Object chain.
    
    Returns a formatted chain dict suitable for report generation.
    """
    kind = gc_root['kind']
    decoded = decode_root_info(gc_root, thread_map, frame_map)
    
    # Look up target object class
    obj_id = gc_root['object_id']
    target_class = class_map.get(obj_id, 'unknown')
    target_name = target_class.get('class_name', 'unknown')
    target_instances = target_class.get('num_instances', '?')
    
    chain = {
        'root_kind': kind,
        'target_class': target_name,
        'target_instance_count': target_instances,
        'stack_trace': [],
        'decoded_context': decoded,
    }
    
    if decoded['context'] and decoded['context']['type'] == 'thread':
        thread = decoded['context']
        chain['thread_name'] = thread['name']
        # Walk frame_ids through frame_map to build stack trace
        for fid in thread.get('frame_ids', []):
            if fid in frame_map:
                f = frame_map[fid]
                chain['stack_trace'].append(
                    f"{f['class_name']}.{f['method_name']}({f['line_number']})"
                )
            else:
                chain['stack_trace'].append(f'<frame_id={fid}>')
    elif decoded['context']:
        ctx = decoded['context']
        if ctx.get('type') == 'local_var' and ctx.get('frame'):
            f = ctx['frame']
            chain['stack_trace'].append(
                f"{f['class_name']}.{f['method_name']} (local variable)"
            )
        elif ctx.get('type') == 'java_frame' and ctx.get('frame'):
            f = ctx['frame']
            chain['stack_trace'].append(
                f"{f['class_name']}.{f['method_name']} (Java frame)"
            )
    
    return chain


def build_reference_chains(gc_roots, thread_map, frame_map, class_map, string_table):
    """Build GC Root → Thread → Stack Frame → Leaked Object chains for all roots.
    
    Groups chains by (target_class, root_kind) to merge similar leaks.
    
    Returns list of chain dicts sorted by target_instance_count descending.
    """
    # Group by (target_class_name, root_kind)
    groups = {}
    unknown_count = 0
    
    for root in gc_roots:
        chain = build_reference_chain(root, thread_map, frame_map, class_map, string_table)
        key = (chain['target_class'], chain['root_kind'])
        
        if key not in groups:
            groups[key] = {
                'chains': [],
                'total_roots': 0,
            }
        
        groups[key]['chains'].append(chain)
        groups[key]['total_roots'] += 1
        
        if not chain['stack_trace']:
            unknown_count += 1
    
    result = []
    for (target_class, root_kind), group in groups.items():
        # Pick the chain with the most stack frames as representative
        best_chain = max(group['chains'], key=lambda c: len(c['stack_trace']))
        
        # Determine priority based on instance count
        try:
            inst_count = int(best_chain['target_instance_count']) if best_chain['target_instance_count'] != '?' else 0
        except ValueError:
            inst_count = 0
        
        if inst_count > 50000:
            priority = 'P0'
        elif inst_count > 20000:
            priority = 'P1'
        elif inst_count > 5000:
            priority = 'P2'
        else:
            priority = 'P3'
        
        result.append({
            'priority': priority,
            'root_kind': root_kind,
            'target_class': target_class,
            'target_instance_count': best_chain['target_instance_count'],
            'total_roots': group['total_roots'],
            'stack_trace': best_chain['stack_trace'],
            'thread_name': best_chain.get('thread_name', ''),
            'decoded_context': best_chain['decoded_context'],
        })
    
    # Sort by instance count descending
    result.sort(key=lambda x: (int(x['target_instance_count']) if isinstance(x['target_instance_count'], int) else 0), reverse=True)
    
    return result
```

### Step 9: Generate Comprehensive Report

Refer to [references/report-template.md](references/report-template.md) for the complete Markdown report template. Use it as the structure for your analysis output.

Key sections to populate:

1. **概要** — Overall health summary with key metrics table
2. **堆分布** — Top 20 by object count and shallow size, class instance distribution histogram
3. **GC Root 分析** — Root type distribution, suspicious reference chains with stack traces
4. **内存泄漏检测** — High-instance classes, pattern matching (Activity leak, listener not unregistered, WebView leak, Handler leak, static collection growth)
5. **线程快照** — Active threads and stack snapshots
6. **风险评级** — P0-P3 severity assessment

To build GC Root reference chains, call the following pipeline after parsing all chunks:

```python
# Pipeline: parse THREAD_SUSPEND + STACK_FRAME, then build chains
thread_map = parse_thread_suspended(chunks, filepath)
frame_map = parse_stack_frames(chunks, filepath, string_table)

# Build reference chains from gc_roots + thread_map + frame_map
chains = build_reference_chains(
    gc_roots=gc_roots,
    thread_map=thread_map,
    frame_map=frame_map,
    class_map=classes_with_object_ids,  # {object_id: {'class_name': ..., 'num_instances': ...}}
    string_table=string_table,
)

# Count roots by kind for the root type distribution table
from collections import Counter
root_kind_counts = Counter(root['kind'] for root in gc_roots)
unknown_count = sum(1 for root in gc_roots if root['kind'] == 'UNKNOWN')
total_roots = len(gc_roots)
resolved_ratio = 1 - unknown_count / total_roots if total_roots > 0 else 0

# Also decode all root_info fields for classification improvement
decoded_roots = []
for root in gc_roots:
    decoded = decode_root_info(root, thread_map, frame_map)
    decoded_roots.append(decoded)
```

When annotating class/method names:
- Kotlin synthetic fields (`$this$coroutineScope`, `$onCreate$1`): add `← Kotlin synthetic` annotation
- ProGuard-obfuscated names (`a3()`, `v3()`): add `← obfuscated method (ProGuard)` — do NOT guess original names
- DroidPlugin internals (`msdocker.*`, `Ill111l`): add `← DroidPlugin hook` annotation

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

## Output Convention

Save the final analysis report to `hprof_analysis/<hprof_filename_without_extension>_report.md`.

Examples:
- Input: `dump.hprof` → Output: `hprof_analysis/dump_report.md`
- Input: `taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` → Output: `hprof_analysis/taqu_android_client_logfile_401_1783731893047_1_1_342013740_report.md`

If the `hprof_analysis/` directory doesn't exist, create it.

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
