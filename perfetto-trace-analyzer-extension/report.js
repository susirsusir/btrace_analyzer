/**
 * report.js — Markdown report generation for Perfetto Trace Analyzer
 *
 * Loaded in the Service Worker (background.js) context via importScripts().
 * Generates structured Markdown performance analysis reports from diagnostic data.
 *
 * Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 9.2
 */

// ---------------------------------------------------------------------------
// Severity classification
// ---------------------------------------------------------------------------

/**
 * Classify the severity of a performance issue based on its duration.
 *
 * Rules (from Requirements 8.5):
 *   >500ms → P0 (blocking)
 *   >200ms → P1 (severe)
 *   >100ms → P2 (moderate)
 *   ≤100ms → P3 (minor)
 *
 * @param {number} durMs - Duration in milliseconds.
 * @returns {"P0"|"P1"|"P2"|"P3"}
 */
function classifySeverity(durMs) {
  if (durMs > 500) return "P0";
  if (durMs > 200) return "P1";
  if (durMs > 100) return "P2";
  return "P3";
}

// ---------------------------------------------------------------------------
// Issue classification
// ---------------------------------------------------------------------------

/**
 * Convert raw diagnostic data into a deduplicated, severity-sorted Issue list.
 *
 * @param {object} data - Analysis data.
 * @param {Array} data.longSlices - Long-running main-thread slices.
 * @param {Array} data.frameJanks - Frame jank slices.
 * @param {Array} data.mainThreadIO - Main-thread I/O slices.
 * @param {Array} data.cpuHeavy - CPU-heavy method aggregates.
 * @param {Object} data.callStacks - Map of sliceId → callStack array.
 * @returns {Array<object>} Sorted Issue list (P0 first).
 */
function classifyIssues(data) {
  const issueMap = new Map(); // keyed by method name, keeps worst occurrence

  const longSlices = data.longSlices || [];
  const frameJanks = data.frameJanks || [];
  const mainThreadIO = data.mainThreadIO || [];
  const cpuHeavy = data.cpuHeavy || [];
  const callStacks = data.callStacks || {};

  // Helper: upsert into issueMap, keeping the higher-severity (longer duration) entry
  function upsert(key, issue) {
    const existing = issueMap.get(key);
    if (!existing || severityRank(issue.severity) < severityRank(existing.severity)) {
      issueMap.set(key, issue);
    } else if (
      severityRank(issue.severity) === severityRank(existing.severity) &&
      parseFloat(issue.durationRaw) > parseFloat(existing.durationRaw)
    ) {
      issueMap.set(key, issue);
    }
  }

  // Long slices (>50ms threshold)
  for (const s of longSlices) {
    if (s.dur_ms <= 50) continue;
    const stack = callStacks[s.id] || [];
    upsert(s.name, {
      severity: classifySeverity(s.dur_ms),
      title: `主线程长耗时: ${s.name}`,
      what: `主线程方法 \`${s.name}\` 单次执行耗时 ${s.dur_ms.toFixed(1)}ms，可能导致 UI 卡顿`,
      where: `\`${s.name}\``,
      duration: `${s.dur_ms.toFixed(1)}ms`,
      durationRaw: s.dur_ms,
      impact: s.dur_ms > 500
        ? "严重阻塞主线程，用户可感知明显卡顿或无响应"
        : s.dur_ms > 200
          ? "主线程阻塞时间较长，可能导致掉帧和交互延迟"
          : s.dur_ms > 100
            ? "主线程有一定阻塞，可能在低端设备上引起卡顿"
            : "轻微影响，建议关注",
      callStack: formatCallStack(stack),
      suggestion: `考虑将 \`${s.name}\` 的耗时操作移至后台线程，或优化其执行逻辑以减少耗时`,
    });
  }

  // Frame janks
  for (const s of frameJanks) {
    const stack = callStacks[s.id] || [];
    const key = `jank:${s.name}`;
    upsert(key, {
      severity: classifySeverity(s.dur_ms),
      title: `帧卡顿: ${s.name}`,
      what: `帧渲染方法 \`${s.name}\` 耗时 ${s.dur_ms.toFixed(1)}ms，超过 16.6ms 帧预算`,
      where: `\`${s.name}\``,
      duration: `${s.dur_ms.toFixed(1)}ms`,
      durationRaw: s.dur_ms,
      impact: `帧渲染超时 ${(s.dur_ms / 16.6).toFixed(1)} 倍，用户可感知明显掉帧`,
      callStack: formatCallStack(stack),
      suggestion: `优化帧渲染流程，减少 \`${s.name}\` 中的同步操作，避免在 doFrame 中执行耗时任务`,
    });
  }

  // Main thread I/O (>50ms threshold)
  for (const s of mainThreadIO) {
    if (s.dur_ms <= 50) continue;
    const stack = callStacks[s.id] || [];
    const key = `io:${s.name}`;
    upsert(key, {
      severity: classifySeverity(s.dur_ms),
      title: `主线程 I/O: ${s.name}`,
      what: `主线程上执行了 I/O 操作 \`${s.name}\`，耗时 ${s.dur_ms.toFixed(1)}ms`,
      where: `\`${s.name}\``,
      duration: `${s.dur_ms.toFixed(1)}ms`,
      durationRaw: s.dur_ms,
      impact: "主线程 I/O 操作会阻塞 UI 渲染，导致卡顿和 ANR 风险",
      callStack: formatCallStack(stack),
      suggestion: `将 \`${s.name}\` 移至后台线程执行，使用异步 API 或 WorkManager 处理 I/O 操作`,
    });
  }

  // CPU heavy methods (top by total_ms)
  for (const s of cpuHeavy) {
    const durForSeverity = s.total_ms;
    const key = `cpu:${s.name}`;
    upsert(key, {
      severity: classifySeverity(durForSeverity),
      title: `CPU 密集型方法: ${s.name}`,
      what: `方法 \`${s.name}\` 被调用 ${s.count} 次，总耗时 ${s.total_ms.toFixed(1)}ms（平均 ${s.avg_ms.toFixed(1)}ms/次）`,
      where: `\`${s.name}\``,
      duration: `${s.total_ms.toFixed(1)}ms (${s.count}次, 平均${s.avg_ms.toFixed(1)}ms)`,
      durationRaw: s.total_ms,
      impact: durForSeverity > 500
        ? "累计耗时极高，严重消耗 CPU 资源，影响整体性能"
        : durForSeverity > 200
          ? "累计耗时较高，占用较多 CPU 资源"
          : "有一定 CPU 开销，建议关注",
      callStack: "",
      suggestion: `优化 \`${s.name}\` 的实现，减少调用频次或单次执行耗时，考虑缓存计算结果`,
    });
  }

  // Collect, sort by severity (P0→P3), then by durationRaw descending
  const issues = Array.from(issueMap.values());
  issues.sort((a, b) => {
    const rankDiff = severityRank(a.severity) - severityRank(b.severity);
    if (rankDiff !== 0) return rankDiff;
    return (b.durationRaw || 0) - (a.durationRaw || 0);
  });

  return issues;
}

