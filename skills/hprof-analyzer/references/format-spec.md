# Android hprof-libs Format Specification

This document describes the Android hprof-libs format used by modern Android devices (Android 7.0+).

## File Structure

```
[0-15]:     Magic "JAVA PROFILE 1.0.3\0"
[16-19]:    Stated header size (uint32 LE)
[20-N]:     Extended header data (N-20 bytes)
[0x80+]:    Chunk stream (tag(2B LE) + length(2B LE) + payload)
```

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
```
class_serial:   uint32 LE
pad:            uint32 LE
num_instances:  uint32 LE
pad:            uint32 LE
load_data_chunk_id: uint32 LE
pad:            uint32 LE
class_loader_info:  uint32 LE
pad:            uint32 LE
static_field_data:  variable (field_offset(4B) + class_serial(4B) pairs)
```

### STRING_DUMP (0x0010)
```
string_id:      uint32 LE
string_length:  uint16 LE
string_data:    string_length bytes of UTF-8 text
(next entry follows immediately)
```

### OBJECT_DUMP (0x0004)
```
object_id:      uint32 LE
class_serial:   uint32 LE
pad:            uint32 LE
field_data:     variable (depends on class layout)
```

### SAMPLE_GC_HEAP (0x0005)
```
root_kind:      uint32 LE (0-10)
root_info:      uint32 LE (context-specific)
object_id:      uint32 LE (reachable object)
(next entry follows immediately, 12 bytes per entry)
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

### LOAD_DATA (0x0011)
```
class_serial:   uint32 LE
object_id:      uint32 LE
field_data:     variable
```

Field data format depends on context:
- For class definitions: `field_name + 0x01 + 7_zero_bytes + string_length(1B) + pointer(4B)`
- For instance data: `field_offset(2B) + type_code(1B) + value(variable)`

### CHAIN_INSTANCE (0x0019)
```
Contains Kotlin synthetic field metadata and class chain information.
Entries: field_name + 0x01 + 8_bytes_metadata
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
