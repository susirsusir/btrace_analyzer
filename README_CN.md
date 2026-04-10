# BTrace Analyzer

[English](README.md)

一个用于分析 BTrace/xTrace Android 性能采样 trace 文件的工具集。包含两个 Kiro AI 技能和一个 Chrome 扩展，支持自动化 Perfetto UI 分析。

## 这是什么？

BTrace/xTrace 是部分 Android 性能监控 SDK（如 `BTraceMonitor`）使用的自定义二进制采样 trace 格式。这些文件**不是**标准的 Perfetto/systrace 格式，无法直接在 Perfetto UI 中加载。

本项目提供三个工具：

| 工具 | 类型 | 说明 | 依赖 |
|------|------|------|------|
| `btrace-analyzer` | Kiro 技能 | 文本分析 — 直接解析二进制，输出 CPU 热点和热调用栈 | 无（内联 Python） |
| `btrace-perfetto-viewer` | Kiro 技能 | 可视化分析 — 转换为 Perfetto protobuf，在 Perfetto UI 中打开，运行 SQL 诊断，生成带截图的报告 | Java 8+、`btrace.jar`、`chrome-devtools-mcp` |
| `perfetto-trace-analyzer-extension` | Chrome 扩展 | 在 Perfetto UI 中对已加载的 trace 自动执行诊断分析，追溯调用栈，将 Markdown 报告保存到本地 | Chrome 浏览器 |

## 功能特性

- 解析 BTrace 二进制 trace 文件和 mapping 文件
- 将内存地址解析为 Java 方法签名
- 分析 Self Time（直接消耗 CPU 的方法）
- 分析 Inclusive Time（调用栈中出现的方法）
- 识别热调用栈（最频繁采样的执行路径）
- 提供可操作的性能优化建议
- 转换为 Perfetto protobuf 格式并在 Perfetto UI 中可视化分析（需要 `btrace.jar`）
- Chrome 扩展支持在 Perfetto UI 中一键自动化分析

## 环境配置

### Kiro 技能

Perfetto 可视化技能（`btrace-perfetto-viewer`）依赖以下外部工具，本仓库不包含这些文件：

| 依赖 | 说明 | 获取方式 |
|------|------|----------|
| Java 8+ | btrace.jar 的运行环境 | 通过包管理器安装 |
| `btrace.jar` | 将 BTrace 二进制转换为 Perfetto protobuf | 由 APM SDK 或团队工具链提供 |
| `chrome-devtools-mcp` | 浏览器控制 MCP 服务 | `npx -y chrome-devtools-mcp@latest` |

获取 `btrace.jar` 后，放置到已知路径（如 `~/.btrace-analyzer/btrace.jar`）。技能首次使用时会询问路径。

文本分析技能（`btrace-analyzer`）无外部依赖，使用内联 Python 直接解析二进制格式。

### Chrome 扩展

无外部依赖。将 `perfetto-trace-analyzer-extension/` 目录以开发者模式加载到 Chrome 即可。

## 配置

复制示例配置文件并填入你的值：

```bash
cp config.local.json.example config.local.json
```

编辑 `config.local.json`：

```json
{
  "btrace_jar_path": "~/.btrace-analyzer/btrace.jar",
  "app_package_name": "com.example.myapp"
}
```

| 字段 | 说明 |
|------|------|
| `btrace_jar_path` | `btrace.jar` 的绝对路径或 `~` 相对路径 |
| `app_package_name` | 你的 Android 应用包名 |

此文件已加入 `.gitignore`，不会被提交。

## 文件格式

### Trace 文件（`sampling`）

| 字段 | 大小 | 说明 |
|------|------|------|
| Magic | 4 字节 | `0x01020304`（小端序） |
| 保留 | 4 字节 | - |
| 版本 | 4 字节 | uint32 LE（通常为 5） |
| 时间戳 | 8 字节 | uint64 LE |
| 采样间隔 | 4 字节 | 微秒（如 25000 = 25ms） |
| JSON 长度 | 4 字节 | uint32 LE |
| JSON 元数据 | N 字节 | 如 `{"processId":11550}` |
| 采样记录 | ... | 重复至文件末尾 |

每条采样记录：

| 字段 | 大小 | 说明 |
|------|------|------|
| 标记 | 2 字节 | 记录类型（0x0002-0x0017） |
| 时间/线程数据 | 66 字节 | 时间戳、线程 ID 等 |
| 栈深度 | 4 字节 | uint32 LE |
| 栈深度（重复） | 4 字节 | uint32 LE |
| 帧地址 | depth × 8 字节 | 每个 uint64 LE |

### Mapping 文件（`sampling-mapping`）

