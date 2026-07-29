#!/usr/bin/env python3
"""hprof_to_parquet.formatters — Format and convert HPROF type codes.

Provides utilities to convert raw HPROF type codes (single-byte values)
into human-readable string representations, and to format array element types.
"""


def format_type_code(type_code: int) -> str:
    """
    Convert an HPROF type code to a descriptive string.

    Type codes from the hprof-libs format:
      0x02 = object with instances
      0x08, 0x10, 0x18 = array variants
      0x0A, 0x0B = class with no instances / static
      0x20-0xF8 = object type encoding
      0xFF = special/unknown
    """
    if type_code == 0xFF:
        return 'unknown'
    elif type_code in (0x0A, 0x0B):
        return 'class_no_instances' if type_code == 0x0A else 'static_class'
    elif type_code >= 0x20 and type_code <= 0xF8:
        # Object or array type - encode as object/array
        if (type_code & 0x01) == 0:
            return f'object_{type_code:02X}'
        return f'array_{type_code:02X}'
    elif type_code == 0x02:
        return 'object_with_instances'
    else:
        return f'type_{type_code:02X}'


def format_primitive_array_type(type_code: int) -> str:
    """Format a primitive array type code to a Python type name."""
    mapping = {
        0x0B: 'boolean',
        0x0C: 'byte',
        0x0D: 'char',
        0x0E: 'short',
        0x0F: 'int',
        0x10: 'long',
        0x11: 'float',
        0x12: 'double',
        0x7F: 'object',
    }
    return mapping.get(type_code, f'primitive_{type_code:02X}')


def is_valid_type_code(type_code: int) -> bool:
    """Check if a type code is valid for CLASS_DUMP/OBJECT_DUMP entries."""
    valid = {
        0x02, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x10, 0x18,
        0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58,
        0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98,
        0xA0, 0xA8, 0xB0, 0xB8, 0xC0, 0xC8, 0xD0, 0xD8,
        0xE0, 0xE8, 0xF0, 0xF8, 0xFF,
    }
    return type_code in valid
