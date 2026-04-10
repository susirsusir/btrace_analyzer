/**
 * Service Worker (background.js) for Perfetto Trace Analyzer Extension
 *
 * Coordinates communication between Popup and Content Script.
 * Injects content scripts into the Perfetto page using world: "MAIN"
 * so they can access window.app.trace.engine directly.
 *
 * Requirements: 2.1, 2.2, 2.3, 10.1
 */

// Load report generation functions into the Service Worker context.
import { classifyIssues, generateReport, generateFilename } from "./report.js";

console.log('[SW] background.js loaded, import OK');

// Cache the Perfetto tab ID so zoomToProblem can use it even after the popup closes.
let perfettoTabId = null;

const PERFETTO_URL_PATTERN = "https://ui.perfetto.dev/*";
const ANALYSIS_TIMEOUT_MS = 30000; // 30 seconds

/**
 * Send a progress update to the Popup.
 * @param {string} step - Analysis step identifier (e.g. "checking_trace").
 * @param {string} description - Human-readable description of the step.
 */
async function sendProgress(step, description) {
  try {
    await chrome.runtime.sendMessage({
      action: "progress",
      step,
      description,
    });
  } catch (_) {
    // Popup may be closed; ignore send failures.
  }
}

/**
 * Inject a content script file into the given tab using world: "MAIN"
 * so the script can access page globals (e.g. window.app.trace.engine).
 * @param {number} tabId - The tab to inject into.
 * @param {string[]} files - Script file paths relative to the extension root.
 * @returns {Promise<void>}
 */
async function injectContentScript(tabId, files = ["content.js"]) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files,
    world: "MAIN",
  });
}

/**
 * Execute a function in the page's MAIN world and return its result.
 *
 * Because content scripts injected with world: "MAIN" share the page's JS
 * context (not the extension's isolated world), we cannot use
 * chrome.tabs.sendMessage to talk to them. Instead we use
 * chrome.scripting.executeScript to run an arbitrary function in MAIN world
 * and retrieve the return value via the injection result.
 *
 * @param {number} tabId - Target tab.
 * @param {Function} func - A serialisable function to execute in the page context.
 * @param {any[]} [args=[]] - Arguments forwarded to `func`.
 * @returns {Promise<any>} The value returned by `func`.
 */
async function sendToContentScript(tabId, func, args = []) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func,
    args,
    world: "MAIN",
  });

  // results is an array of InjectionResult; we care about the first frame.
  if (results && results.length > 0) {
    return results[0].result;
  }
  return undefined;
}

/**
 * The diagnostic query types to execute, in order.
 */
const DIAGNOSTIC_TYPES = ["long_slices", "frame_jank", "cpu_heavy", "main_thread_io"];

/**
 * Human-readable labels for each diagnostic step (used in progress messages).
 */
const DIAGNOSTIC_LABELS = {
  long_slices: "正在检测主线程长耗时 Slice...",
  frame_jank: "正在检测帧卡顿...",
  cpu_heavy: "正在识别 CPU 密集型方法...",
  main_thread_io: "正在检测主线程 I/O...",
};

/**
 * Core analysis logic without timeout wrapper.
 * Executes the full pipeline: check trace → diagnostics → call stacks → report → save.
 *
 * @param {number} tabId - The Perfetto tab to analyse.
 */
