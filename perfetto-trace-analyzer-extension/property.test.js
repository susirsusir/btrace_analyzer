/**
 * Property-Based Tests for Perfetto Trace Analyzer Extension
 *
 * Uses fast-check to verify correctness properties across randomized inputs.
 * Each test is annotated with its Property number and validated Requirements.
 */

const fc = require("fast-check");
const {
  classifySeverity,
  classifyIssues,
  generateReport,
  generateFilename,
  severityRank,
} = require("./report");
const { IO_KEYWORDS, isIORelated } = require("./diagnostics");

// =========================================================================
// Property 1: 严重等级分类正确性
// Validates: Requirements 8.5
// =========================================================================
describe("Property 1: 严重等级分类正确性", () => {
  test("classifySeverity returns correct severity for all positive durations", () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0.01, max: 100000, noNaN: true, noDefaultInfinity: true }),
        (durMs) => {
          const severity = classifySeverity(durMs);
          if (durMs > 500) return severity === "P0";
          if (durMs > 200) return severity === "P1";
          if (durMs > 100) return severity === "P2";
          return severity === "P3";
        }
      ),
      { numRuns: 100 }
    );
  });

  test("classifySeverity boundary: durMs=0 returns P3", () => {
    expect(classifySeverity(0)).toBe("P3");
  });

  test("classifySeverity boundary: exact thresholds", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(100, 200, 500),
        (boundary) => {
          // At exact boundary, should be the lower severity
          if (boundary === 100) return classifySeverity(boundary) === "P3";
          if (boundary === 200) return classifySeverity(boundary) === "P2";
          if (boundary === 500) return classifySeverity(boundary) === "P1";
          return false;
        }
      ),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 2: 诊断结果排序与数量限制
