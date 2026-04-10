# BTrace Analyzer

[中文文档](README_CN.md)

A toolkit for analyzing BTrace/xTrace Android performance sampling trace files. Provides **three completely independent tools** — use whichever fits your workflow.

## What is this?

BTrace/xTrace is a custom binary sampling trace format used by some Android performance monitoring SDKs (e.g., `BTraceMonitor`). These files are **not** standard Perfetto/systrace format and cannot be loaded in Perfetto UI directly.

## Three Independent Tools

Each tool is self-contained and works independently of the others. You do not need to install or configure the other two to use any one of them.

| Tool | Type | What it does | Dependencies |
|------|------|--------------|--------------|
| [`btrace-analyzer`](skills/btrace-analyzer/SKILL.md) | AI Skill | Text-based analysis — downloads and parses the binary directly, outputs CPU hotspots, inclusive time, and hot call stacks in chat | Python 3, curl |
| [`btrace-perfetto-viewer`](skills/btrace-perfetto-viewer/SKILL.md) | AI Skill | Visual analysis — converts to Perfetto protobuf, opens in Perfetto UI via MCP browser, runs SQL diagnostics, generates a Markdown report with screenshots | Java 8+, `btrace.jar`, `chrome-devtools-mcp` |
| [`perfetto-trace-analyzer-extension`](perfetto-trace-analyzer-extension/) | Chrome Extension | In-browser analysis — runs SQL diagnostics on a trace already loaded in Perfetto UI, shows a prioritized issue list with locate buttons, exports a Markdown report | Chrome browser |

### When to use which

- **btrace-analyzer** — quickest option, no setup required, gives you CPU hotspots in seconds
- **btrace-perfetto-viewer** — when you need a visual timeline with screenshots and a saved report; requires `btrace.jar`
- **perfetto-trace-analyzer-extension** — when you already have a trace open in Perfetto UI and want automated diagnostics without leaving the browser

---

## Tool 1: btrace-analyzer (AI Skill)

### Installation

**Kiro — workspace scope** (applies to one project):

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-analyzer
```

**Kiro — global scope** (available in all workspaces):

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-analyzer
```

**Other AI assistants** — attach [`skills/btrace-analyzer/SKILL.md`](skills/btrace-analyzer/SKILL.md) as context before asking for analysis.

### Usage

No configuration needed. Just provide the trace and mapping URLs:

```
Analyze this BTrace trace file:
- Trace: https://example.com/path/to/sampling
- Mapping: https://example.com/path/to/sampling-mapping
```

The AI will download both files, parse the binary format with inline Python, and output:

- **Self Time** — methods directly on top of the stack (direct CPU consumers)
- **Inclusive Time** — methods appearing anywhere in the call stack
- **Hot Call Stacks** — most frequently sampled execution paths (top 5 frames)

---

## Tool 2: btrace-perfetto-viewer (AI Skill)

### Dependencies

| Dependency | Description | How to install |
|------------|-------------|----------------|
| Java 8+ | Runtime for btrace.jar | `brew install openjdk` (macOS) or your package manager |
| `btrace.jar` | Converts BTrace binary to Perfetto protobuf | See below |
| `chrome-devtools-mcp` | MCP server for browser control | `npx -y chrome-devtools-mcp@latest` |

### Installing btrace.jar

`btrace.jar` is the RheaTrace/BTrace trace processor. It is distributed as part of the ByteDance APM SDK toolchain and is **not publicly available on Maven or npm**. Obtain it through one of these channels:

