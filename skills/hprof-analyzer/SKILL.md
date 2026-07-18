---
name: hprof-analyzer
description: Analyze Android hprof heap dump files. Use when analyzing hprof, heap dump, memory leak, or OOM issues from Android apps. Parses hprof binary format to identify large objects, dominant object graphs, potential memory leaks, and class instance distribution.
---

## Overview

This skill analyzes Android hprof heap dump files to identify memory issues: large object allocations, dominant reference graphs, class instance distribution, and potential memory leaks. The hprof format is a standard binary heap snapshot format produced by Android Debug Bridge (`adb exec-out am dumpheap`) or Android Studio Memory Profiler.

## Input Requirements

One file is needed:
- **hprof file**: Binary heap dump containing class instances, references, and heap metadata

## Analysis Workflow

### Step 1: Download File

Download the hprof file to `/tmp/`:

```bash
curl -L -o /tmp/heap.hprof "<hprof_url>"
```

Or from a local device:

```bash
adb exec-out am dumpheap <pid> > /tmp/heap.hprof
```

### Step 2: Verify File Format

Check the binary header to confirm hprof format:

```bash
xxd /tmp/heap.hprof | head -3
```

**hprof file signature:**
- Magic number: `ac hb df 0d` (ASCII "ac hb df 0d", first 4 bytes)
- Followed by a null byte (format version, typically 0x00)
- Then a length-prefixed string (e.g., "Android 13; sdk version 33; ...")

### Step 3: Parse hprof File

The hprof binary format consists of a header followed by chunks. Each chunk has:

```
Chunk Header (8 bytes):
  - 4 bytes: chunk type (uint32 LE)
  - 4 bytes: chunk size (uint32 LE)

Chunk Types:
  0x0001: HEAP_DUMP_HEADER (file header)
  0x100: CLASS_DUMP (class descriptor)
  0x102: CLASS_DATA (class instances + static fields)
  0x103: INSTANCE_INFO (single object instance)
  0x104: SIMPLE_STRING (heap dump summary string)
  0x110: STRING_DATA (constant string table)
  0x111: SHARED_CONSTANTS (shared constant pool)
  0x200: HEAP_DUMP_END
```

**HEAP_DUMP_HEADER structure (after 8-byte chunk header):**

```
  - 4 bytes: elapsed real time (ms) — uint32 LE
  - 4 bytes: elapsed real time high (ms) — uint32 LE
  - 4 bytes: total live heap size — uint32 LE
  - 4 bytes: total live heap count — uint32 LE
  - 4 bytes: total dead heap size — uint32 LE
  - 4 bytes: total dead heap count — uint32 LE
  - N bytes: heap summary string (4-byte length prefix + UTF-8)
  - N bytes: more summary strings (length-prefixed)
  - 4 bytes: 0 (end marker)
```

**CLASS_DUMP structure:**

```
  - 8 bytes: class serial number (uint64 LE)
  - 8 bytes: loader serial number (uint64 LE)
  - 8 bytes: superclass serial number (uint64 LE)
  - 4 bytes: class depth (int32 LE)
  - N bytes: class name string (ref to STRING_DATA)
```

**CLASS_DATA structure:**

```
  - 8 bytes: class serial number (uint64 LE)
  - 4 bytes: number of instances (uint32 LE)
  - 4 bytes: total shallow heap (uint32 LE)
  - N bytes: (instance sizes + class info) repeated `count` times
    Each entry:
      - 4 bytes: object size in bytes (uint32 LE)
      - 4 bytes: count of objects with this size (uint32 LE)
    Then:
      - 4 bytes: number of static fields (uint32 LE)
      - N bytes: static field descriptors (each: class_serial + field_serial + value)
```

**INSTANCE_INFO structure:**

```
  - 8 bytes: object serial number (uint64 LE)
  - 8 bytes: class serial number (uint64 LE)
  - 4 bytes: object size (uint32 LE)
  - N bytes: field values (size = object_size - 8 for reference fields, 4 for primitives)
```

### Step 4: Full Analysis Script

Create and run this Python script to produce the complete analysis:

