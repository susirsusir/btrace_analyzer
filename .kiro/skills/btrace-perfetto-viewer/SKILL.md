---
name: btrace-perfetto-viewer
description: Open BTrace/xTrace Android sampling trace files in Perfetto UI for visual analysis. Use when the user wants to visually inspect a btrace, xtrace, or sampling trace in Perfetto, or when they provide a trace URL and want it opened in the browser. Converts BTrace binary format to Perfetto protobuf and loads it in ui.perfetto.dev via MCP browser.
---

## Overview

This skill converts BTrace/xTrace sampling trace files to Perfetto protobuf format and opens them in https://ui.perfetto.dev/ for visual timeline analysis. It uses the locally installed `btrace.jar` for format conversion and the `chrome-devtools-mcp` to open Perfetto UI in the MCP-controlled browser instance.

## Prerequisites

- Java 8+ installed and available in PATH
- `btrace.jar` — the BTrace trace processor JAR (user must configure the path)
- `chrome-devtools-mcp` configured in `.kiro/settings/mcp.json`

## Configuration

The skill reads configuration from `config.local.json` in the workspace root:

```json
{
  "btrace_jar_path": "~/.btrace-analyzer/btrace.jar",
  "app_package_name": "com.example.myapp"
}
```

Before first use, copy `config.local.json.example` to `config.local.json` and fill in your values. This file is git-ignored.

To read the config, use:

```bash
cat config.local.json
```

Then extract `btrace_jar_path` and `app_package_name` from the JSON. If the file doesn't exist, ask the user to create it from the example.

## Workflow

### Step 1: Download trace and mapping files

Download both files to `/tmp/btrace_work/`:

```bash
mkdir -p /tmp/btrace_work
curl -L -o /tmp/btrace_work/sampling.bin "<trace_url>"
curl -L -o /tmp/btrace_work/sampling-mapping.bin "<trace_url>-mapping"
```

If the trace URL already ends with `/sampling`, the mapping URL is `<trace_url>-mapping`.
If the user provides a base directory URL, append `/sampling` and `/sampling-mapping`.

IMPORTANT: The mapping file MUST be named `sampling-mapping.bin` (matching the main file name with `-mapping` suffix). `btrace.jar` derives the mapping path from the main file name.

### Step 2: Convert to Perfetto protobuf format

Use `btrace.jar` to convert:

```bash
java -jar <BTRACE_JAR_PATH> \
  -onlyDecode \
  -a <APP_PACKAGE_NAME> \
  -t 10 \
  -mode perfetto \
  -o /tmp/btrace_work/sampling.pb \
  /tmp/btrace_work/sampling.bin \
  /tmp/btrace_work/sampling-mapping.bin
```

Replace `<BTRACE_JAR_PATH>` and `<APP_PACKAGE_NAME>` with the user's configured values.

Expected output should end with `writing trace:/tmp/btrace_work/sampling.pb`. The resulting `.pb` file is typically 80-100MB.

### Step 3: Start local HTTP server

Before starting the server, kill any process already occupying port 9001 to avoid conflicts with other tools (e.g., HBAPM plugin) that use the same port:

```bash
lsof -ti:9001 | xargs kill -9 2>/dev/null; true
```

Start a background HTTP server to serve the `.pb` file to Perfetto UI. The server MUST:
- Listen on `127.0.0.1:9001`
- Set `Access-Control-Allow-Origin: https://ui.perfetto.dev` header (required for CORS)
- Set `Cache-Control: no-cache` header
- Serve files from `/tmp/btrace_work/`

Use `controlBashProcess` with action "start" to run:

```python
python3 -c "
import http.server, socketserver, os
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', 'https://ui.perfetto.dev')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()
    def do_POST(self):
        self.send_error(404)
os.chdir('/tmp/btrace_work')
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(('127.0.0.1', 9001), H)
print('HTTP server started on port 9001', flush=True)
httpd.serve_forever()
"
```

### Step 4: Open Perfetto UI in MCP browser

Use the MCP `new_page` tool to open Perfetto with the local URL:

```
url: https://ui.perfetto.dev/#!/?url=http://127.0.0.1:9001/sampling.pb
timeout: 30000
```

CRITICAL: Do NOT use `open_trace_in_browser.py` or `webbrowser.open()`. These open the page in the user's default browser (instance A), but MCP controls a separate Chrome instance (instance B). Opening in A and observing in B causes the "two Chrome instances" problem where the loaded trace is invisible to MCP.

### Step 5: Verify and interact

After the page loads, the URL should change to `#!/viewer?local_cache_key=...` indicating the trace was successfully parsed. You can then:
- Take screenshots to show the user the timeline
- Use Perfetto's SQL query mode for programmatic analysis
- Navigate the timeline using MCP click/scroll tools

### Step 6: Analyze and diagnose issues

