# BTrace Analyzer

[中文文档](README_CN.md)

A Kiro AI skill for analyzing BTrace/xTrace Android performance sampling trace files. It parses custom binary trace and mapping files to identify CPU hotspots, hot call stacks, and performance bottlenecks.

## What is this?

BTrace/xTrace is a custom binary sampling trace format used by some Android performance monitoring SDKs (e.g., `BTraceMonitor`). These files are **not** standard Perfetto/systrace format and cannot be loaded in Perfetto UI directly.

This project provides a [Kiro Agent Skill](https://kiro.dev/docs/skills/) that teaches Kiro how to parse and analyze these trace files automatically.

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

Once installed, Kiro automatically activates this skill when you mention btrace, xtrace, or sampling trace analysis in chat.

Example prompt:

```
Analyze this BTrace trace file:
- Trace: https://example.com/path/to/sampling
- Mapping: https://example.com/path/to/sampling-mapping
```

Kiro will:
1. Download the trace and mapping files
2. Parse the binary formats
3. Generate a performance analysis report with CPU hotspots and hot call stacks
4. Provide optimization suggestions

## Analysis Output

The skill produces three key reports:

- **Self Time**: Methods directly on top of the stack (consuming CPU)
- **Inclusive Time**: Methods appearing anywhere in the call stack
- **Hot Call Stacks**: Most frequently sampled call paths (top 5 frames)

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
