---
name: btrace-perfetto-viewer
description: Open BTrace/xTrace Android sampling trace files in Perfetto UI for visual analysis. Use when the user wants to visually inspect a btrace, xtrace, or sampling trace in Perfetto, or when they provide a trace URL and want it opened in the browser. Converts BTrace binary format to Perfetto protobuf and loads it in ui.perfetto.dev via MCP browser.
---

## Overview

This skill converts BTrace/xTrace sampling trace files to Perfetto protobuf format and opens them in https://ui.perfetto.dev/ for visual timeline analysis. It uses the locally installed `btrace.jar` for format conversion and `chrome-devtools-mcp` to control the browser.

## Prerequisites

- Java 8+ installed and available in PATH
- `btrace.jar` — the BTrace trace processor JAR (user must configure the path)
- `chrome-devtools-mcp` MCP server available in your AI assistant's tool configuration

## Configuration

Read from `config.local.json` in the workspace root:

```bash
cat config.local.json
```

```json
{
  "btrace_jar_path": "~/.btrace-analyzer/btrace.jar",
  "app_package_name": "com.example.myapp"
}
```

If the file doesn't exist, ask the user to copy from `config.local.json.example`.

## Workflow

### Step 1: Download trace and mapping files

```bash
mkdir -p /tmp/btrace_work
curl -L -o /tmp/btrace_work/sampling.bin "<trace_url>"
curl -L -o /tmp/btrace_work/sampling-mapping.bin "<trace_url>-mapping"
```

IMPORTANT: The mapping file MUST be named `sampling-mapping.bin`. `btrace.jar` derives the mapping path from the main filename.

### Step 2: Convert to Perfetto protobuf

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

Expected: output ends with `writing trace:/tmp/btrace_work/sampling.pb` (~80-100MB).

### Step 3: Start local HTTP server (background process)

Kill any existing process on port 9001 first:

```bash
lsof -ti:9001 | xargs kill -9 2>/dev/null; true
```

Start as a background process:

```python
python3 -c "
import http.server, socketserver, os
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', 'https://ui.perfetto.dev')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()
    def do_POST(self): self.send_error(404)
os.chdir('/tmp/btrace_work')
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(('127.0.0.1', 9001), H)
print('HTTP server started on port 9001', flush=True)
httpd.serve_forever()
"
```

### Step 4: Open Perfetto UI via MCP browser

```
new_page url: https://ui.perfetto.dev/#!/?url=http://127.0.0.1:9001/sampling.pb
timeout: 30000
```

CRITICAL: Do NOT use `webbrowser.open()`. MCP controls a separate Chrome instance — opening in the system browser makes the trace invisible to MCP tools.

Wait for URL to change to `#!/viewer?local_cache_key=...` confirming the trace loaded.

### Step 5: Diagnose issues

Run SQL diagnostics, trace call stacks, and take screenshots for each issue.

See [references/diagnostics.md](references/diagnostics.md) for all SQL queries and call stack tracing code.

### Step 6: Generate report

Save report to `trace-analysis/<traceID>/report.md` with screenshots in the same directory.

See [references/report-format.md](references/report-format.md) for the report template and method name annotation rules.

**traceID**: extracted from the trace URL (e.g. `xtrace_abc123_huawei`). Fall back to `analysis_YYYYMMDD_HHmmss` if not extractable.

### Step 7: Cleanup

Stop the HTTP server background process after analysis is complete.

## Execution Constraints

- NEVER create temporary files in the workspace. All files go to `/tmp/btrace_work/`.
- NEVER use `webbrowser.open()` — always use MCP `new_page`.
- ALWAYS start the HTTP server as a background process.
- ALWAYS stop the HTTP server after analysis.
- Port 9001 is hardcoded in Perfetto's CSP allowlist — do not change it.

## Troubleshooting

- `NoSuchFileException` for mapping → check filename is `<main>-mapping.bin`
- Perfetto "Cannot open this file" → conversion failed or file is not `.pb` format
- Port 9001 busy → `lsof -ti:9001 | xargs kill -9`
- MCP can't see Perfetto page → ensure you used MCP `new_page`, not `webbrowser.open()`
