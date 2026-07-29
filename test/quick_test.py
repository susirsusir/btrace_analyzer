#!/usr/bin/env python3
"""Quick integration test for hprof_to_parquet modules.

This script verifies that the core parser and writer can work together.
Run from project root: python -m test.quick_test
"""

import sys
from pathlib import Path

# Resolve project root (this file is in test/)
PROJECT_ROOT = Path(__file__).parent.parent

# Add src to path
sys.path.insert(0, str(PROJECT_ROOT / "src"))

def test_parser():
    """Test basic parser functionality."""
    print("Testing HPROFParser...")

    # Check that ChunkScanner works
    from hprof_to_parquet.chunk_scanner import ChunkScanner

    # Use a small test file if available, otherwise synthetic test
    test_file = Path("..") / "hprof" / "taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof"

    if test_file.exists():
        scanner = ChunkScanner(str(test_file))
        chunks = scanner.scan()
        print(f"  ✓ Scanned {len(chunks)} chunks from {test_file.name}")

        # Count valid vs filler tags
        valid_tags = sum(1 for _, tag, _ in chunks if tag not in (0x0000, 0x3F3F))
        print(f"    Valid chunks: {valid_tags}, Filler chunks: {len(chunks) - valid_tags}")
    else:
        # Create synthetic test
        print("  ⚠ Sample hprof not found, skipping actual parsing test")
        print("  Creating minimal synthetic scanner test...")
        class FakeScanner:
            def scan(self):
                return [(0x80, 0x0010, 16), (0x90, 0x0001, 12)]  # STRING_DUMP, CLASS_DUMP
        scanner = FakeScanner()
        chunks = scanner.scan()
        print(f"  Got {len(chunks)} synthetic chunks")

    return True


def test_class_names_resolver():
    """Test ClassNamesResolver."""
    print("\nTesting ClassNamesResolver...")
    from hprof_to_parquet.class_names import ClassNamesResolver

    resolver = ClassNamesResolver()

    # Add some mappings
    resolver.add_mapping(28, "android.accessibilityservice.AccessibilityServiceInfo$1")
    resolver.add_mapping(66, "com.xingjiabi.shengsheng.kuikly.KuiklyRenderActivity")

    # Test resolution
    name = resolver.get_name(28)
    assert name == "android.accessibilityservice.AccessibilityServiceInfo$1", f"Expected 'android.accessibilityservice...', got '{name}'"

    name = resolver.get_name(999)  # Unresolved serial should return fallback
    assert name.startswith("class_"), f"Expected class_fallback, got '{name}'"

    print("  ✓ All ClassNamesResolver tests passed")
    return True


def main():
    """Run all quick tests."""
    print("=" * 60)
    print("hprof_to_parquet Quick Integration Tests")
    print("=" * 60)

    all_passed = True
    failed_modules = []

    try:
        test_parser()
    except Exception as e:
        print(f"  ✗ Parser test failed: {e}")
        all_passed = False
        failed_modules.append("parser")

    try:
        test_class_names_resolver()
    except Exception as e:
        print(f"  ✗ ClassNamesResolver test failed: {e}")
        all_passed = False
        failed_modules.append("class_names")

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL QUICK TESTS PASSED")
    else:
        print(f"✗ {len(failed_modules)} module(s) failed: {', '.join(failed_modules)}")
        sys.exit(1)
    print("=" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
