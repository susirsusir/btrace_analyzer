#!/usr/bin/env python3
"""hprof_to_parquet.class_names — Resolve class_serial to class_name mappings.

In hprof-libs format, classes are referenced by numeric serial numbers that
must be mapped to actual class names (e.g., "com.example.MyClass"). This module
provides utilities to extract these mappings from CHUNK_HEADER segments and
STRING_DUMP tables.
"""

from typing import Dict


class ClassNamesResolver:
    """Resolves class serial numbers to full class names."""

    def __init__(self):
        self._mapping: Dict[int, str] = {}  # class_serial -> class_name
        self._string_map: Dict[int, str] = {}  # string_id -> text (from STRING_DUMP)

    def set_strings(self, string_map: Dict[int, str]) -> None:
        """Set the string ID → text mapping from STRING_DUMP chunks."""
        self._string_map = string_map

    def add_mapping(self, class_serial: int, class_name: str) -> None:
        """Add an explicit class name mapping."""
        self._mapping[class_serial] = class_name

    def parse_from_header(self, payload: bytes) -> int:
        """
        Parse CHUNK_HEADER / CHAIN_INSTANCE segment for class name mappings.

        Returns the number of new mappings added.

        The header may contain serialized class definitions where a class_serial
        is followed by a string reference. We attempt to extract these pairs.
        """
        count = 0

        # Look for pattern: class_serial(1B) + string_id(4B) or similar
        p = 0
        while p + 5 < len(payload):
            class_serial = payload[p]
            if 28 <= class_serial <= 255 and class_serial not in self._mapping:
                # Try to read potential string ID following the serial
                if p + 5 <= len(payload):
                    string_ref = int.from_bytes(payload[p+1:p+5], 'little')
                    # If this looks like a valid string ID, note it
                    if string_ref > 1000000:
                        self._mapping[class_serial] = f'Class_{class_serial}_ref_{string_ref}'
                        count += 1
            p += 1

        return count

    def get_name(self, class_serial: int) -> str:
        """Get the class name for a given serial number."""
        if class_serial in self._mapping:
            return self._mapping[class_serial]

        # Try to infer from string table
        for sid, txt in self._string_map.items():
            if str(class_serial) in txt or txt.endswith(f'_{class_serial}'):
                self._mapping[class_serial] = txt
                return txt

        # Default fallback
        return f'class_{class_serial}'

    def get_name_extended(self, class_serial: int, obj_id: int = None) -> str:
        """Get extended class name including object context if available."""
        base_name = self.get_name(class_serial)
        if obj_id:
            return f"{base_name}[obj_id={obj_id}]"
        return base_name

    def size(self) -> int:
        """Number of resolved class name mappings."""
        return len(self._mapping)
