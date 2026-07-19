# Android hprof-libs Format Specification

This document describes the Android hprof-libs format used by modern Android devices (Android 7.0+).

> **Source**: Reverse-engineered from `taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` (159 MB, Android 7.0+ hprof-libs).
> Verified against known-good data from HeapDumpStarDiver + DuckDB output and Eclipse MAT reference.

## File Structure

```
[0-15]:     Magic "JAVA PROFILE 1.0" (12 bytes) + stated_header_size(4B LE) at offset 16
[16-19]:    Stated header size (uint32 LE), typically 13102+ for hprof-libs
[20-N]:     Extended header data (class metadata, string tables, field definitions)
[0x80+]:    Chunk stream begins at FIXED offset 0x80 (NOT 16 + stated_header_size)
```

**Format detection**: if `stated_header_size > 2000`, use hprof-libs format; otherwise standard hprof-heap.

## Chunk Format

Each chunk in the record stream:

```
tag:      uint16 LE (chunk type)
length:   uint16 LE (total chunk size INCLUDING the 4-byte header)
payload:  length - 4 bytes of chunk data
```

## Chunk Types

| Tag | Name | Description |
|-----|------|-------------|
| 0x0000 | ZERO | Padding / zero-filled data |
| 0x0001 | CLASS_DUMP | Class metadata (serial, instance count, static fields) |
| 0x0002 | STACK_FRAME | Stack frame information |
| 0x0003 | THREAD_SUSPEND | Thread suspension snapshot |
| 0x0004 | OBJECT_DUMP | Object instance data |
| 0x0005 | SAMPLE_GC_HEAP | GC heap sample (roots and reachable objects) |
| 0x0010 | STRING_DUMP | String table entries |
| 0x0011 | LOAD_DATA | Class/instance field data |
| 0x0013 | DUMP_COMPLETED | Dump completion marker |
| 0x0014 | SESSION_START | Session start metadata |
| 0x0015 | SESSION_FINISH | Session end metadata |
| 0x0016 | BUFFER_START | Buffer start marker |
| 0x0017 | BUFFER_END | Buffer end marker |
| 0x0019 | CHAIN_INSTANCE | Chain instance (field metadata) |
| 0x0030 | DYNAMIC_SYSTEM_LIBRARY | Dynamic system library info |
| 0x0031 | STATIC_SYSTEM_LIBRARY | Static system library info |
| 0x0032 | ROM_PING | ROM partition info |

## Chunk Payload Formats

### CLASS_DUMP (0x0001)

Two observed layouts:

**Large CLASS_DUMP** (e.g., @0x56bfd1, len=51989): dense packed entries, no `00 40 00` markers visible in first bytes. Likely a raw array of class metadata records.

**Small CLASS_DUMP** (e.g., @0x8b9381, len=64): marker-based table using `00 40 00 XX` separators. Each entry contains class metadata followed by a marker whose `XX` byte is the NEXT class_serial.

```
Small CLASS_DUMP layout (marker-based table):
  [entry_data(variable)] [00 40 00 XX]

  entry_data contains:
    type_code(1B) — 0x02=has instances, 0x0A=no instances, 0x0B=static/other
    instance_count(1B) — 0xFF means unknown/special
    field_info(variable)

  The XX byte of the marker is the NEXT class_serial (sequential counter).
  A 7-byte header precedes the first entry in each chunk.
```

### STRING_DUMP (0x0010)

```
hprof-libs format (variable length per entry):
  string_text:    variable bytes (printable ASCII, terminated by 0x01)
  separator:      0x01
  zeros:          7 bytes (0x00)
  value:          1 byte (metadata)
  ref:            4 bytes LE (string_id or pointer)
  Total entry:    len(string_text) + 13 bytes
```

