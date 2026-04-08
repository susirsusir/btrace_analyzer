# BTrace Analyzer

[中文文档](README_CN.md)

A Kiro AI skill for analyzing BTrace/xTrace Android performance sampling trace files. It parses custom binary trace and mapping files to identify CPU hotspots, hot call stacks, and performance bottlenecks.

## What is this?

BTrace/xTrace is a custom binary sampling trace format used by some Android performance monitoring SDKs (e.g., `BTraceMonitor`). These files are **not** standard Perfetto/systrace format and cannot be loaded in Perfetto UI directly.

This project provides two [Kiro Agent Skills](https://kiro.dev/docs/skills/) for analyzing these trace files:

| Skill | Description | Dependencies |
|-------|-------------|--------------|
| `btrace-analyzer` | Text-based analysis — parses binary directly, outputs CPU hotspots and hot call stacks | None (inline Python) |
| `btrace-perfetto-viewer` | Visual analysis — converts to Perfetto protobuf, opens in Perfetto UI, runs SQL diagnostics, generates report with screenshots | Java 8+, `btrace.jar`, `chrome-devtools-mcp` |

## Features

- Parse BTrace binary trace files and mapping files
- Resolve memory addresses to Java method signatures
- Analyze self time (direct CPU consumers)
- Analyze inclusive time (methods anywhere in call stack)
- Identify hot call stacks (most frequently sampled execution paths)
- Provide actionable performance optimization suggestions
- Convert to Perfetto protobuf and open in Perfetto UI for visual analysis (requires `btrace.jar`)

## Environment Setup

The Perfetto viewer skill (`btrace-perfetto-viewer`) requires external tools that are NOT included in this repository:

| Dependency | Description | How to obtain |
|------------|-------------|---------------|
| Java 8+ | Runtime for btrace.jar | Install via your package manager |
| `btrace.jar` | Converts BTrace binary to Perfetto protobuf | Provided by your APM SDK or team toolchain |
| `chrome-devtools-mcp` | MCP server for browser control | `npx -y chrome-devtools-mcp@latest` |

After obtaining `btrace.jar`, place it in a known location (e.g., `~/.btrace-analyzer/btrace.jar`). The skill will ask for the path on first use.

The text analysis skill (`btrace-analyzer`) has no external dependencies — it parses the binary format directly with inline Python.

## Configuration

Copy the example config file and fill in your values:

```bash
cp config.local.json.example config.local.json
```

Edit `config.local.json`:

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

## File Format

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

## Installation

### As a Kiro Workspace Skill

Clone this repo into your project's `.kiro/skills/` directory:

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-analyzer
```

### As a Kiro Global Skill

Clone into your global skills directory to make it available across all workspaces:

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-analyzer
```

## Usage

### Text Analysis (btrace-analyzer)

For quick CPU hotspot analysis without external dependencies:

```
Analyze this BTrace trace file:
- Trace: https://example.com/path/to/sampling
- Mapping: https://example.com/path/to/sampling-mapping
```

Kiro will parse the binary format directly and output self time, inclusive time, and hot call stacks.

### Visual Analysis (btrace-perfetto-viewer)

For visual timeline analysis with Perfetto UI:

```
Visually analyze this trace in Perfetto:
- Trace: https://example.com/path/to/sampling
- Mapping: https://example.com/path/to/sampling-mapping
```

Kiro will:
1. Download the trace and mapping files
2. Convert to Perfetto protobuf via `btrace.jar`
3. Open Perfetto UI in the MCP-controlled browser
4. Run SQL diagnostic queries (long slices, frame jank, lock contention, I/O on main thread)
5. Navigate to each issue's exact time range and take screenshots
6. Generate a prioritized report at `trace-analysis/<traceID>/report.md`

## Analysis Output

### Text Analysis

Three key reports printed in chat:

- **Self Time**: Methods directly on top of the stack (consuming CPU)
- **Inclusive Time**: Methods appearing anywhere in the call stack
- **Hot Call Stacks**: Most frequently sampled call paths (top 5 frames)

### Visual Analysis

A markdown report with screenshots saved to `trace-analysis/<traceID>/`:

```
trace-analysis/
└── xtrace_abc123_huawei/
    ├── report.md
    ├── screenshot_overview.png
    ├── screenshot_1_ishumei.png
    ├── screenshot_2_monitor_lock.png
    └── ...
```

Each issue in the report includes severity (P0-P3), description, affected method, duration, impact, optimization suggestion, and a Perfetto timeline screenshot zoomed to the exact time range.

Multiple analyses are stored in separate subdirectories and never overwrite each other.

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