async function runAnalysisCore(tabId) {
  console.log('[SW] runAnalysisCore start, tabId:', tabId);
  // ------------------------------------------------------------------
  // Step 1: Check trace loaded
  // ------------------------------------------------------------------
  await sendProgress("checking_trace", "正在检查 Trace 数据...");

  const traceStatus = await sendToContentScript(
    tabId,
    () => window.__perfettoAnalyzer.checkTraceLoaded()
  );
  console.log('[SW] traceStatus:', JSON.stringify(traceStatus));

  if (!traceStatus || !traceStatus.ready) {
    console.warn('[SW] trace not ready, aborting');
    try {
      await chrome.runtime.sendMessage({ action: "traceNotLoaded" });
    } catch (_) { /* popup closed */ }
    return;
  }

  // ------------------------------------------------------------------
  // Step 2: Run 4 diagnostic queries (partial failure tolerant)
  // ------------------------------------------------------------------
  const diagnosticResults = {};

  for (const type of DIAGNOSTIC_TYPES) {
    await sendProgress(type, DIAGNOSTIC_LABELS[type]);
    try {
      const rows = await sendToContentScript(
        tabId,
        (t) => window.__perfettoAnalyzer.runDiagnosticQuery(t),
        [type]
      );
      diagnosticResults[type] = rows || [];
      console.log(`[SW] diagnostic "${type}": ${diagnosticResults[type].length} rows`);
    } catch (err) {
      // Skip this diagnostic type on failure, continue with others
      console.warn(`[SW] Diagnostic query "${type}" failed, skipping:`, err);
      diagnosticResults[type] = [];
    }
  }

  // ------------------------------------------------------------------
  // Step 3: Trace call stacks for significant slices
  // ------------------------------------------------------------------
  await sendProgress("call_stacks", "正在追溯调用栈...");

  const callStacks = {};

  // Collect slice IDs from long_slices, frame_jank, and main_thread_io
  // (these have an `id` field; cpu_heavy is aggregated and has no id)
  const sliceSourceTypes = ["long_slices", "frame_jank", "main_thread_io"];
  const sliceIds = new Set();

  for (const type of sliceSourceTypes) {
    const rows = diagnosticResults[type] || [];
    for (const row of rows) {
      if (row.id !== undefined && row.id !== null) {
        sliceIds.add(row.id);
      }
    }
  }

  for (const sliceId of sliceIds) {
    try {
      const stack = await sendToContentScript(
        tabId,
        (sid) => window.__perfettoAnalyzer.traceCallStack(sid),
        [sliceId]
      );
      if (stack && stack.length > 0) {
        callStacks[sliceId] = stack;
      }
    } catch (err) {
      // Call stack tracing failed for this slice; skip it
      console.warn(`Call stack tracing failed for slice ${sliceId}:`, err);
    }
  }

  // ------------------------------------------------------------------
  // Step 4: Generate report data and issues list
  // ------------------------------------------------------------------
  await sendProgress("generating_report", "正在生成分析报告...");

  const reportData = {
    longSlices: diagnosticResults.long_slices || [],
    frameJanks: diagnosticResults.frame_jank || [],
    cpuHeavy: diagnosticResults.cpu_heavy || [],
    mainThreadIO: diagnosticResults.main_thread_io || [],
    callStacks,
  };

  const issues = classifyIssues(reportData);
  console.log('[SW] issues count:', issues.length, issues.map(i => `[${i.severity}] ${i.title}`));

  // Persist results in session storage so the popup can restore them on re-open.
  try {
    await chrome.storage.session.set({ lastAnalysis: { issues, reportData } });
  } catch (_) { /* session storage unavailable; non-fatal */ }

  try {
    await chrome.runtime.sendMessage({
      action: "complete",
      issues: issues,
      reportData: reportData
    });
  } catch (_) { /* popup closed */ }
}

/**
 * Full analysis orchestrator with 30-second timeout.
 *
 * Executes the analysis pipeline and races it against a timeout.
 * If the timeout fires first, an error message is sent to the Popup.
 *
 * @param {number} tabId - The Perfetto tab to analyse.
 */
async function runAnalysis(tabId) {
  const timeoutPromise = new Promise((_, reject) => {
    setTimeout(() => reject(new Error("分析超时，请重试")), ANALYSIS_TIMEOUT_MS);
  });

  try {
    await Promise.race([runAnalysisCore(tabId), timeoutPromise]);
  } catch (err) {
    try {
      await chrome.runtime.sendMessage({
        action: "error",
        message: err.message || "分析过程中发生未知错误",
      });
    } catch (_) { /* popup closed */ }
  }
}