**Important**: The first entry in a chunk may have a garbage first byte (carry-over from previous chunk's ref). Validate that text is printable ASCII before accepting.

### OBJECT_DUMP (0x0004)

```
hprof-libs format (marker-based table, same as CLASS_DUMP small layout):
  [entry_data(variable)] [00 40 00 class_serial(1B)]

  entry_data contains:
    object_id(4B LE)
    type_code(1B) — e.g., 0x0B, 0x02, 0xFF
    field_data(variable, often padding or small values)

  Multiple entries per chunk, separated by 00 40 00 XX markers.
  The XX byte is the class_serial (same numbering as CLASS_DUMP).
  Entry size varies: typically 5-9 bytes.
```

**Reverse-engineered example** (@0x5f7d01, len=16384):
- Markers found at positions [7, 20, 33, 46, 59, ...], spacing ~13 bytes
- First entry: `00 F3 02 14 C4 DA 00 00 40` → object_id=0xDA C4 14 02, type=0xF3, marker class_serial=0xF4
- Second entry: `00 F4 0B 00 00 01 9F 4E 97 2B 58 00 40` → object_id=0x2B 58 4E 9F, type=0xF4, marker class_serial=0xF5

### SAMPLE_GC_HEAP (0x0005)

```
hprof-libs format (20 bytes per entry):
  object_id:      uint32 LE (reachable object)
  root_info:      uint32 LE (context-specific)
  root_kind:      uint16 LE (0-10)
  class_serial:   uint32 LE (often constant system class)
  pad:            uint32 LE
  extra:          uint16 LE
```

**Root kinds:**
- 0 = JAVA_STACK
- 1 = NATIVE_STACK
- 2 = SYSTEM_CLASS
- 3 = GC_STATIC_FIELD
- 4 = GC_LOCAL
- 5 = GC_MONITOR
- 6 = GC_JAVA_FRAME
- 7 = GC_NATIVE_FRAME
- 8 = UNREACHABLE
- 9 = DAEMON_WORKER
- 10 = UNKNOWN

**Two observed chunk sizes:**
- Small chunks (~63B): contain 2-3 entries plus prefix/padding. May have a non-standard 4-byte prefix (`3F 21` or `3F 00`) before the first entry.
- Large chunks (up to 34KB): contain ~1700 entries densely packed.

**Verified parsing** (@0x665310, len=63):
```
Prefix: 3F 21 14 9E
Entry 0: obj_id=0x00000024, root_info=0x096F0000, root_kind=30918, class_serial=0x14000000
Entry 1: obj_id=0x096F1024, root_info=0x000078C6, root_kind=0 (JAVA_STACK), class_serial=0x249E1423
```

Note: root_kind values must be validated (<=10) before interpreting. Some chunks are all padding (`3F 00` repeated).

### THREAD_SUSPEND (0x0003)

```
hprof-libs format (marker-based table with 0A 7F 13 markers):
  [entry_data(variable)] [0A 7F 13 counter(1) pad(2: 00 40)]

  entry_data contains:
    thread_obj_id(4B LE) — object ID used to link from SAMPLE_GC_HEAP.root_info when root_kind=JAVA_STACK
    suspend_type(1B) — 0=suspend, 1=yield, etc.

  The marker sequence is 0A 7F 13. The counter byte is a sequential thread index.
  The pad bytes are always 0x00 0x40.
  Entry size is fixed at 9 bytes: obj_id(4) + 0x0A + 0x7F + 0x13 + counter(1) + pad(2).
```

**Reverse-engineered layout** (verified on `taqu_android_client_logfile_*.hprof`):
- The chunk payload begins with a dense table of 9-byte entries separated by `0A 7F 13` markers
- After the marker table, thread metadata follows (variable length, may include thread names as string_id references)
- A single 16KB chunk can contain ~1820 thread entries (16384/9)
- **Thread names are NOT stored inline in THREAD_SUSPEND**. They are resolved via the STRING_DUMP table using string_id lookups.
- Frame IDs referenced by threads are resolved through STACK_FRAME chunks.

**Returns**: dict mapping thread_obj_id -> {
    'name': str,          # resolved via STRING_DUMP string_table
    'suspend_type': int,
    'frame_ids': [int, ...],  # resolved via STACK_FRAME chunks
}

### STACK_FRAME (0x0002)

```
hprof-libs format (marker-based table using 00 40 00 XX separators):
  [entry_data(variable)] [00 40 00 class_serial(1B)]

  entry_data contains:
    frame_id(4B LE) — unique frame identifier
    type_code(1B) — 0x02=has instances, 0x0A=no instances, etc.

  The XX byte of the marker is the NEXT class_serial.
  Marker-based entries are typically 5 bytes each.
```

**Pre-marker block**: The data before the first `00 40 00` marker may contain initial frame records in a different format:
```
frame_id(4B LE) + class_serial(4B LE) + pad(4B LE) + method_index(4B LE) + line_number(4B LE)
```

**Returns**: dict mapping frame_id -> {
    'class_serial': int,
    'class_name': str,       # resolved via string_table
    'method_index': int,
    'method_name': str,      # resolved via string_table
    'line_number': int,
    'type_code': int
}

### LOAD_DATA (0x0011)

```
class_serial:   uint32 LE
object_id:      uint32 LE
field_data:     variable
```

Field data format depends on context:
- For class definitions: `field_name + 0x01 + 7_zero_bytes + string_length(1B) + pointer(4B)`
- For instance data: `field_offset(2B) + type_code(1B) + value(variable)`

**Verified example** (@0x5eb2a3b, len=104):
```
class_serial=0x6FC60900, object_id=0x00000014
field_data: 3F 00 00 00 00 1F 11 00 78 6F 09 C6 78 ...
```

### CHAIN_INSTANCE (0x0019)

```
Contains Kotlin synthetic field metadata and class chain information.
Entries: field_name + 0x01 + 8_bytes_metadata
Example strings: "$this$coroutineScope", "$this$launchWhenResumed", "$this$liveData"
```

## Parsing Strategy

1. Read magic and stated header size from file header
2. If stated_header_size > 2000, use hprof-libs format (chunks at 0x80)
3. Otherwise, use standard hprof-heap format (records at 16 + stated_header_size)
4. Scan chunk stream for valid tags, skipping unknown/padding chunks
5. Parse each chunk type according to its payload format
6. Correlate data across chunk types using serial numbers and object IDs

## Differences from Standard hprof-heap

| Aspect | hprof-heap (standard) | hprof-libs (Android 7+) |
|--------|----------------------|------------------------|
| Record header | 8 bytes (tag 4B + length 4B) | 4 bytes (tag 2B + length 2B) |
| Record start | 16 + stated_header_size | 0x80 (fixed) |
| Endianness | Little-endian | Little-endian |
| Header size | Typically 128 | Typically 13102+ |
| Compression | None | May include compressed sections |