1. **From your APM SDK package** — if your app integrates the BTrace/RheaTrace SDK, the JAR is typically included in the SDK's tools directory
2. **From your team's internal toolchain** — ask your performance/APM team for the latest version
3. **From the RheaTrace open-source project** — build from source at [github.com/bytedance/btrace](https://github.com/bytedance/btrace) if your team uses the open-source variant

Once you have the JAR, place it in a stable location:

```bash
mkdir -p ~/.btrace-analyzer
cp /path/to/btrace.jar ~/.btrace-analyzer/btrace.jar
```

Verify it works:

```bash
java -jar ~/.btrace-analyzer/btrace.jar --help
```

### Configuration

Copy the example config and fill in your values:

```bash
cp config.local.json.example config.local.json
```

```json
{
  "btrace_jar_path": "~/.btrace-analyzer/btrace.jar",
  "app_package_name": "com.example.myapp"
}
```

| Field | Description |
|-------|-------------|
| `btrace_jar_path` | Absolute or `~`-relative path to your `btrace.jar` |
| `app_package_name` | Your Android app's package name |

This file is git-ignored and will not be committed.

### Installation

**Kiro — workspace scope**:

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-perfetto-viewer
```

**Kiro — global scope**:

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-perfetto-viewer
```

**Other AI assistants** — attach [`skills/btrace-perfetto-viewer/SKILL.md`](skills/btrace-perfetto-viewer/SKILL.md) as context.

### Usage

```
Visually analyze this trace in Perfetto:
- Trace: https://example.com/path/to/sampling
- Mapping: https://example.com/path/to/sampling-mapping
```

The AI will:
1. Download the trace and mapping files
2. Convert to Perfetto protobuf via `btrace.jar`
3. Start a local HTTP server and open Perfetto UI in the MCP-controlled browser
4. Run SQL diagnostic queries (long slices, frame jank, CPU-heavy methods, main-thread I/O)
5. Trace call stacks for each issue
6. Navigate to each issue's exact time range and take a screenshot
7. Generate a prioritized report at `trace-analysis/<traceID>/report.md`

---

## Tool 3: perfetto-trace-analyzer-extension (Chrome Extension)

This tool works entirely in the browser. It does **not** require `btrace.jar` or any AI skill — it analyzes a trace that is already loaded in Perfetto UI.

### Installation

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** and select the `perfetto-trace-analyzer-extension/` directory
4. The extension icon appears in the toolbar, active only on `ui.perfetto.dev`

### Usage

1. Open `https://ui.perfetto.dev` and load your trace file (any format Perfetto supports, including the `.pb` output from `btrace-perfetto-viewer`)
2. Click the **Perfetto Trace Analyzer** extension icon
3. Click **Start Analysis**
4. The extension runs four diagnostic queries and traces call stacks, then displays a prioritized issue list in the popup
5. Click **Locate** next to any issue to open a new tab zoomed to that exact time range with the slice selected and highlighted in the details panel
6. Click **Export Report** to download the Markdown report as `perfetto_analysis_report_YYYYMMDD_HHmmss.md`

> CPU-heavy issues are aggregate statistics (no single timestamp) and show an **Aggregated** badge instead of a Locate button.

Severity levels: **P0** (>500ms), **P1** (>200ms), **P2** (>100ms), **P3** (≤100ms)

---

## File Format Reference

### Trace File (`sampling`)

| Field | Size | Description |
|-------|------|-------------|
| Magic | 4 bytes | `0x01020304` (LE) |
| Reserved | 4 bytes | - |
| Version | 4 bytes | uint32 LE (typically 5) |
| Timestamp | 8 bytes | uint64 LE |
| Sampling Interval | 4 bytes | microseconds (e.g., 25000 = 25ms) |
| JSON Length | 4 bytes | uint32 LE |
| JSON Metadata | N bytes | e.g., `{"processId":11550}` |
| Sample Records | ... | Repeated until EOF |

Each sample record:

| Field | Size | Description |
|-------|------|-------------|
| Marker | 2 bytes | Record type (0x0002-0x0017) |
| Timing/Thread Data | 66 bytes | Timestamps, thread IDs, etc. |
| Stack Depth | 4 bytes | uint32 LE |
| Stack Depth (dup) | 4 bytes | uint32 LE |
| Frame Addresses | depth × 8 bytes | uint64 LE each |

### Mapping File (`sampling-mapping`)

| Field | Size | Description |
|-------|------|-------------|
| Reserved | 8 bytes | Typically 0 |
| Version | 4 bytes | uint32 LE (typically 1) |
| Method Count | 4 bytes | uint32 LE |
| Records | ... | Repeated |

Each mapping record:

| Field | Size | Description |
|-------|------|-------------|
| Method Address | 8 bytes | uint64 LE |
| String Length | 2 bytes | uint16 LE |
| Method Signature | N bytes | UTF-8 string |

---

## Common Performance Patterns

| Pattern | Indicator | Suggestion |
|---------|-----------|------------|
| Main thread idle | High `MessageQueue.next` | Normal idle state |
| JSON serialization | `JSONObject.put`, Gson reflection | Use code-gen (Moshi, kotlinx.serialization) |
| Reflection overhead | `java.lang.reflect.*` in hot paths | Use code generation |
| I/O on main thread | File/network ops on main thread | Move to background thread |
| Vendor SDK overhead | OEM code (vivo, huawei) | Disable or reduce frequency |
| Database contention | Room/SQLite high frequency | Optimize query frequency |
| Logging overhead | FilePrinter in hot paths | Async batch logging |

## License

MIT