// ---------------------------------------------------------------------------
// Message listener – entry point from Popup
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[SW] onMessage:', message && message.action);
  if (message.action === "startAnalysis") {
    (async () => {
      try {
        // 1. Get the currently active tab
        const [tab] = await chrome.tabs.query({
          active: true,
          currentWindow: true,
        });
        console.log('[SW] active tab:', tab && tab.url);

        // Cache the Perfetto tab ID for later use by zoomToProblem
        perfettoTabId = tab.id;

        if (!tab || !tab.url) {
          await chrome.runtime.sendMessage({
            action: "error",
            message: "无法获取当前标签页信息",
          });
          return;
        }

        // 2. Check if the tab is a Perfetto UI page
        const isPerfetto = tab.url.startsWith("https://ui.perfetto.dev/");
        if (!isPerfetto) {
          await chrome.runtime.sendMessage({ action: "notPerfettoPage" });
          return;
        }

        // 3. Inject content scripts into the Perfetto page
        //    Order matters: utility modules first, then the main content script.
        try {
          await injectContentScript(tab.id, ["diagnostics.js", "callstack.js", "content.js"]);
          console.log('[SW] content scripts injected OK');
        } catch (err) {
          console.error('[SW] inject failed:', err);
          await chrome.runtime.sendMessage({
            action: "error",
            message: "无法注入分析脚本，请刷新页面后重试",
          });
          return;
        }

        // 4. Run full analysis pipeline with timeout
        await runAnalysis(tab.id);
      } catch (err) {
        try {
          await chrome.runtime.sendMessage({
            action: "error",
            message: `分析过程中发生错误：${err.message}`,
          });
        } catch (_) {
          // Popup closed; nothing we can do.
        }
      }
    })();
    return true;
  }

  if (message.action === "exportReport") {
    (async () => {
      try {
        const markdown = generateReport(message.reportData);
        const filename = generateFilename();

        const bytes = new TextEncoder().encode(markdown);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const base64Content = btoa(binary);
        const dataUrl = `data:text/markdown;base64,${base64Content}`;

        chrome.downloads.download({ url: dataUrl, filename, saveAs: false }, (id) => {
          if (chrome.runtime.lastError) {
            sendResponse({ error: chrome.runtime.lastError.message });
          } else {
            sendResponse({ success: true });
          }
        });
      } catch (err) {
        sendResponse({ error: err.message });
      }
    })();
    return true;
  }

  if (message.action === "zoomToProblem") {
    (async () => {
      try {
        // Use cached tabId to get the current Perfetto page URL (with local_cache_key).
        let sourceTabId = perfettoTabId;
        if (!sourceTabId) {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (tab && tab.url.startsWith("https://ui.perfetto.dev/")) {
            sourceTabId = tab.id;
          }
        }
        console.log('[SW] zoomToProblem tabId:', sourceTabId, 'ts:', message.ts, 'dur:', message.dur);
        if (!sourceTabId) {
          console.warn('[SW] zoomToProblem: no Perfetto tab found');
          return;
        }

        // Get the current Perfetto URL (contains local_cache_key so the trace reloads).
        const sourceTab = await chrome.tabs.get(sourceTabId);
        const perfettoUrl = sourceTab.url;

        // Open a new tab with the same trace URL.
        const newTab = await chrome.tabs.create({ url: perfettoUrl, active: true });
        const newTabId = newTab.id;
        console.log('[SW] opened new tab id:', newTabId, 'url:', perfettoUrl);

        // Wait for the Perfetto engine to become available in the new tab (up to 15s).
        const ready = await new Promise((resolve) => {
          let attempts = 0;
          const MAX = 30; // 30 × 500ms = 15s
          const poll = async () => {
            try {
              const result = await chrome.scripting.executeScript({
                target: { tabId: newTabId },
                func: () => !!(window.app && window.app.trace && window.app.trace.engine),
                world: "MAIN",
              });
              if (result && result[0] && result[0].result) {
                resolve(true);
                return;
              }
            } catch (_) { /* tab still loading */ }
            if (++attempts < MAX) setTimeout(poll, 500);
            else resolve(false);
          };
          setTimeout(poll, 1000); // initial delay for page load
        });

        if (!ready) {
          console.warn('[SW] zoomToProblem: engine not ready in new tab');
          return;
        }

        // Wait an extra 1s after engine is ready for Perfetto's initial render
        // to complete. Without this, setVisibleWindow gets overwritten by the
        // React render that resets the timeline to show the full trace.
        await new Promise(r => setTimeout(r, 1000));

        // Execute zoom directly via executeScript (inline func, no file injection needed).
        // Using files injection + a separate func call has a race condition where
        // the injected file may not have finished executing before the func runs.
        const zoomResult = await sendToContentScript(newTabId,
          (tsStr, durStr, label, sliceIdStr) => {
            try {
              // Update the page title so multiple tabs can be distinguished
              if (label) document.title = label + ' — Perfetto';
              const timeline = window.app.trace.timeline;
              const vw = timeline._visibleWindow;
              if (!vw) return 'no visibleWindow';
              const HPT = vw.start.constructor;
              const HPTS = vw.constructor;
              const ts = BigInt(tsStr);
              const dur = BigInt(durStr);

              // Add 10% padding on each side so the target slice is visually centred
              // and not flush against the viewport edge.
              const padding = BigInt(Math.round(Number(dur) * 0.1));
              const paddedStart = ts - padding > 0n ? ts - padding : ts;
              const paddedDur = Number(dur) + Number(padding) * 2;
              timeline.setVisibleWindow(new HPTS(new HPT(paddedStart, 0), paddedDur));

              // Select the specific slice so the details panel highlights it.
              // Perfetto exposes selection.selectSqlEvent(table, rowId) for this.
              if (sliceIdStr) {
                try {
                  const sel = window.app.trace.selection;
                  if (sel && typeof sel.selectSqlEvent === 'function') {
                    sel.selectSqlEvent('slice', Number(sliceIdStr));
                  }
                } catch (_) { /* selection API unavailable, non-fatal */ }
              }

              return 'zoom OK: ' + String(timeline._visibleWindow.start.integral);
            } catch (e) {
              return 'zoom error: ' + e.message;
            }
          },
          [message.ts, message.dur, message.label || '', message.sliceId || '']);
        console.log('[SW] zoomToProblem result:', zoomResult);

        // Verify the zoom stuck after 1.5s
        await new Promise(r => setTimeout(r, 1500));
        const verifyResult = await sendToContentScript(newTabId,
          () => String(window.app.trace.timeline._visibleWindow.start.integral));
        console.log('[SW] zoom verify after 1.5s:', verifyResult);
        sendResponse({ success: true });
      } catch (e) {
        console.error('[SW] ZoomToProblem error:', e);
        sendResponse({ error: e.message });
      }
    })();
    return true;
  }
});
