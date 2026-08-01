#!/usr/bin/env python3
"""hprof_to_parquet.chunk_scanner — Chunk scanning utilities for hprof-libs files.

Provides a simple interface to scan through HPROF files and enumerate valid
chunks, skipping filler data and handling format variations.
"""

import struct
import os
from typing import List, Tuple

# Chunk tags (both hprof-libs 2-byte and hprof-heap 4-byte formats)
TAG_STRING_DUMP = 0x0010
TAG_CLASS_DUMP = 0x0001
TAG_LOAD_DATA = 0x0011
TAG_OBJECT_DUMP = 0x0004
TAG_SAMPLE_GC_HEAP = 0x0005
TAG_THREAD_SUSPEND = 0x0003
TAG_STACK_FRAME = 0x0002
TAG_CHUNK_HEADER = 0x0000
TAG_CHAIN_INSTANCE = 0x0019

# Valid chunk tags for hprof-libs format
VALID_TAGS = {
    0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005,
    0x0010, 0x0011, 0x0013, 0x0014, 0x0015,
    0x0016, 0x0017, 0x0019, 0x0030, 0x0031, 0x0032,
}

# Filler chunk tags to skip in hprof-libs
FILLER_TAGS = {0x0000, 0x3F3F}


class ChunkScanner:
    """Scans through hprof-libs file and yields valid chunks."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)
        self.record_start = self._detect_record_start()

    def _detect_record_start(self) -> int:
        """Detect the actual record start offset from hprof header.

        hprof files start with:
          - 16 bytes: magic "JAVA PROFILE 1.0" + version
          - 4 bytes: stated header size (little-endian uint32)
          - N bytes: header content (stated_size - 8 bytes)
          - Records start at offset 16 + stated_size
        """
        with open(self.filepath, 'rb') as f:
            magic = f.read(16)
            stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        return 16 + stated_size

    def scan(self) -> List[Tuple[int, int, int]]:
        """
        Return list of (pos, tag, length) tuples for all valid chunks.
        Chunks start at record_start (detected from header).
        """
        chunks = []
        pos = self.record_start

        while pos < self.file_size - 4:
            with open(self.filepath, 'rb') as f:
                f.seek(pos)
                header = f.read(4)
                if len(header) < 4:
                    break
                tag, length = struct.unpack_from('<HH', header, 0)

            # Skip filler chunks
            if tag in FILLER_TAGS:
                pos += 4
                continue

            # Validate: must have enough remaining data
            remaining = self.file_size - pos
            if length < 4 or length > remaining:
                pos += 1  # Scan forward by 1 byte to resync
                continue

            chunks.append((pos, tag, length))
            pos += length

        return chunks


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python chunk_scanner.py <hprof_file>")
        sys.exit(1)

    scanner = ChunkScanner(sys.argv[1])
    chunks = scanner.scan()
    print(f"File: {sys.argv[1]}")
    print(f"File size: {scanner.file_size:,} bytes")
    print(f"Record start: 0x{scanner.record_start:X}")
    print(f"Found {len(chunks)} valid chunks")
