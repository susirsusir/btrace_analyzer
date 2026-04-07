# BTrace Analyzer

[English](README.md)

一个用于分析 BTrace/xTrace Android 性能采样 trace 文件的 Kiro AI 技能。可解析自定义二进制 trace 和 mapping 文件，定位 CPU 热点、热调用栈和性能瓶颈。

## 这是什么？

BTrace/xTrace 是部分 Android 性能监控 SDK（如 `BTraceMonitor`）使用的自定义二进制采样 trace 格式。这些文件**不是**标准的 Perfetto/systrace 格式，无法直接在 Perfetto UI 中加载。

本项目提供了一个 [Kiro Agent Skill](https://kiro.dev/docs/skills/)，让 Kiro 能够自动解析和分析这类 trace 文件。

## 功能特性

- 解析 BTrace 二进制 trace 文件和 mapping 文件
- 将内存地址解析为 Java 方法签名
- 分析 Self Time（直接消耗 CPU 的方法）
- 分析 Inclusive Time（调用栈中出现的方法）
- 识别热调用栈（最频繁采样的执行路径）
- 提供可操作的性能优化建议

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

### 作为工作区技能

将本仓库克隆到项目的 `.kiro/skills/` 目录：

```bash
cd your-project
git clone https://github.com/<your-username>/btrace_analyzer.git .kiro/skills/btrace-analyzer
```

### 作为全局技能

克隆到全局技能目录，所有工作区均可使用：

```bash
git clone https://github.com/<your-username>/btrace_analyzer.git ~/.kiro/skills/btrace-analyzer
```

## 使用方法

安装后，在聊天中提到 btrace、xtrace 或采样 trace 分析时，Kiro 会自动激活此技能。

示例提示词：

```
分析这个 BTrace trace 文件：
- Trace 文件：https://example.com/path/to/sampling
- Mapping 文件：https://example.com/path/to/sampling-mapping
```

Kiro 会自动：
1. 下载 trace 和 mapping 文件
2. 解析二进制格式
3. 生成包含 CPU 热点和热调用栈的性能分析报告
4. 提供优化建议

## 分析输出

技能会生成三类关键报告：

- **Self Time**：直接位于栈顶的方法（正在消耗 CPU）
- **Inclusive Time**：出现在调用栈任意位置的方法
- **热调用栈**：最频繁采样的调用路径（前 5 帧）

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
