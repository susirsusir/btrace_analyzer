/**
 * content.js — Content Script for Perfetto Trace Analyzer
 *
 * Injected into the Perfetto page via chrome.scripting.executeScript
 * with world: "MAIN", giving direct access to window.app.trace.engine.
 *
 * This script adds the trace-loaded check to the window.__perfettoAnalyzer
 * namespace. diagnostics.js (injected before this file) already provides
 * runDiagnosticQuery on the same namespace.
 *
 * Communication pattern:
 *   background.js → chrome.scripting.executeScript (world: "MAIN")
 *     → calls window.__perfettoAnalyzer.checkTraceLoaded()
 *     → result returned via injection result
 *
 * Requirements: 2.1, 2.2, 2.3, 3.1, 4.1, 5.1, 6.1
 */

(function () {
  "use strict";

  /**
   * Check whether a Perfetto trace is loaded and its query engine is available.
   *
   * @returns {{ ready: boolean, error?: string }}
   *   ready=true  when window.app.trace.engine exists and is usable.
   *   ready=false with an error message otherwise.
   */
  function checkTraceLoaded() {
    try {
      if (
        window.app &&
        window.app.trace &&
        window.app.trace.engine
      ) {
        return { ready: true };
      }
      return {
        ready: false,
        error: "请先在 Perfetto 中加载 trace 文件",
      };
    } catch (e) {
      return {
        ready: false,
        error: "检测 trace 状态时出错：" + e.message,
      };
    }
  }

  // Expose on the shared namespace (diagnostics.js initialises it first)
  window.__perfettoAnalyzer = window.__perfettoAnalyzer || {};
  window.__perfettoAnalyzer.checkTraceLoaded = checkTraceLoaded;

  /**
   * Zoom the Perfetto timeline to a specific time range.
   * Called from background.js via sendToContentScript.
   *
   * @param {string} tsStr - Slice timestamp in nanoseconds (as string to avoid BigInt serialization issues).
   * @param {string} durStr - Slice duration in nanoseconds (as string).
   * @returns {string} 'zoom OK' or an error message.
   */
  function zoomToTimeRange(tsStr, durStr) {
    try {
      const timeline = window.app.trace.timeline;
      const vw = timeline._visibleWindow;
      if (!vw) return 'no visibleWindow';

      const HPT = vw.start.constructor;
      const HPTS = vw.constructor;

      const ts = BigInt(tsStr);
      const dur = BigInt(durStr);
      const padding = dur / BigInt(3);
      const startTime = new HPT({ integral: ts - padding, fractional: 0 });
      const totalDur = Number(dur + padding * BigInt(2));

      timeline.setVisibleWindow(new HPTS(startTime, totalDur));
      return 'zoom OK';
    } catch (e) {
      return 'zoom error: ' + e.message;
    }
  }

  window.__perfettoAnalyzer.zoomToTimeRange = zoomToTimeRange;
})();
