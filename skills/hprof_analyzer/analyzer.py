#!/usr/bin/env python3
"""hprof_analyzer.analyzer — Main entry point for the hprof-analyzer skill.

Converts HPROF files to Parquet and performs Parquet/DuckDB analysis
to generate a comprehensive memory analysis report.

Usage example:
    from skills.hprof_analyzer.analyzer import analyze_hprof

    result = analyze_hprof(
        hprof_name="taqu_android_client_logfile_401..."  # Just filename (no extension!)
    )
"""

import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

# Relative imports within the skill package
from .lib.parser import HPROFParser
from .lib.writer import convert_hprof_to_parquet


def ensure_parquet(hprof_path: str, output_dir: str) -> Dict[str, int]:
    """Convert HPROF to Parquet. Always performs conversion to ensure fresh data."""
    print(f"Converting HPROF to Parquet...")

    # Parse HPROF using the library parser
    parser = HPROFParser(hprof_path)
    data = parser.parse_all()

    # Write Parquet using the library writer
    counts = convert_hprof_to_parquet(hprof_path, output_dir)

    print(f"✓ Conversion complete. Parquet files written to {output_dir}")
    print(f"  Parsed: {counts['objects']} objects, {counts['classes']} classes, {counts['strings']} strings")

    return counts


def get_timestamp_suffix() -> str:
    """Generate timestamp suffix for report filenames (YYYYmmdd_HHMMss)."""
    return time.strftime('%Y%m%d_%H%M%S')


def analyze_hprof(
    hprof_name: str,
    project_root: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Main analysis function — converts HPROF to Parquet and performs DuckDB analysis.

    Args:
        hprof_name: Filename without .hprof extension (e.g., "my_dump")
                    Automatically searched in <project_root>/hprof/
        project_root: Project root (defaults to parent of this file)

    Returns:
        Dictionary containing analysis results with output paths
    """
    # Determine project root (parent of skills/hprof_analyzer)
    if project_root is None:
        project_root = Path(__file__).parents[2]

    # Construct input hprof path
    hprof_path = str(project_root / "hprof" / f"{hprof_name}.hprof")

    # Output structure: hprof_analysis/<basename>/
    basename = hprof_name
    output_base = project_root / "hprof_analysis" / basename
    output_base.mkdir(parents=True, exist_ok=True)  # Create parent dir

    # Parquet subdirectory inside output_base
    parquet_dir = str(output_base / "parquet")
    os.makedirs(parquet_dir, exist_ok=True)

    # Always convert HPROF to Parquet
    counts = ensure_parquet(hprof_path, parquet_dir)

    result = {
        'input_hprof': hprof_path,
        'hprof_name': basename,
        'output_base': str(output_base),
        'parquet_dir': parquet_dir,
        'parquet_counts': counts,
        'status': 'completed',
    }

    # Write Parquet report with timestamp
    timestamp = get_timestamp_suffix()
    report_path = output_base / f"{basename}_report.md"
    with open(report_path, 'w') as f:
        f.write("# HPROF Analysis Report\n\n")
        f.write(f"**Input:** {basename}.hprof\n")
        f.write(f"**Output Parquet Directory:** {parquet_dir}\n")
        f.write(f"**Generated:** {timestamp}\n")
        f.write(f"\n**Summary:** Analysis completed via Parquet/DuckDB path.\n")
        f.write(f"\n**Parsed data:** {counts['objects']} objects, {counts['classes']} classes, {counts['strings']} strings\n")
    print(f"  ✓ Report saved to {report_path}")

    result['report_path'] = str(report_path)
    result['report_filename'] = f"{basename}_report.md"

    print(f"\n✅ Analysis completed successfully!")
    print(f"📁 Output location: {output_base}")

    return result


if __name__ == '__main__':
    # Example usage when run as a script
    import sys
    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <hprof_filename_without_extension>")
        print("Example: python analyzer.py taqu_android_client_logfile_401")
        sys.exit(1)

    hprof_file = sys.argv[1]

    result = analyze_hprof(hprof_file)
    print(f"\n📊 Final output directory: {result['output_base']}")
    print(f"📄 Report file: {result['report_path']}")
