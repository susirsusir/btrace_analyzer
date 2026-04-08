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

#### 6.5 Take a screenshot for visual context

After running queries, take a screenshot of the Perfetto timeline to include in the report.

#### 6.6 Compile the analysis report

Present findings to the user as a prioritized list, ordered by severity (most impactful first):

**Priority criteria** (highest to lowest):
1. **ANR risk** — any single operation > 5000ms on main thread
2. **Frame drops** — Choreographer/doFrame/traversal exceeding 16.6ms, causing visible jank
3. **Main thread blocking** — I/O, Binder calls, database operations on main thread
4. **CPU hotspots** — methods with high total or average execution time
5. **Thread contention** — lock waits, synchronized blocks causing delays
6. **Memory pressure** — GC pauses, large allocations in hot paths
7. **Inefficient patterns** — reflection, JSON serialization, excessive logging in hot paths

**Report format:**

For each issue found, provide:
- Severity level (P0-Critical / P1-High / P2-Medium / P3-Low)
- What: brief description of the problem
- Where: the specific method/call stack
- Duration/frequency: how long and how often it occurs
- Impact: what the user would experience (jank, ANR, slow startup, etc.)
- Suggestion: concrete optimization recommendation

If no significant issues are found, state that the trace looks healthy and mention any minor observations.

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
