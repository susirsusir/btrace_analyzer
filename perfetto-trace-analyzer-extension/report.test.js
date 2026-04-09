/**
 * Unit tests for report.js — Markdown report generation
 *
 * Tests: classifySeverity, classifyIssues, formatCallStack,
 *        generateReport, generateFilename
 */

const {
  classifySeverity,
  classifyIssues,
  formatCallStack,
  generateReport,
  generateFilename,
  severityRank,
  formatDateTime,
} = require("./report");

// =========================================================================
// classifySeverity
// =========================================================================
describe("classifySeverity", () => {
  test("returns P0 for durMs > 500", () => {
    expect(classifySeverity(501)).toBe("P0");
    expect(classifySeverity(1000)).toBe("P0");
    expect(classifySeverity(500.01)).toBe("P0");
  });

  test("returns P1 for 200 < durMs <= 500", () => {
    expect(classifySeverity(500)).toBe("P1");
    expect(classifySeverity(201)).toBe("P1");
    expect(classifySeverity(200.01)).toBe("P1");
    expect(classifySeverity(300)).toBe("P1");
  });

  test("returns P2 for 100 < durMs <= 200", () => {
    expect(classifySeverity(200)).toBe("P2");
    expect(classifySeverity(101)).toBe("P2");
    expect(classifySeverity(100.01)).toBe("P2");
    expect(classifySeverity(150)).toBe("P2");
  });

  test("returns P3 for durMs <= 100", () => {
    expect(classifySeverity(100)).toBe("P3");
    expect(classifySeverity(50)).toBe("P3");
    expect(classifySeverity(0)).toBe("P3");
    expect(classifySeverity(1)).toBe("P3");
  });

  test("boundary values", () => {
    expect(classifySeverity(100)).toBe("P3");
    expect(classifySeverity(100.01)).toBe("P2");
    expect(classifySeverity(200)).toBe("P2");
    expect(classifySeverity(200.01)).toBe("P1");
    expect(classifySeverity(500)).toBe("P1");
    expect(classifySeverity(500.01)).toBe("P0");
  });
});

// =========================================================================
// formatCallStack
// =========================================================================
describe("formatCallStack", () => {
  test("returns empty string for empty/null stack", () => {
    expect(formatCallStack([])).toBe("");
    expect(formatCallStack(null)).toBe("");
    expect(formatCallStack(undefined)).toBe("");
  });

  test("formats single-entry stack", () => {
    const stack = [{ name: "slowMethod", id: "1", parentId: "-1" }];
    const result = formatCallStack(stack);
    expect(result).toContain("slowMethod()");
    expect(result).toContain("← 瓶颈");
  });

  test("formats multi-entry stack with root at top and leaf at bottom", () => {
    // Stack is leaf-first: [leaf, intermediate, root]
    const stack = [
      { name: "leafMethod", id: "3", parentId: "2" },
      { name: "intermediateMethod", id: "2", parentId: "1" },
      { name: "rootMethod", id: "1", parentId: "-1" },
    ];
    const result = formatCallStack(stack);
    const lines = result.split("\n");

    // Root should be first (no arrow)
    expect(lines[0]).toBe("rootMethod()");
    // Intermediate has arrow and indent
    expect(lines[1]).toContain("→ intermediateMethod()");
    // Leaf is last with 瓶颈 marker
    expect(lines[2]).toContain("→ leafMethod()");
    expect(lines[2]).toContain("← 瓶颈");
  });

  test("indentation increases with depth", () => {
    const stack = [
      { name: "d", id: "4", parentId: "3" },
      { name: "c", id: "3", parentId: "2" },
      { name: "b", id: "2", parentId: "1" },
      { name: "a", id: "1", parentId: "-1" },
    ];
    const result = formatCallStack(stack);
    const lines = result.split("\n");
    expect(lines[0]).toBe("a()");
    expect(lines[1]).toBe("  → b()");
    expect(lines[2]).toBe("    → c()");
    expect(lines[3]).toBe("      → d()          ← 瓶颈");
  });

  test("appends truncation marker when stack.truncated is true", () => {
    const stack = [
      { name: "leaf", id: "2", parentId: "1" },
      { name: "parent", id: "1", parentId: "0" },
    ];
    stack.truncated = true;
    const result = formatCallStack(stack);
    const lines = result.split("\n");
    expect(lines[lines.length - 1]).toContain("... (调用栈已截断)");
    // The bottleneck marker should still be on the leaf
    expect(result).toContain("← 瓶颈");
  });

  test("does not append truncation marker when stack.truncated is falsy", () => {
    const stack = [
      { name: "leaf", id: "2", parentId: "1" },
      { name: "root", id: "1", parentId: "-1" },
    ];
    const result = formatCallStack(stack);
    expect(result).not.toContain("调用栈已截断");
  });
});

