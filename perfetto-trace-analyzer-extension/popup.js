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

/**
 * Set UI to Idle state.
 */
function setIdle() {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  completeArea.style.display = "none";
  errorArea.style.display = "none";
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
  progressText.textContent = description || PROGRESS_LABELS[step] || step;
}

/**
 * Set UI to Complete state.
 * @param {string} filename - The saved report filename.
 */
function setComplete(filename) {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  completeArea.style.display = "block";
  errorArea.style.display = "none";
  completeText.textContent = filename
    ? `分析完成！报告已保存：${filename}`
    : "分析完成！";
}

/**
 * Set UI to Error state.
 * @param {string} message - Error message to display.
 */
function setError(message) {
  startBtn.disabled = false;
  progressArea.style.display = "none";
  completeArea.style.display = "none";
  errorArea.style.display = "block";
  errorText.textContent = message;
}

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
      setComplete(message.filename);
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
