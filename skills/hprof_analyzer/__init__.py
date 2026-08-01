"""hprof-analyzer skill — Android HPROF heap dump analysis tool.

Converts HPROF files to Parquet and performs DuckDB-based memory analysis
to detect leaks, analyze object distribution, and generate comprehensive reports.

Main entry point: analyze_hprof() in the analyzer module.
"""

from .analyzer import analyze_hprof

__all__ = [
    "analyze_hprof",
]