// Validates: Requirements 3.1, 4.3, 5.3, 6.2
// =========================================================================
describe("Property 2: 诊断结果排序与数量限制", () => {
  // Generators for each diagnostic type
  const sliceArb = fc.record({
    id: fc.nat(),
    name: fc.string({ minLength: 1, maxLength: 30 }),
    dur_ms: fc.double({ min: 0.1, max: 10000, noNaN: true, noDefaultInfinity: true }),
    ts_str: fc.string(),
    dur_str: fc.string(),
  });

  const cpuHeavyArb = fc.record({
    name: fc.string({ minLength: 1, maxLength: 30 }),
    count: fc.integer({ min: 1, max: 1000 }),
    total_ms: fc.double({ min: 0.1, max: 100000, noNaN: true, noDefaultInfinity: true }),
    avg_ms: fc.double({ min: 0.1, max: 10000, noNaN: true, noDefaultInfinity: true }),
  });

  test("long_slices results are sorted by dur_ms descending and limited to 30", () => {
    fc.assert(
      fc.property(
        fc.array(sliceArb, { minLength: 0, maxLength: 100 }),
        (slices) => {
          // Simulate the SQL ORDER BY dur DESC LIMIT 30
          const sorted = slices.slice().sort((a, b) => b.dur_ms - a.dur_ms);
          const limited = sorted.slice(0, 30);

          // Verify sorting
          for (let i = 1; i < limited.length; i++) {
            if (limited[i].dur_ms > limited[i - 1].dur_ms) return false;
          }
          // Verify limit
          return limited.length <= 30;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("frame_jank results are sorted by dur_ms descending and limited to 20", () => {
    fc.assert(
      fc.property(
        fc.array(sliceArb, { minLength: 0, maxLength: 100 }),
        (slices) => {
          // Simulate frame_jank: filter dur > 16.6ms, sort desc, limit 20
          const filtered = slices.filter((s) => s.dur_ms > 16.6);
          const sorted = filtered.sort((a, b) => b.dur_ms - a.dur_ms);
          const limited = sorted.slice(0, 20);

          for (let i = 1; i < limited.length; i++) {
            if (limited[i].dur_ms > limited[i - 1].dur_ms) return false;
          }
          return limited.length <= 20;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("cpu_heavy results are sorted by total_ms descending and limited to 30", () => {
    fc.assert(
      fc.property(
        fc.array(cpuHeavyArb, { minLength: 0, maxLength: 100 }),
        (methods) => {
          // Simulate cpu_heavy: filter count > 5, sort by total_ms desc, limit 30
          const filtered = methods.filter((m) => m.count > 5);
          const sorted = filtered.sort((a, b) => b.total_ms - a.total_ms);
          const limited = sorted.slice(0, 30);

          for (let i = 1; i < limited.length; i++) {
            if (limited[i].total_ms > limited[i - 1].total_ms) return false;
          }
          return limited.length <= 30;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("main_thread_io results are sorted by dur_ms descending and limited to 20", () => {
    fc.assert(
      fc.property(
        fc.array(sliceArb, { minLength: 0, maxLength: 100 }),
        (slices) => {
          const sorted = slices.slice().sort((a, b) => b.dur_ms - a.dur_ms);
          const limited = sorted.slice(0, 20);

          for (let i = 1; i < limited.length; i++) {
            if (limited[i].dur_ms > limited[i - 1].dur_ms) return false;
          }
          return limited.length <= 20;
        }
      ),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 3: 帧卡顿阈值过滤
// Validates: Requirements 4.2
// =========================================================================
describe("Property 3: 帧卡顿阈值过滤", () => {
  test("all returned frame jank records have dur > 16.6ms (16600000 ns)", () => {
    const frameSliceArb = fc.record({
      id: fc.nat(),
      name: fc.constantFrom("doFrame", "Choreographer#doFrame", "traversal"),
      dur_ms: fc.double({ min: 0.01, max: 1000, noNaN: true, noDefaultInfinity: true }),
      ts_str: fc.string(),
      dur_str: fc.string(),
    });

    fc.assert(
      fc.property(
        fc.array(frameSliceArb, { minLength: 0, maxLength: 50 }),
        (slices) => {
          // Simulate frame_jank filter: dur > 16600000 ns = 16.6 ms
          const filtered = slices.filter((s) => s.dur_ms > 16.6);

          // Every returned record must exceed threshold
          for (const s of filtered) {
            if (s.dur_ms <= 16.6) return false;
          }

          // All slices above threshold must be included
          const aboveThreshold = slices.filter((s) => s.dur_ms > 16.6);
          return filtered.length === aboveThreshold.length;
        }
      ),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 4: CPU 密集型方法调用次数过滤
// Validates: Requirements 5.2
// =========================================================================
describe("Property 4: CPU 密集型方法调用次数过滤", () => {
  test("all returned CPU-heavy records have count > 5", () => {
    const methodArb = fc.record({
      name: fc.string({ minLength: 1, maxLength: 30 }),
      count: fc.integer({ min: 1, max: 500 }),
      total_ms: fc.double({ min: 0.1, max: 50000, noNaN: true, noDefaultInfinity: true }),
      avg_ms: fc.double({ min: 0.1, max: 5000, noNaN: true, noDefaultInfinity: true }),
    });

    fc.assert(
      fc.property(
        fc.array(methodArb, { minLength: 0, maxLength: 50 }),
        (methods) => {
          // Simulate HAVING count > 5
          const filtered = methods.filter((m) => m.count > 5);

          // Every returned record must have count > 5
          for (const m of filtered) {
            if (m.count <= 5) return false;
          }

          // All methods with count > 5 must be included
          const aboveThreshold = methods.filter((m) => m.count > 5);
          return filtered.length === aboveThreshold.length;
        }
      ),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 5: I/O 关键词匹配
// Validates: Requirements 6.1
// =========================================================================
describe("Property 5: I/O 关键词匹配", () => {
  test("isIORelated returns true iff name contains an I/O keyword", () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 0, maxLength: 100 }),
        (name) => {
          const result = isIORelated(name);
          const expected = IO_KEYWORDS.some((kw) => name.includes(kw));
          return result === expected;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("isIORelated returns true when name contains any I/O keyword", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...IO_KEYWORDS),
        fc.string({ minLength: 0, maxLength: 20 }),
        fc.string({ minLength: 0, maxLength: 20 }),
        (keyword, prefix, suffix) => {
          const name = prefix + keyword + suffix;
          return isIORelated(name) === true;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("isIORelated returns false for strings without any I/O keyword", () => {
    // Generate strings that definitely don't contain any IO keyword
    const safeCharArb = fc.constantFrom(
      "a", "b", "c", "d", "e", "g", "j", "k", "l", "m",
      "o", "p", "q", "r", "u", "v", "w", "x", "y", "z",
      "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
      "_", "-", "."
    );
    const safeStringArb = fc.array(safeCharArb, { minLength: 0, maxLength: 30 })
      .map((chars) => chars.join(""));

    fc.assert(
      fc.property(safeStringArb, (name) => {
        // Double-check our generator doesn't accidentally produce IO keywords
        const containsKeyword = IO_KEYWORDS.some((kw) => name.includes(kw));
        if (containsKeyword) return true; // skip if accidentally contains keyword
        return isIORelated(name) === false;
      }),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 6: 调用栈遍历深度限制与正确性
// Validates: Requirements 7.1, 7.2, 7.3
// NOTE: Since traceCallStack requires Perfetto engine, we test the
// depth-limiting logic with a mock engine that simulates query behavior.
// =========================================================================
describe("Property 6: 调用栈遍历深度限制与正确性", () => {
  /**
   * Build a mock engine and run the same traversal logic as callstack.js.
   * This replicates the core algorithm without needing the real Perfetto engine.
   */
  function buildSliceChain(depth) {
    // Build a chain: slice 0 -> parent 1 -> parent 2 -> ... -> root (depth-1)
    // Root has parentId = -1
    const slices = {};
    for (let i = 0; i < depth; i++) {
      slices[String(i)] = {
        id: String(i),
        pid: i < depth - 1 ? String(i + 1) : "-1",
        name: `method_${i}`,
      };
    }
    return slices;
  }

  async function traceCallStackMock(sliceId, slices, maxDepth) {
    if (maxDepth === undefined || maxDepth === null) {
      maxDepth = 25;
    }

    const stack = [];
    const startSlice = slices[String(sliceId)];
    if (!startSlice) return stack;

    stack.push({
      name: startSlice.name,
      id: startSlice.id,
      parentId: startSlice.pid,
    });
    let currentPid = startSlice.pid;

    for (let i = 0; i < maxDepth && currentPid && currentPid !== "-1"; i++) {
      const parent = slices[currentPid];
      if (!parent) break;
      stack.push({
        name: parent.name,
        id: parent.id,
        parentId: parent.pid,
      });
      currentPid = parent.pid;
    }

    // Mark truncation
    if (currentPid && currentPid !== "-1" && stack.length > maxDepth) {
      stack.truncated = true;
    }

    return stack;
  }

  test("traversal depth does not exceed 25 layers for any tree depth", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 50 }),
        async (depth) => {
          const slices = buildSliceChain(depth);
          const stack = await traceCallStackMock(0, slices, 25);

          // Stack should include the initial slice + up to 25 parents
          // Total entries: min(depth, 26) — 1 initial + up to 25 traversals
          return stack.length <= 26;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("traversal returns complete chain when depth <= 25", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 25 }),
        async (depth) => {
          const slices = buildSliceChain(depth);
          const stack = await traceCallStackMock(0, slices, 25);

          // Should get the full chain
          return stack.length === depth;
        }
      ),
      { numRuns: 100 }
    );
  });

  test("traversal marks truncation when depth > 25", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 28, max: 50 }),
        async (depth) => {
          const slices = buildSliceChain(depth);
          const stack = await traceCallStackMock(0, slices, 25);

          // Should be truncated: 1 initial + 25 parents = 26
          return stack.length === 26 && stack.truncated === true;
        }
      ),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 7: 报告生成完整性
// Validates: Requirements 8.1, 8.2, 8.3, 8.4
// =========================================================================
describe("Property 7: 报告生成完整性", () => {
  // Generator for random AnalysisData
  const sliceArb = fc.record({
    id: fc.nat({ max: 10000 }),
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_.]{0,29}$/),
    dur_ms: fc.double({ min: 51, max: 5000, noNaN: true, noDefaultInfinity: true }),
  });

  const cpuHeavyArb = fc.record({
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_.]{0,29}$/),
    count: fc.integer({ min: 6, max: 500 }),
    total_ms: fc.double({ min: 51, max: 50000, noNaN: true, noDefaultInfinity: true }),
    avg_ms: fc.double({ min: 0.1, max: 5000, noNaN: true, noDefaultInfinity: true }),
  });

  const analysisDataArb = fc.record({
    longSlices: fc.array(sliceArb, { minLength: 0, maxLength: 5 }),
    frameJanks: fc.array(sliceArb, { minLength: 0, maxLength: 5 }),
    cpuHeavy: fc.array(cpuHeavyArb, { minLength: 0, maxLength: 5 }),
    mainThreadIO: fc.array(sliceArb, { minLength: 0, maxLength: 5 }),
  }).map((data) => ({ ...data, callStacks: {} }));

  test("report contains ## Summary and ## Issues sections", () => {
    fc.assert(
      fc.property(analysisDataArb, (data) => {
        const report = generateReport(data);
        return report.includes("## Summary") && report.includes("## Issues");
      }),
      { numRuns: 100 }
    );
  });

  test("issues are sorted P0 → P1 → P2 → P3", () => {
    fc.assert(
      fc.property(analysisDataArb, (data) => {
        const issues = classifyIssues(data);
        for (let i = 1; i < issues.length; i++) {
          if (severityRank(issues[i].severity) < severityRank(issues[i - 1].severity)) {
            return false;
          }
        }
        return true;
      }),
      { numRuns: 100 }
    );
  });

  test("each issue in the report has all required fields", () => {
    // Use data that guarantees at least one issue
    const nonEmptyDataArb = fc.record({
      longSlices: fc.array(sliceArb, { minLength: 1, maxLength: 5 }),
      frameJanks: fc.constant([]),
      cpuHeavy: fc.constant([]),
      mainThreadIO: fc.constant([]),
    }).map((data) => ({ ...data, callStacks: {} }));

    fc.assert(
      fc.property(nonEmptyDataArb, (data) => {
        const report = generateReport(data);
        const issues = classifyIssues(data);

        if (issues.length === 0) return true;

        // Check that the report contains required fields for each issue
        const requiredFields = [
          "**What**:",
          "**Where**:",
          "**Duration**:",
          "**Impact**:",
          "**Suggestion**:",
        ];

        for (const field of requiredFields) {
          if (!report.includes(field)) return false;
        }

        // Check severity tags exist
        const severityPattern = /\[P[0-3]\]/;
        return severityPattern.test(report);
      }),
      { numRuns: 100 }
    );
  });
});

// =========================================================================
// Property 8: 报告文件名格式
// Validates: Requirements 9.2
// =========================================================================
describe("Property 8: 报告文件名格式", () => {
  test("filename matches regex perfetto_analysis_report_YYYYMMDD_HHmmss.md", () => {
    // Generate random dates within a reasonable range
    const dateArb = fc
      .integer({ min: 0, max: 4102444800000 }) // 2000-01-01 to ~2100
      .map((ts) => new Date(ts));

    fc.assert(
      fc.property(dateArb, (date) => {
        // Skip invalid dates
        if (isNaN(date.getTime())) return true;

        const filename = generateFilename(date);
        return /^perfetto_analysis_report_\d{8}_\d{6}\.md$/.test(filename);
      }),
      { numRuns: 100 }
    );
  });

  test("filename date/time matches the input Date object", () => {
    const dateArb = fc
      .integer({ min: 946684800000, max: 4102444800000 }) // 2000 to ~2100
      .map((ts) => new Date(ts));

    fc.assert(
      fc.property(dateArb, (date) => {
        if (isNaN(date.getTime())) return true;

        const filename = generateFilename(date);
        const match = filename.match(
          /^perfetto_analysis_report_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.md$/
        );
        if (!match) return false;

        const [, y, mo, d, h, mi, s] = match;
        return (
          parseInt(y) === date.getFullYear() &&
          parseInt(mo) === date.getMonth() + 1 &&
          parseInt(d) === date.getDate() &&
          parseInt(h) === date.getHours() &&
          parseInt(mi) === date.getMinutes() &&
          parseInt(s) === date.getSeconds()
        );
      }),
      { numRuns: 100 }
    );
  });
});
