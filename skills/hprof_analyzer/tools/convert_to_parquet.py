#!/usr/bin/env python3
"""convert_to_parquet — CLI tool for HPROF to Parquet conversion.

This is a wrapper around the library modules in skills.hprof_analyzer.lib.
Usage:
    python3 convert_to_parquet.py <hprof_file> <output_dir> [--shard-size N]
"""

import sys
from pathlib import Path

# Add skills to path
sys.path.insert(0, str(Path(__file__).parents[1].parent))

from lib.parser import HPROFParser
from lib.writer import convert_hprof_to_parquet as main_convert

def main():
    if len(sys.argv) < 3:
        print("Usage: convert_to_parquet.py <hprof_file> <output_dir> [--shard-size N]")
        sys.exit(1)
    
    hprof_path = sys.argv[1]
    output_dir = sys.argv[2]
    shard_size = 50000
    if len(sys.argv) > 4 and sys.argv[3] == '--shard-size':
        shard_size = int(sys.argv[4])
    
    counts = main_convert(hprof_path, output_dir, shard_size=shard_size)
    print(f"Conversion complete! Object count: {counts['objects']}")

if __name__ == '__main__':
    main()