/**
 * Numeric rank for severity (lower = more severe).
 * @param {"P0"|"P1"|"P2"|"P3"} severity
 * @returns {number}
 */
function severityRank(severity) {
  const ranks = { P0: 0, P1: 1, P2: 2, P3: 3 };
  return ranks[severity] !== undefined ? ranks[severity] : 4;
}

// ---------------------------------------------------------------------------
// Call stack formatting
// ---------------------------------------------------------------------------

/**
 * Format a call stack array into indented arrow notation.
 *
 * Input: array of { name, id, parentId } from leaf (problem slice) to root.
 * Output: multi-line string with root at top, leaf at bottom marked as 瓶颈.
 *
 * Example:
 *   rootMethod()
 *     → intermediateMethod()
 *       → leafMethod()          ← 瓶颈
 *
 * @param {Array<{name: string}>} stack - Call stack entries (leaf-first order).
 * @returns {string} Formatted call stack string.
 */
function formatCallStack(stack) {
  if (!stack || stack.length === 0) return "";

  // The stack from traceCallStack is leaf-first (problem slice at index 0,
  // root at the end). We reverse it so root is at the top.
  const reversed = stack.slice().reverse();

  const lines = [];
  for (let i = 0; i < reversed.length; i++) {
    const entry = reversed[i];
    const name = entry.name || entry;
    const isLast = i === reversed.length - 1;
    const indent = "  ".repeat(i);

    if (i === 0) {
      // Single-entry stack: root is also the bottleneck
      const suffix = (isLast && reversed.length === 1) ? "          ← 瓶颈" : "";
      lines.push(`${name}()${suffix}`);
    } else {
      const suffix = isLast ? "          ← 瓶颈" : "";
      lines.push(`${indent}→ ${name}()${suffix}`);
    }
  }

  // Annotate truncation when the call stack was cut short by maxDepth
  if (stack.truncated) {
    const indent = "  ".repeat(reversed.length);
    lines.push(`${indent}... (调用栈已截断)`);
  }

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Report generation
// ---------------------------------------------------------------------------

/**
 * Generate a complete Markdown performance analysis report.
 *
 * @param {object} data - Analysis data containing diagnostic results and call stacks.
 * @returns {string} Markdown report content.
 */
function generateReport(data) {
  const now = new Date();
  const timestamp = formatDateTime(now);
  const issues = classifyIssues(data);

  const lines = [];

  // Header
  lines.push("# Perfetto Trace 性能分析报告");
  lines.push("");
  lines.push(`**分析时间**: ${timestamp}`);
  lines.push("");

  // Summary
  lines.push("## Summary");
  lines.push("");
  lines.push(generateSummary(issues, data));
  lines.push("");

  // Issues
  lines.push("## Issues");
  lines.push("");

  if (issues.length === 0) {
    lines.push("未发现显著性能问题。");
    lines.push("");
  } else {
    for (const issue of issues) {
      lines.push(`### [${issue.severity}] ${issue.title}`);
      lines.push(`- **What**: ${issue.what}`);
      lines.push(`- **Where**: ${issue.where}`);
      lines.push(`- **Duration**: ${issue.duration}`);
      lines.push(`- **Impact**: ${issue.impact}`);
      if (issue.callStack) {
        lines.push("- **Call Stack**:");
        lines.push("  ```");
        // Indent each line of the call stack within the code block
        const stackLines = issue.callStack.split("\n");
        for (const sl of stackLines) {
          lines.push(`  ${sl}`);
        }
        lines.push("  ```");
      }
      lines.push(`- **Suggestion**: ${issue.suggestion}`);
      lines.push("");
    }
  }

  return lines.join("\n");
}

/**
 * Generate a summary paragraph based on classified issues.
 *
 * @param {Array} issues - Classified and sorted issue list.
 * @param {object} data - Raw analysis data.
 * @returns {string} Summary text.
 */
function generateSummary(issues, data) {
  if (issues.length === 0) {
    return "本次 trace 分析未发现显著性能问题，应用整体运行状况良好。";
  }

  const counts = { P0: 0, P1: 0, P2: 0, P3: 0 };
  for (const issue of issues) {
    counts[issue.severity]++;
  }

  const parts = [];
  parts.push(`本次分析共发现 ${issues.length} 个性能问题`);

  const severityParts = [];
  if (counts.P0 > 0) severityParts.push(`${counts.P0} 个 P0（阻塞级）`);
  if (counts.P1 > 0) severityParts.push(`${counts.P1} 个 P1（严重）`);
  if (counts.P2 > 0) severityParts.push(`${counts.P2} 个 P2（中等）`);
  if (counts.P3 > 0) severityParts.push(`${counts.P3} 个 P3（轻微）`);

  if (severityParts.length > 0) {
    parts.push(`，其中 ${severityParts.join("、")}。`);
  } else {
    parts.push("。");
  }

  if (counts.P0 > 0) {
    parts.push("存在阻塞级问题，建议优先处理 P0 问题以改善用户体验。");
  } else if (counts.P1 > 0) {
    parts.push("建议优先关注 P1 级别问题。");
  }

  return parts.join("");
}

// ---------------------------------------------------------------------------
// Filename generation
// ---------------------------------------------------------------------------

/**
 * Generate a report filename with timestamp.
 *
 * Format: perfetto_analysis_report_YYYYMMDD_HHmmss.md
 *
 * @param {Date} [date] - Date to use for the timestamp. Defaults to now.
 * @returns {string} Filename string.
 */
function generateFilename(date) {
  if (!date) date = new Date();

  const y = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const h = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  const s = String(date.getSeconds()).padStart(2, "0");

  return `perfetto_analysis_report_${y}${mo}${d}_${h}${mi}${s}.md`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Format a Date as "YYYY-MM-DD HH:mm:ss" in local time.
 * @param {Date} date
 * @returns {string}
 */
function formatDateTime(date) {
  const y = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const h = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  const s = String(date.getSeconds()).padStart(2, "0");
  return `${y}-${mo}-${d} ${h}:${mi}:${s}`;
}


// ---------------------------------------------------------------------------
// Export for testing (Node.js / Jest). In the Service Worker context,
// `module` is undefined and this block is safely skipped.
// ---------------------------------------------------------------------------
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    classifySeverity,
    classifyIssues,
    formatCallStack,
    generateReport,
    generateFilename,
    severityRank,
    formatDateTime,
    generateSummary,
  };
}
