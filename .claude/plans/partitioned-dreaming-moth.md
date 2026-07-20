# Plan: Fix Binary hprof-libs Parsers — Revised

## Context

End-to-end verification against `hprof/taqu_android_client_logfile_*.hprof` (159MB). Parquet files provide ground truth.

**Current status after v3 fixes:**
| Metric | Expected | Current | Gap |
|--------|----------|---------|-----|
| Strings | ~460+ | 106,490 | ✅ Fixed |
| Classes | 27,182 | 78 | ❌ 99.7% |
| Objects | 3,267,296 | 1,314 | ❌ 99.96% |
| GC Roots | 188,505 | 50 | ❌ 99.97% |
| Threads | 221 | 9 | ❌ 95.9% |

**Critical discovery**: ZERO_PAD chunks contain STRING_DUMP-like data. This was the biggest win.

---

## Step 1: Fix CLASS_DUMP Parser — Remove Overly Restrictive Filters

**File**: `skills/hprof-analyzer/SKILL.md`, Step 5

**Bug**: My filter `entry_len <= 20` rejects valid entries. Looking at actual data:
- @0xbb30b4: 979 markers, most entries are 5-13 bytes ✓
- @0x10c7786: 626 markers, entries include 5, 7, 9, 11, 13, 16, 52, 83, 166+ bytes
- @0x4c018f4: 22 markers, entries are 4090-65535 bytes (class metadata blocks)

The issue: I'm filtering out entries with `entry_len > 20`, which excludes many valid entries in chunks like @0x10c7786.

**Fix**: 
1. Remove the `entry_len <= 20` filter
2. Keep validation on `inst < 1000` and valid type_code
3. For chunks with ONLY large entries (>1000 bytes), skip those entries (they're metadata blocks)
4. Parse ALL entries between markers, accepting reasonable values

**Expected**: Classes increase from 78 → thousands.

---

## Step 2: Fix OBJECT_DUMP Parser

**File**: `skills/hprof-analyzer/SKILL.md`, Step 7

**Same bug as CLASS_DUMP**: Overly restrictive filters.

**Actual format**: Marker-based, entries are 5-160+ bytes. Object IDs are in the entry data.

**Fix**:
1. Remove entry length filter
2. Extract object_id from first 4 bytes of entry
3. Validate obj_id > 0 and class_serial is reasonable

**Expected**: Objects increase from 1,314 → hundreds of thousands.

---

## Step 3: Fix THREAD_SUSPEND Parser — Add Second Pattern

**File**: `skills/hprof-analyzer/SKILL.md`, Step 8.5

**Current**: Only finds `0A 7F XX` pattern. Some chunks use `0A 02 06` pattern.

**Evidence**: 
- @0x96080a: `0a 7f 13` pattern
- @0x844b8f8: `0a 02 06` pattern

**Fix**: Add second pattern detection with relaxed validation.

**Expected**: Threads increase from 9 → ~221.

---

## Step 4: Investigate SAMPLE_GC_HEAP Root Data Location

**File**: `skills/hprof-analyzer/SKILL.md`, Step 8

**Problem**: 29 SAMPLE_GC_HEAP chunks × 63B = 1.8KB. Cannot contain 188K roots.

**Hypothesis**: GC root data is embedded in ZERO_PAD chunks (which we now know contain string data).

**Investigation**:
1. Use Parquet ground truth to get sample `obj_id` values
2. Search entire binary for those obj_ids
3. Check if they appear in ZERO_PAD payloads at regular intervals
4. Look for repeating 20-byte patterns in ZERO_PAD chunks

**If confirmed**: Add GC root parser that scans ZERO_PAD payloads.

---

## Step 5: Re-run End-to-End Verification

After fixes, re-run `/tmp/hprof_analysis/verify_binary_path_v3.py` and compare:

| Metric | Target |
|--------|--------|
| Classes | >5,000 |
| Objects | >100,000 |
| Threads | >150 |
| GC Roots | >10,000 |
| Reference chains | Meaningful stack traces |

Save results to `/tmp/hprof_analysis/verification_result.md`.

---

## Step 6: Update SKILL.md Code

Apply all fixes back into `skills/hprof-analyzer/SKILL.md`:
- Step 4: Include ZERO_PAD in string parsing (already done)
- Step 5: Remove overly restrictive entry length filter
- Step 7: Remove overly restrictive entry length filter
- Step 8: Add GC root parser for ZERO_PAD chunks (if confirmed)
- Step 8.5: Add second THREAD_SUSPEND pattern

---

## Verification

1. Run `python3 /tmp/hprof_analysis/verify_binary_path_v3.py` — confirm improved counts
2. Cross-check class names against Parquet `_class_hierarchy.parquet`
3. Cross-check GC root types against Parquet `_gc_roots_chunk0.parquet`
4. Verify reference chains produce readable stack traces
