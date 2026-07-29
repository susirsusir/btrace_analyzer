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

# Filler chunk tags to skip in hprof-libs
FILLER_TAGS = {0x0000, 0x3F3F}


class ChunkScanner:
    """Scans through hprof-libs file and yields valid chunks."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file_size = os.path.getsize(filepath)

    def scan(self) -> List[Tuple[int, int, int]]:
        """
        Return list of (pos, tag, length) tuples for all valid chunks.
        Chunks start at fixed offset 0x80 in hprof-libs format.
        """
        chunks = []
        pos = 0x80  # Fixed chunk stream start in hprof-libs

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
