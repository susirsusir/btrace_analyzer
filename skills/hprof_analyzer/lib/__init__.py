"""hprof_analyzer.lib — Core library modules for HPROF parsing and Parquet conversion.

This package contains the low-level components used by the hprof-analyzer skill.
Public API:
  - HPROFParser: Main parser class for hprof-libs format
  - convert_hprof_to_parquet: Entry point for converting HPROF to Parquet
"""

from .parser import HPROFParser
from .writer import convert_hprof_to_parquet

__all__ = [
    "HPROFParser",
    "convert_hprof_to_parquet",
]
