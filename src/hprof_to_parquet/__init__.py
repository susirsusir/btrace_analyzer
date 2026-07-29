"""hprof_to_parquet — Pure Python HPROF-libs to Parquet converter.

Replaces the HeapDumpStarDiver binary dependency with a pure Python
implementation for Android hprof (hprof-libs) files.

Supports both standard hprof-heap and Android hprof-libs formats.
"""

__version__ = "0.1.0"
__author__ = "Sapiens AI Team"

from .parser import HPROFParser
from .chunk_scanner import ChunkScanner
from .class_names import ClassNamesResolver
from .formatters import format_type_code, format_primitive_array_type

__all__ = [
    "HPROFParser",
    "ChunkScanner",
    "ClassNamesResolver",
    "format_type_code",
    "format_primitive_array_type",
]
