"""hprof-analyzer skill — Android HPROF heap dump analysis tool.

This skill provides two parallel analysis paths for hprof files:
- Path A: Parquet/DuckDB (structured data from HeapDumpStarDiver)
- Path B: Binary direct parsing (pure Python hprof-libs parser)

Main entry point: analyze_hprof() in the analyzer module.
"""

from .analyzer import analyze_hprof

__all__ = [
    "analyze_hprof",
]