| 字段 | 大小 | 说明 |
|------|------|------|
| 保留 | 8 字节 | 通常为 0 |
| 版本 | 4 字节 | uint32 LE（通常为 1） |
| 方法数量 | 4 字节 | uint32 LE |
| 记录 | ... | 重复 |

每条 mapping 记录：

| 字段 | 大小 | 说明 |
|------|------|------|
| 方法地址 | 8 字节 | uint64 LE |
| 字符串长度 | 2 字节 | uint16 LE |
| 方法签名 | N 字节 | UTF-8 字符串 |

## 安装

### Kiro 技能 — 工作区

将本仓库克隆到项目的 `.kiro/skills/` 目录：

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-analyzer
```

### Kiro 技能 — 全局

克隆到全局技能目录，所有工作区均可使用：

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-analyzer
```

### Chrome 扩展

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启右上角的**开发者模式**
3. 点击**加载已解压的扩展程序**，选择 `perfetto-trace-analyzer-extension/` 目录
4. 扩展图标出现在工具栏，仅在 `ui.perfetto.dev` 页面上激活

## 使用方法

### 文本分析（btrace-analyzer）

快速 CPU 热点分析，无需外部依赖：

```
分析这个 BTrace trace 文件：
- Trace 文件：https://example.com/path/to/sampling
- Mapping 文件：https://example.com/path/to/sampling-mapping
```

Kiro 会直接解析二进制格式，输出 self time、inclusive time 和热调用栈。

### 可视化分析（btrace-perfetto-viewer）

在 Perfetto UI 中进行可视化时间线分析：

```
可视化分析这个 trace 文件：
- Trace 文件：https://example.com/path/to/sampling
- Mapping 文件：https://example.com/path/to/sampling-mapping
```

Kiro 会自动：
1. 下载 trace 和 mapping 文件
2. 通过 `btrace.jar` 转换为 Perfetto protobuf 格式
3. 在 MCP 控制的浏览器中打开 Perfetto UI
4. 运行 SQL 诊断查询（长耗时 slice、帧卡顿、锁竞争、主线程 I/O）
5. 跳转到每个问题的精确时间范围并截图
6. 生成按优先级排序的报告到 `trace-analysis/<traceID>/report.md`

### Chrome 扩展（perfetto-trace-analyzer-extension）

对 Perfetto UI 中已加载的 trace 进行自动化分析：

1. 打开 `https://ui.perfetto.dev` 并加载你的 trace 文件
2. 点击工具栏中的 **Perfetto Trace Analyzer** 扩展图标
3. 点击**开始分析**
4. 扩展会自动执行四类诊断查询，追溯每个问题的调用栈，生成 Markdown 报告
5. 报告自动下载为 `perfetto_analysis_report_YYYYMMDD_HHmmss.md`

报告严重等级：**P0**（>500ms）、**P1**（>200ms）、**P2**（>100ms）、**P3**（≤100ms）

## 分析输出

### 文本分析

在聊天中输出三类关键报告：

- **Self Time**：直接位于栈顶的方法（正在消耗 CPU）
- **Inclusive Time**：出现在调用栈任意位置的方法
- **热调用栈**：最频繁采样的调用路径（前 5 帧）

### 可视化分析

生成带截图的 Markdown 报告，保存到 `trace-analysis/<traceID>/`：

```
trace-analysis/
└── xtrace_abc123_huawei/
    ├── report.md
    ├── screenshot_overview.png
    ├── screenshot_1_ishumei.png
    ├── screenshot_2_monitor_lock.png
    └── ...
```

报告中每个问题包含严重等级（P0-P3）、问题描述、涉及方法、耗时、影响、优化建议，以及缩放到精确时间范围的 Perfetto 时间线截图。

### Chrome 扩展

下载到默认下载目录的单个 Markdown 文件。每个问题包含：

- 严重等级（P0-P3）、标题、涉及方法、耗时
- 调用栈（最多 25 帧）
- 优化建议

多次分析存放在独立子目录中，互不覆盖。

## 常见性能模式

| 模式 | 特征 | 建议 |
|------|------|------|
| 主线程空闲 | `MessageQueue.next` 占比高 | 正常空闲状态 |
| JSON 序列化 | `JSONObject.put`、Gson 反射 | 使用代码生成（Moshi、kotlinx.serialization） |
| 反射开销 | 热路径中出现 `java.lang.reflect.*` | 使用代码生成替代 |
| 主线程 I/O | 主线程上的文件/网络操作 | 移到后台线程 |
| 厂商 SDK 开销 | OEM 代码（vivo、华为等） | 关闭或降低频率 |
| 数据库竞争 | Room/SQLite 高频访问 | 优化查询频率 |
| 日志开销 | 热路径中的 FilePrinter | 异步批量写入 |

## 许可证

MIT
