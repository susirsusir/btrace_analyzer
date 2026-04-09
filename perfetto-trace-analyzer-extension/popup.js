/**
 * Popup UI for Perfetto Trace Analyzer Extension
 *
 * UI State Machine: Idle → Analyzing → Complete / Error
 *
 * Progress steps:
 *   checking_trace, long_slices, frame_jank, cpu_heavy,
 *   main_thread_io, call_stacks, generating_report, saving_file
 */

const PROGRESS_LABELS = {
  checking_trace: "正在检查 Trace 数据...",
  long_slices: "正在检测主线程长耗时 Slice...",
  frame_jank: "正在检测帧卡顿...",
  cpu_heavy: "正在识别 CPU 密集型方法...",
  main_thread_io: "正在检测主线程 I/O...",
  call_stacks: "正在追溯调用栈...",
  generating_report: "正在生成分析报告...",
  saving_file: "正在保存报告文件...",
};

// DOM elements
const startBtn = document.getElementById("startBtn");
const progressArea = document.getElementById("progressArea");
const progressText = document.getElementById("progressText");
const completeArea = document.getElementById("completeArea");
const completeText = document.getElementById("completeText");
const errorArea = document.getElementById("errorArea");
const errorText = document.getElementById("errorText");
const resultsArea = document.getElementById("resultsArea");
const problemList = document.getElementById("problemList");
const exportBtn = document.getElementById("exportBtn");

let currentReportData = null;

/**
 * Set UI to Idle state.
 */
function setIdle() {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  completeArea.style.display = "none";
  errorArea.style.display = "none";
  resultsArea.style.display = "none";
}

/**
 * Set UI to Analyzing state with a progress step.
 * @param {string} step - One of the PROGRESS_LABELS keys.
 * @param {string} [description] - Optional override description.
 */
function setAnalyzing(step, description) {
  startBtn.disabled = true;
  progressArea.style.display = "block";
  completeArea.style.display = "none";
  errorArea.style.display = "none";
  resultsArea.style.display = "none";
  progressText.textContent = description || PROGRESS_LABELS[step] || step;
}

/**
 * Set UI to Complete state.
 * @param {Array} issues - The generated issues array.
 * @param {Object} reportData - The raw report data for export.
 */
function setComplete(issues, reportData) {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  errorArea.style.display = "none";
  
  if (issues && issues.length > 0) {
    currentReportData = reportData;
    resultsArea.style.display = "block";
    completeArea.style.display = "none";
    renderIssues(issues);
  } else {
    resultsArea.style.display = "none";
    completeArea.style.display = "block";
    completeText.textContent = "分析完成！未发现明显的性能问题。";
  }
}

/**
 * Render the issues list
 */
function renderIssues(issues) {
  problemList.innerHTML = "";
  
  issues.forEach((issue) => {
    const li = document.createElement("li");
    li.style.cssText = "padding: 10px; border-bottom: 1px solid #eee; display: flex; flex-direction: column; gap: 6px;";
    
    // Title row
    const titleRow = document.createElement("div");
    titleRow.style.cssText = "display: flex; justify-content: space-between; align-items: flex-start;";
    
    const titleText = document.createElement("strong");
    titleText.style.cssText = "font-size: 13px; color: #1a1a1a; word-break: break-all;";
    titleText.textContent = `[${issue.severity}] ${issue.title.replace("主线程长耗时: ", "").replace("帧卡顿: ", "").replace("主线程 I/O: ", "").replace("CPU 密集型方法: ", "")}`;
    
    titleRow.appendChild(titleText);
    
    // View button (only if we have ts and dur)
    if (issue.ts && issue.dur) {
      const viewBtn = document.createElement("button");
      viewBtn.textContent = "查看";
      viewBtn.style.cssText = "padding: 4px 8px; font-size: 12px; color: #fff; background: #4285f4; border: none; border-radius: 4px; cursor: pointer; flex-shrink: 0; margin-left: 8px;";
      viewBtn.onclick = () => {
        chrome.runtime.sendMessage({
          action: "zoomToProblem",
          ts: issue.ts,
          dur: issue.dur
        });
      };
      titleRow.appendChild(viewBtn);
    }
    
    li.appendChild(titleRow);
    
    // Duration
    const durDiv = document.createElement("div");
    durDiv.style.cssText = "font-size: 12px; color: #666;";
    durDiv.textContent = `耗时: ${issue.duration}`;
    li.appendChild(durDiv);
    
    problemList.appendChild(li);
  });
}

/**
 * Set UI to Error state.
 */
function setError(message) {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  completeArea.style.display = "none";
  resultsArea.style.display = "none";
  errorArea.style.display = "block";
  errorText.textContent = message;
}

// Handle Export button click
exportBtn.addEventListener("click", () => {
  if (!currentReportData) return;
  chrome.runtime.sendMessage({ 
    action: "exportReport",
    reportData: currentReportData
  }, (response) => {
    if (chrome.runtime.lastError) {
      setError("导出失败：" + chrome.runtime.lastError.message);
    } else {
      completeArea.style.display = "block";
      completeText.textContent = "导出完成！";
    }
  });
});

// Handle "开始分析" button click
startBtn.addEventListener("click", () => {
  // Reset UI and enter analyzing state
  setAnalyzing("checking_trace");

  // Send startAnalysis message to Service Worker
  chrome.runtime.sendMessage({ action: "startAnalysis" }, (response) => {
    if (chrome.runtime.lastError) {
      setError("无法连接到后台服务，请重试");
    }
  });
});

// Listen for messages from Service Worker (progress / complete / error)
chrome.runtime.onMessage.addListener((message, _sender, _sendResponse) => {
  if (!message || !message.action) return;

  switch (message.action) {
    case "progress":
      setAnalyzing(message.step, message.description);
      break;

    case "complete":
      setComplete(message.issues, message.reportData);
      break;

    case "error":
      setError(message.message || "分析过程中发生未知错误");
      break;

    case "notPerfettoPage":
      setError("请在 Perfetto UI 页面上使用本插件");
      break;

    case "traceNotLoaded":
      setError("请先在 Perfetto 中加载 trace 文件");
      break;
  }
});

// Initialise in Idle state
setIdle();
