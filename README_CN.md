# BTrace Analyzer

[English](README.md)

一个用于分析 BTrace/xTrace Android 性能采样 trace 文件的工具集。提供**三个完全独立的工具**，按需选用。

## 这是什么？

BTrace/xTrace 是部分 Android 性能监控 SDK（如 `BTraceMonitor`）使用的自定义二进制采样 trace 格式。这些文件**不是**标准的 Perfetto/systrace 格式，无法直接在 Perfetto UI 中加载。

## 三个独立工具

三个工具完全独立，互不依赖。使用其中任意一个，无需安装或配置其他两个。

| 工具 | 类型 | 功能 | 依赖 |
|------|------|------|------|
| [`btrace-analyzer`](skills/btrace-analyzer/SKILL.md) | AI 技能 | 文本分析 — 直接下载并解析二进制，在对话中输出 CPU 热点、Inclusive Time 和热调用栈 | Python 3、curl |
| [`btrace-perfetto-viewer`](skills/btrace-perfetto-viewer/SKILL.md) | AI 技能 | 可视化分析 — 转换为 Perfetto protobuf，通过 MCP 浏览器在 Perfetto UI 中打开，运行 SQL 诊断，生成带截图的 Markdown 报告 | Java 8+、`btrace.jar`、`chrome-devtools-mcp` |
| [`perfetto-trace-analyzer-extension`](perfetto-trace-analyzer-extension/) | Chrome 扩展 | 浏览器内分析 — 对已在 Perfetto UI 中加载的 trace 执行 SQL 诊断，展示带定位按钮的问题列表，导出 Markdown 报告 | Chrome 浏览器 |

### 如何选择

- **btrace-analyzer** — 最快，无需任何配置，几秒内得到 CPU 热点
- **btrace-perfetto-viewer** — 需要可视化时间线、截图和保存报告时使用；需要 `btrace.jar`
- **perfetto-trace-analyzer-extension** — 已在 Perfetto UI 中打开 trace 时使用，无需离开浏览器即可完成自动化诊断

---

## 工具一：btrace-analyzer（AI 技能）

### 安装

**Kiro — 工作区范围**（仅对当前项目生效）：

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-analyzer
```

**Kiro — 全局范围**（所有工作区均可使用）：

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-analyzer
```

**其他 AI 助手** — 将 [`skills/btrace-analyzer/SKILL.md`](skills/btrace-analyzer/SKILL.md) 作为上下文附加到对话中即可。

### 使用方法

无需任何配置，直接提供 trace 和 mapping 的 URL：

```
分析这个 BTrace trace 文件：
- Trace 文件：https://example.com/path/to/sampling
- Mapping 文件：https://example.com/path/to/sampling-mapping
```

AI 会下载两个文件，用内联 Python 解析二进制格式，输出：

- **Self Time** — 直接位于栈顶的方法（直接消耗 CPU）
- **Inclusive Time** — 出现在调用栈任意位置的方法
- **热调用栈** — 最频繁采样的执行路径（前 5 帧）

---

## 工具二：btrace-perfetto-viewer（AI 技能）

### 依赖

| 依赖 | 说明 | 安装方式 |
|------|------|----------|
| Java 8+ | btrace.jar 的运行环境 | `brew install openjdk`（macOS）或系统包管理器 |
| `btrace.jar` | 将 BTrace 二进制转换为 Perfetto protobuf | 见下方说明 |
| `chrome-devtools-mcp` | 浏览器控制 MCP 服务 | `npx -y chrome-devtools-mcp@latest` |

### 安装 btrace.jar

`btrace.jar` 是 RheaTrace/BTrace trace 处理器，随字节跳动 APM SDK 工具链分发，**不在 Maven 或 npm 上公开发布**。通过以下渠道获取：

1. **从 APM SDK 包中获取** — 如果你的应用集成了 BTrace/RheaTrace SDK，JAR 文件通常在 SDK 的 tools 目录下
2. **从团队内部工具链获取** — 向你的性能/APM 团队索取最新版本
3. **从 RheaTrace 开源项目构建** — 如果团队使用开源版本，可从 [github.com/bytedance/btrace](https://github.com/bytedance/btrace) 自行构建

获取 JAR 后，放置到固定路径：

```bash
mkdir -p ~/.btrace-analyzer
cp /path/to/btrace.jar ~/.btrace-analyzer/btrace.jar
```

验证是否可用：

```bash
java -jar ~/.btrace-analyzer/btrace.jar --help
```

### 配置

复制示例配置文件并填入你的值：

```bash
cp config.local.json.example config.local.json
```

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

### 安装

**Kiro — 工作区范围**：

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-perfetto-viewer
```

**Kiro — 全局范围**：

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-perfetto-viewer
```

**其他 AI 助手** — 将 [`skills/btrace-perfetto-viewer/SKILL.md`](skills/btrace-perfetto-viewer/SKILL.md) 作为上下文附加到对话中即可。

### 使用方法

```
可视化分析这个 trace 文件：
- Trace 文件：https://example.com/path/to/sampling
- Mapping 文件：https://example.com/path/to/sampling-mapping
```

AI 会自动：
1. 下载 trace 和 mapping 文件
2. 通过 `btrace.jar` 转换为 Perfetto protobuf 格式
3. 启动本地 HTTP 服务并在 MCP 控制的浏览器中打开 Perfetto UI
4. 运行 SQL 诊断查询（长耗时 slice、帧卡顿、CPU 密集型方法、主线程 I/O）
5. 追溯每个问题的调用栈
6. 跳转到每个问题的精确时间范围并截图
7. 生成按优先级排序的报告到 `trace-analysis/<traceID>/report.md`

---

## 工具三：perfetto-trace-analyzer-extension（Chrome 扩展）

此工具完全在浏览器中运行，**不需要 `btrace.jar` 或任何 AI 技能**，分析的是已在 Perfetto UI 中加载的 trace。

### 安装

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启右上角的**开发者模式**
3. 点击**加载已解压的扩展程序**，选择 `perfetto-trace-analyzer-extension/` 目录
4. 扩展图标出现在工具栏，仅在 `ui.perfetto.dev` 页面上激活

### 使用方法

1. 打开 `https://ui.perfetto.dev` 并加载你的 trace 文件（支持 Perfetto 原生格式，也支持 `btrace-perfetto-viewer` 输出的 `.pb` 文件）
2. 点击工具栏中的 **Perfetto Trace Analyzer** 扩展图标
3. 点击**开始分析**
4. 扩展自动执行四类诊断查询并追溯每个问题的调用栈，在弹窗中展示按优先级排序的问题列表
5. 点击问题旁的**定位**按钮，在新标签页中打开 Perfetto 并缩放到该问题的精确时间范围，同时在详情面板中高亮选中对应 slice
6. 点击**导出报告**，将 Markdown 报告下载为 `perfetto_analysis_report_YYYYMMDD_HHmmss.md`

> CPU 密集型问题为多次调用的聚合统计，没有单一时间戳，显示**聚合问题**标签而非定位按钮。

报告严重等级：**P0**（>500ms）、**P1**（>200ms）、**P2**（>100ms）、**P3**（≤100ms）

---

## 文件格式参考

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

---

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

Apache License 2.0 — 详见 [LICENSE](LICENSE)。

> 注意：`btrace.jar` 是字节跳动 APM SDK 团队分发的独立工具，**不在本许可证覆盖范围内**。其使用条款请参考你的 APM SDK 协议。