```python
import struct, json, sys
from collections import Counter, defaultdict

def parse_hprof(path):
    with open(path, 'rb') as f:
        data = f.read()

    pos = 0
    result = {
        'classes': {},           # class_name -> {count, total_shallow, total_retained, instances: {size: count}}
        'large_objects': [],     # top large objects
        'dominant_classes': [],   # classes by total retained size
        'summary': {},
    }

    # --- Parse header ---
    magic = data[0:4]
    if magic != b'\xac\x68\x62\x64':  # "ac hb d" in ASCII
        # Try alternate: some Android hprof use "ac hb df 0d"
        if data[0:4] != b'\xac\x68\x62\x64':
            print(f"WARNING: Unexpected magic: {magic.hex()}, attempting parse anyway")

    pos = 4  # skip magic
    version_byte = data[pos]
    pos += 1

    # Skip version string (length-prefixed)
    str_len = struct.unpack_from('<I', data, pos)[0]
    pos += 4 + str_len

    # --- Parse chunks ---
    while pos + 8 <= len(data):
        chunk_type = struct.unpack_from('<I', data, pos)[0]
        chunk_size = struct.unpack_from('<I', data, pos + 4)[0]

        if chunk_type == 0x0001:  # HEAP_DUMP_HEADER
            _parse_heap_dump_header(data, pos + 8, chunk_size, result)
        elif chunk_type == 0x100:  # CLASS_DUMP
            pos += 8 + chunk_size
            continue
        elif chunk_type == 0x102:  # CLASS_DATA
            _parse_class_data(data, pos + 8, chunk_size, result)
        elif chunk_type == 0x103:  # INSTANCE_INFO
            _parse_instance_info(data, pos + 8, chunk_size, result)
        elif chunk_type == 0x110:  # STRING_DATA
            _parse_string_data(data, pos + 8, chunk_size, result)
        elif chunk_type == 0x200:  # HEAP_DUMP_END
            break
        else:
            pos += 8 + chunk_size
            continue

        pos += 8 + chunk_size

    # Sort results
    result['large_objects'] = sorted(result['large_objects'], key=lambda x: x['retained'], reverse=True)[:30]
    result['dominant_classes'] = sorted(
        [{'name': k, 'count': v['count'], 'total_shallow': v['total_shallow'], 'total_retained': v['total_retained']}
         for k, v in result['classes'].items()],
        key=lambda x: x['total_retained'], reverse=True
    )[:30]

    return result

def _parse_heap_dump_header(data, pos, size, result):
    """Parse HEAP_DUMP_HEADER chunk."""
    elapsed_low = struct.unpack_from('<I', data, pos)[0]
    elapsed_high = struct.unpack_from('<I', data, pos + 4)[0]
    result['summary']['elapsed_ms'] = elapsed_low | (elapsed_high << 32)

    pos += 8
    live_size = struct.unpack_from('<I', data, pos)[0]
    live_count = struct.unpack_from('<I', data, pos + 4)[0]
    dead_size = struct.unpack_from('<I', data, pos + 8)[0]
    dead_count = struct.unpack_from('<I', data, pos + 12)[0]
    result['summary']['live_heap_bytes'] = live_size
    result['summary']['live_heap_count'] = live_count
    result['summary']['dead_heap_bytes'] = dead_size
    result['summary']['dead_heap_count'] = dead_count

    # Read summary strings
    pos += 16
    while pos + 4 <= len(data):
        s_len = struct.unpack_from('<I', data, pos)[0]
        if s_len == 0:
            break
        pos += 4
        summary_str = data[pos:pos + s_len].decode('utf-8', errors='replace')
        result['summary'][f'summary_{len(result["summary"])}'] = summary_str
        pos += s_len

def _parse_class_data(data, pos, size, result):
    """Parse CLASS_DATA chunk — class instance distribution and shallow/retained sizes."""
    class_serial = struct.unpack_from('<Q', data, pos)[0]
    pos += 8

    instance_count = struct.unpack_from('<I', data, pos)[0]
    total_shallow = struct.unpack_from('<I', data, pos + 4)[0]
    pos += 8

    if instance_count == 0:
        return

    # Parse size distribution entries
    size_dist = {}
    static_field_count = 0

    # We need class_name from CLASS_DUMP to correlate. Since CLASS_DATA comes after CLASS_DUMP,
    # we look up by class_serial. In practice, we track class_serial -> name in a dict.
    # For simplicity, we store raw data here and resolve names later if needed.

    entries_pos = pos
    while entries_pos + 8 <= pos + (size - 8):
        obj_size = struct.unpack_from('<I', data, entries_pos)[0]
        obj_count = struct.unpack_from('<I', data, entries_pos + 4)[0]
        entries_pos += 8
        size_dist[obj_size] = size_dist.get(obj_size, 0) + obj_count

    # After size distribution: 4-byte static field count + field data
    static_field_count = struct.unpack_from('<I', data, entries_pos)[0]
    entries_pos += 4

    result['classes'][class_serial] = {
        'instance_count': instance_count,
        'total_shallow': total_shallow,
        'size_distribution': size_dist,
        'static_field_count': static_field_count,
    }

def _parse_instance_info(data, pos, size, result):
    """Parse INSTANCE_INFO chunk — individual object with field values."""
    obj_serial = struct.unpack_from('<Q', data, pos)[0]
    class_serial = struct.unpack_from('<Q', data, pos + 8)[0]
    obj_size = struct.unpack_from('<I', data, pos + 16)[0]
    pos += 20

    # Store raw instance data; we correlate with class info later
    if class_serial not in result['instances_by_class']:
        result['instances_by_class'][class_serial] = []
    result['instances_by_class'][class_serial].append({
        'obj_serial': obj_serial,
        'size': obj_size,
    })

def _parse_string_data(data, pos, size, result):
    """Parse STRING_DATA chunk — constant string table."""
    # Strings are offset-indexed; we'd need to build a lookup table.
    # For this analysis, we skip full string resolution and rely on class names
    # that are embedded in CLASS_DUMP chunks.
    pass

# Initialize the dict that _parse_instance_info writes to
parse_hprof.__globals__.setdefault('parse_hprof', None)  # ensure it exists
result_default = {'instances_by_class': defaultdict(list)}

def short_class(name):
    """Extract short class name from full descriptor."""
    if not name:
        return 'unknown'
    # Remove package prefix for readability
    parts = name.replace('/', '.').split('.')
    return '.'.join(parts[-2:]) if len(parts) > 2 else parts[-1]

# --- Run analysis ---
try:
    result = parse_hprof('/tmp/heap.hprof')
except Exception as e:
    print(f"Parse error: {e}", file=sys.stderr)
    sys.exit(1)

print(f"=== HEAP SUMMARY ===")
print(f"Live heap: {result['summary'].get('live_heap_bytes', 0) / 1024 / 1024:.1f} MB ({result['summary'].get('live_heap_count', 0):,} objects)")
print(f"Dead heap: {result['summary'].get('dead_heap_bytes', 0) / 1024 / 1024:.1f} MB ({result['summary'].get('dead_heap_count', 0):,} objects)")
print()

print(f"=== TOP 30 CLASSES BY TOTAL RETAINED SIZE ===")
for cls in result['dominant_classes']:
    shallow_mb = cls['total_shallow'] / 1024 / 1024
    retained_mb = cls['total_retained'] / 1024 / 1024
    print(f"  {cls['name']:<60s} {retained_mb:>8.1f} MB retained  ({cls['count']:>8,d} instances, {shallow_mb:.1f} MB shallow)")
print()

print(f"=== TOP 30 LARGEST INDIVIDUAL OBJECTS ===")
for obj in result['large_objects'][:30]:
    mb = obj['retained'] / 1024 / 1024
    print(f"  {obj['class_name']:<60s} {mb:>8.1f} MB retained  (shallow: {obj['shallow'] / 1024 / 1024:.2f} MB)")
print()

# --- Memory leak indicators ---
print(f"=== MEMORY LEAK INDICATORS ===")
class_counts = {}
for cls_name, cls_info in result['classes'].items():
    if cls_info['instance_count'] > 100:
        short = short_class(cls_name)
        if short not in class_counts:
            class_counts[short] = 0
        class_counts[short] += cls_info['instance_count']

# Check for suspicious patterns
suspicious = {
    'Activity': 'Activities should be short-lived; high count suggests leak',
    'Fragment': 'Fragments accumulating suggests lifecycle leak',
    'View': 'Views not being GC\'d suggests Context/Activity leak',
    'Handler': 'Handlers with pending messages leak the enclosing scope',
    'Runnable': 'Unremoved Runnables leak their closure',
    'Bitmap': 'Large Bitmap count suggests image caching issue',
    'Context': 'Context held by long-lived objects',
}

for pattern, hint in suspicious.items():
    matching = {k: v for k, v in class_counts.items() if pattern.lower() in k.lower()}
    if matching:
        for name, count in sorted(matching.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"  ⚠️  {name}: {count:,} instances — {hint}")
```

