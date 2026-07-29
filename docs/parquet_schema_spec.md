# Parquet Schema Specification

Generated from HeapDumpStarDiver output analysis.

## `_object_index`

| Column | Type | Description |
|--------|------|-------------|
| obj_id | int/str | Generated from HPROF chunk |
| class_id | int/str | Generated from HPROF chunk |
| class_name | int/str | Generated from HPROF chunk |
| shallow_size | int/str | Generated from HPROF chunk |

## `_class_hierarchy`

| Column | Type | Description |
|--------|------|-------------|
| class_id | int/str | Generated from HPROF chunk |
| super_class_id | int/str | Generated from HPROF chunk |
| class_name | int/str | Generated from HPROF chunk |
| num_instances | int/str | Generated from HPROF chunk |

## `_gc_roots`

| Column | Type | Description |
|--------|------|-------------|
| root_id | int/str | Generated from HPROF chunk |
| root_type | int/str | Generated from HPROF chunk |
| thread_id | int/str | Generated from HPROF chunk |
| object_id | int/str | Generated from HPROF chunk |

## `_thread_stacks`

| Column | Type | Description |
|--------|------|-------------|
| thread_id | int/str | Generated from HPROF chunk |
| thread_name | int/str | Generated from HPROF chunk |
| suspend_type | int/str | Generated from HPROF chunk |
| frame_ids | int/str | Generated from HPROF chunk |

## `_stack_frames`

| Column | Type | Description |
|--------|------|-------------|
| frame_id | int/str | Generated from HPROF chunk |
| class_id | int/str | Generated from HPROF chunk |
| class_name | int/str | Generated from HPROF chunk |
| method_name | int/str | Generated from HPROF chunk |
| line_number | int/str | Generated from HPROF chunk |

## `_java_strings`

| Column | Type | Description |
|--------|------|-------------|
| string_id | int/str | Generated from HPROF chunk |
| value | int/str | Generated from HPROF chunk |
| length | int/str | Generated from HPROF chunk |