After the trace is loaded in Perfetto, perform a systematic analysis to identify performance issues. Use Perfetto's SQL query mode (click the search bar and type `:` to enter SQL mode) to run diagnostic queries.

#### 6.1 Identify long-running slices on the main thread

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms, s.ts
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE t.name = 'main' OR t.tid = (SELECT pid FROM process LIMIT 1)
ORDER BY s.dur DESC
LIMIT 30
```

#### 6.2 Find frame jank (frames exceeding 16.6ms)

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms, s.ts
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE (t.name = 'main' OR t.is_main_thread = 1)
  AND s.name LIKE '%doFrame%' OR s.name LIKE '%Choreographer%' OR s.name LIKE '%traversal%'
  AND s.dur > 16600000
ORDER BY s.dur DESC
LIMIT 20
```

#### 6.3 Identify CPU-heavy methods

```sql
SELECT s.name, COUNT(*) AS count, SUM(s.dur) / 1000000.0 AS total_ms, AVG(s.dur) / 1000000.0 AS avg_ms
FROM slice s
GROUP BY s.name
HAVING count > 5
ORDER BY total_ms DESC
LIMIT 30
```

#### 6.4 Check for I/O on main thread

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE (t.name = 'main' OR t.is_main_thread = 1)
  AND (s.name LIKE '%Binder%' OR s.name LIKE '%sqlite%' OR s.name LIKE '%SharedPreferences%'
       OR s.name LIKE '%File%' OR s.name LIKE '%network%' OR s.name LIKE '%http%')
ORDER BY s.dur DESC
LIMIT 20
```

#### 6.5 Trace call stacks for each issue

For each identified problem slice, walk up the `parent_id` chain to find the business code that triggered it. Use `evaluate_script` to run this in Perfetto's JS context:

```javascript
async () => {
  const engine = window.app.trace.engine;
  const traceStack = async (whereClause) => {
    const q1 = await engine.query(
      "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id, -1) AS TEXT) as pid, name " +
      "FROM slice WHERE " + whereClause + " ORDER BY dur DESC LIMIT 1"
    );
    let sliceId, parentId, name;
    for (const it = q1.iter({id:'str', pid:'str', name:'str'}); it.valid(); it.next()) {
      sliceId = it.id; parentId = it.pid; name = it.name;
    }
    if (!sliceId) return [];
    const stack = [{name}];
    let currentPid = parentId;
    for (let i = 0; i < 25 && currentPid && currentPid !== '-1'; i++) {
      const q = await engine.query(
        "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id,-1) AS TEXT) as pid, name " +
        "FROM slice WHERE id = " + currentPid
      );
      let found = false;
      for (const it = q.iter({id:'str', pid:'str', name:'str'}); it.valid(); it.next()) {
        stack.push({name: it.name}); currentPid = it.pid; found = true;
      }
      if (!found) break;
    }
    return stack;
  };
  // Call for each issue:
  return await traceStack("name LIKE '%keyword%'");
}
```

IMPORTANT: Use `COALESCE(parent_id, -1)` because root slices have NULL parent_id.

In the report, include the full call stack for each issue, highlighting:
- The **root business caller** (the app-specific class that initiated the operation)
- The **problematic method** (the one consuming the most time)
- Any **intermediate framework/library calls** that connect them

#### 6.5 Take screenshots for each issue

For each identified issue, use the SQL query results (ts and dur) to navigate Perfetto's timeline to the exact time range, then capture a screenshot.

**Navigation method** — Use `evaluate_script` to call Perfetto's internal timeline API:

```javascript
() => {
  const vw = window.app.trace.timeline._visibleWindow;
  const HPT = vw.start.constructor;   // HighPrecisionTime
  const HPTS = vw.constructor;        // HighPrecisionTimeSpan
  
  // ts and dur from SQL query (nanoseconds, as BigInt)
  const ts = <TS_VALUE>n;
  const dur = <DUR_VALUE>n;
  const padding = dur / 3n;  // 33% padding on each side
  
  const startTime = new HPT({ integral: ts - padding, fractional: 0 });
  const totalDur = Number(dur + padding * 2n);
  window.app.trace.timeline.setVisibleWindow(new HPTS(startTime, totalDur));
  return 'navigated';
}
```

Replace `<TS_VALUE>` and `<DUR_VALUE>` with the `ts` and `dur` values from the SQL query results (CAST AS TEXT to get the raw nanosecond values).

**Steps for each issue:**
1. Run SQL to get the longest/worst slice: `SELECT CAST(s.ts AS TEXT) as ts_str, CAST(s.dur AS TEXT) as dur_str FROM slice s WHERE s.name LIKE '%keyword%' ORDER BY s.dur DESC LIMIT 1`
2. Use `evaluate_script` with the navigation code above to jump to that time range
3. Wait 1 second for the timeline to render
4. Use `take_screenshot` with `filePath` to save to `trace-analysis/screenshot_<N>_<name>.png`

IMPORTANT:
- Use `CAST(ts AS TEXT)` in SQL because ts/dur are int64 and need BigInt in JS
- The padding (dur/3) ensures the slice is visible with context on both sides
- Always wait at least 1 second after navigation before taking the screenshot

#### 6.6 Generate analysis report

Each analysis session gets its own directory under `trace-analysis/` to support multiple analyses without overwriting previous results.

**Directory naming**: `trace-analysis/<traceID>/` where `<traceID>` is extracted from the trace URL. For example, URL `https://cfile.jiaoliuqu.com/xtrace_bgigfacajbjdbcbh_toutiao70_8581_.../sampling` produces directory `trace-analysis/xtrace_bgigfacajbjdbcbh_toutiao70_8581_...`.