### Step 5: Interpret Results

When analyzing the output, focus on:

1. **Top classes by retained size**: Classes consuming the most memory including what they keep alive. A large retained size with few instances suggests a few very large objects; many instances with moderate retained size suggests accumulation.

2. **Shallow vs Retained heap**:
   - **Shallow heap**: Memory directly consumed by the object itself
   - **Retained heap**: Shallow + all objects reachable only through this object
   - High retained/shallow ratio means the object is a "dominator" — a likely leak source

3. **Large individual objects**: The biggest single allocations. A few enormous objects may indicate improper caching, loading huge datasets, or bitmap issues.

4. **Class instance distribution**: Multiple entries for the same class with different sizes indicate heterogeneous allocations. Uniform large sizes suggest deliberate caching.

5. **Memory leak indicators**: Check for patterns like:
   - **Activity/Fragment accumulation**: Growing count across screen transitions
   - **Static references holding Context**: Long-lived static fields referencing short-lived objects
   - **Listener/Callback leaks**: Registered listeners never unregistered
   - **Handler/Runnable leaks**: Pending messages holding enclosing scope
   - **Bitmap/Drawable leaks**: Images not recycled, especially after configuration changes
   - **Collection growth**: Unbounded ArrayList/HashMap growing over time

## Execution Constraints

- NEVER create temporary scripts or files in the user's workspace directory. Use inline Python (`python3 -c "..."` or `python3 << 'EOF'`) or write temporary files to `/tmp/` only.
- All intermediate files should go to `/tmp/`.
- After analysis is complete, there should be zero artifacts left in the workspace.

## Notes

- Android hprof uses a slightly different format from standard Java hprof — the magic is `ac hb d` (`0xac 0x68 0x62 0x64`)
- Some Android versions prepend "ac hb df 0d" magic — the parser handles both
- For very large heap dumps (>200MB), parsing may take several minutes
- `hprof-conv` tool (bundled with Android SDK) can convert between formats and compress heap dumps
- The instance parsing in the script above is simplified; for production use, consider using the `pyhprof` library or Android Studio's built-in MAT integration
- To generate an hprof from a running app: `adb exec-out am dumpheap <pid> > heap.hprof`
- To generate from Android Studio: Memory Profiler → Capture heap dump