// =========================================================================
// classifyIssues
// =========================================================================
describe("classifyIssues", () => {
  test("returns empty array for empty data", () => {
    const issues = classifyIssues({
      longSlices: [],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(issues).toEqual([]);
  });

  test("filters out longSlices with durMs <= 50", () => {
    const issues = classifyIssues({
      longSlices: [
        { id: 1, name: "fast", dur_ms: 30 },
        { id: 2, name: "slow", dur_ms: 600 },
      ],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(issues.length).toBe(1);
    expect(issues[0].title).toContain("slow");
  });

  test("filters out mainThreadIO with durMs <= 50", () => {
    const issues = classifyIssues({
      longSlices: [],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [
        { id: 1, name: "Binder.fast", dur_ms: 10 },
        { id: 2, name: "Binder.slow", dur_ms: 300 },
      ],
      callStacks: {},
    });
    expect(issues.length).toBe(1);
    expect(issues[0].title).toContain("Binder.slow");
  });

  test("sorts issues P0 → P1 → P2 → P3", () => {
    const issues = classifyIssues({
      longSlices: [
        { id: 1, name: "minor", dur_ms: 80 },
        { id: 2, name: "blocking", dur_ms: 600 },
        { id: 3, name: "moderate", dur_ms: 150 },
        { id: 4, name: "severe", dur_ms: 300 },
      ],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });

    // minor (80ms) is ≤50 threshold? No, 80 > 50 so it passes.
    // minor → P3, moderate → P2, severe → P1, blocking → P0
    const severities = issues.map((i) => i.severity);
    expect(severities).toEqual(["P0", "P1", "P2", "P3"]);
  });

  test("deduplicates by method name, keeping worst occurrence", () => {
    const issues = classifyIssues({
      longSlices: [
        { id: 1, name: "duplicateMethod", dur_ms: 150 },
        { id: 2, name: "duplicateMethod", dur_ms: 600 },
      ],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    // Should keep only the 600ms occurrence (P0)
    const matching = issues.filter((i) => i.title.includes("duplicateMethod"));
    expect(matching.length).toBe(1);
    expect(matching[0].severity).toBe("P0");
  });

  test("handles missing data fields gracefully", () => {
    const issues = classifyIssues({});
    expect(issues).toEqual([]);
  });
});

// =========================================================================
// generateReport
// =========================================================================
describe("generateReport", () => {
  test("contains Summary and Issues sections", () => {
    const report = generateReport({
      longSlices: [],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(report).toContain("## Summary");
    expect(report).toContain("## Issues");
  });

  test("contains header with analysis time", () => {
    const report = generateReport({
      longSlices: [],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(report).toContain("# Perfetto Trace 性能分析报告");
    expect(report).toContain("**分析时间**:");
  });

  test("includes all required fields for each issue", () => {
    const report = generateReport({
      longSlices: [{ id: 1, name: "TestMethod", dur_ms: 600 }],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(report).toContain("[P0]");
    expect(report).toContain("**What**:");
    expect(report).toContain("**Where**:");
    expect(report).toContain("**Duration**:");
    expect(report).toContain("**Impact**:");
    expect(report).toContain("**Suggestion**:");
  });

  test("issues are ordered P0 → P3", () => {
    const report = generateReport({
      longSlices: [
        { id: 1, name: "minor", dur_ms: 80 },
        { id: 2, name: "blocking", dur_ms: 600 },
      ],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    const p0Pos = report.indexOf("[P0]");
    const p3Pos = report.indexOf("[P3]");
    expect(p0Pos).toBeLessThan(p3Pos);
  });

  test("includes call stack when available", () => {
    const report = generateReport({
      longSlices: [{ id: 1, name: "slowMethod", dur_ms: 600 }],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {
        1: [
          { name: "slowMethod", id: "1", parentId: "2" },
          { name: "rootMethod", id: "2", parentId: "-1" },
        ],
      },
    });
    expect(report).toContain("**Call Stack**:");
    expect(report).toContain("rootMethod()");
    expect(report).toContain("slowMethod()");
  });

  test("shows no-issues message when data is empty", () => {
    const report = generateReport({
      longSlices: [],
      frameJanks: [],
      cpuHeavy: [],
      mainThreadIO: [],
      callStacks: {},
    });
    expect(report).toContain("未发现显著性能问题");
  });
});

// =========================================================================
// generateFilename
// =========================================================================
describe("generateFilename", () => {
  test("matches expected format pattern", () => {
    const filename = generateFilename(new Date(2024, 0, 15, 10, 30, 45));
    expect(filename).toBe("perfetto_analysis_report_20240115_103045.md");
  });

  test("pads single-digit months and days", () => {
    const filename = generateFilename(new Date(2024, 2, 5, 8, 5, 3));
    expect(filename).toBe("perfetto_analysis_report_20240305_080503.md");
  });

  test("matches regex pattern", () => {
    const filename = generateFilename(new Date());
    expect(filename).toMatch(/^perfetto_analysis_report_\d{8}_\d{6}\.md$/);
  });

  test("defaults to current date when no argument", () => {
    const filename = generateFilename();
    expect(filename).toMatch(/^perfetto_analysis_report_\d{8}_\d{6}\.md$/);
  });
});