If the traceID cannot be extracted, use a timestamp: `trace-analysis/analysis_YYYYMMDD_HHmmss/`.

Create the report at `trace-analysis/<traceID>/report.md` with all screenshots saved in the same directory. The report MUST include:

```markdown
# BTrace Performance Analysis Report

**Trace file**: <trace_url>
**Device**: <extracted from trace if available>
**Date**: <current date>

## Summary

<1-2 sentence overview of the trace health and key findings>

## Issues

### [P0] Issue Title
- **What**: Brief description
- **Where**: `com.example.ClassName.method()`
- **Duration**: Xms (single) or Xms avg × N occurrences
- **Impact**: What the user experiences
- **Call Stack**:
  ```
  com.example.BusinessClass.entryMethod()          ← business entry point
    → com.library.SomeClass.intermediateCall()
      → com.problematic.Class.slowMethod()          ← bottleneck
  ```
- **Suggestion**: Concrete fix referencing the specific class/method to modify

![Issue 1 - Description](screenshot_1.png)

### [P1] Issue Title
...

## Timeline Overview

![Full timeline](screenshot_overview.png)
```

Rules for the report:
- Create `trace-analysis/<traceID>/` directory for each analysis session
- Save all screenshots into the same `trace-analysis/<traceID>/` directory
- Use relative paths for images in the markdown so they render on GitHub
- Order issues strictly by severity (P0 first, P3 last)
- Include a full timeline overview screenshot at the end
- If no significant issues are found, state the trace is healthy with a summary screenshot
- `trace-analysis/` is already in `.gitignore` since reports contain local analysis artifacts

**Method name interpretation rules** — BTrace mapping files restore obfuscated names, but some patterns still need human-readable annotation in the report:

| Pattern in trace | Meaning | How to annotate in report |
|---|---|---|
| `ClassName$methodName$1$2.invokeSuspend()` | Kotlin coroutine lambda: 2nd nested lambda inside `methodName` | Add comment: `← Kotlin lambda in ClassName.methodName()` |
| `ClassName$methodName$1.invokeSuspend()` | Kotlin suspend lambda / coroutine continuation | Add comment: `← coroutine continuation of ClassName.methodName()` |
| `ClassName$propertyName$2.invoke()` | Kotlin lazy property initializer for `propertyName` | Add comment: `← lazy init of ClassName.propertyName` |
| `ClassName$1.run()` | Anonymous Runnable/Thread defined in ClassName | Add comment: `← anonymous Runnable in ClassName` |
| `ClassName.a3()` / `.v3()` / `.F3()` | ProGuard-obfuscated method name (single letter + digit) | Note as `← obfuscated method (ProGuard)`, do NOT guess original name |
| `msdocker.*` / `Ill111l` / `illi` | Security/plugin framework internals (DroidPlugin + string encryption) | Describe as `← DroidPlugin hook / string decryption` |

When writing call stacks in the report, always annotate Kotlin-generated class names with their semantic meaning using `←` comments so readers understand what the synthetic class represents.

### Step 7: Cleanup

After analysis is complete, stop the HTTP server background process using `controlBashProcess` with action "stop".

## Execution Constraints

- NEVER create temporary files in the user's workspace directory. All files go to `/tmp/btrace_work/`.
- NEVER use `open_trace_in_browser.py` or `webbrowser.open()` — always use MCP `new_page` to open Perfetto.
- ALWAYS start the HTTP server as a background process (it needs to stay running while Perfetto loads the file).
- ALWAYS stop the HTTP server after analysis is complete.
- The HTTP server port 9001 is hardcoded in Perfetto's CSP allowlist, do not change it.

## Troubleshooting

- If `btrace.jar` reports `NoSuchFileException` for the mapping file, check that the mapping file name matches `<main_file_name>-mapping.bin`.
- If Perfetto shows "Cannot open this file", the conversion failed or the file is still in raw BTrace format (not `.pb`).
- If the HTTP server fails to start on port 9001, kill any existing process on that port: `lsof -ti:9001 | xargs kill -9`.
- If MCP cannot see the Perfetto page, ensure you used MCP `new_page` and not `webbrowser.open()`.
