# HPROF 分析报告质量评估标准

## 核心原则

报告必须能直接指导开发者定位和修复内存问题，做到"看到报告就能动手改代码"。

## 评估维度

### 1. 类名可识别度（关键）

| 等级 | 标准 | 示例 |
|------|------|------|
| ❌ 不可用 | 类名全是 `class_serial_XX`，无法识别 | `class_serial_29` |
| ⚠️ 勉强可用 | 部分类有名字，大部分还是 serial | 30% 有名字 |
| ✅ 可用 | 绝大多数类名可识别，包含完整包名 | `com.taqu.ui.renderViewCallback` |
| ✅✅ 优秀 | 所有类名完整可读，ProGuard 混淆类已标注 | `com.taqu.ui.renderViewCallback` + `← obfuscated: a3()` |

**要求**: 至少达到 ✅ 级别。类名无法识别的报告没有实际指导价值。

### 2. 对象实例关联

| 等级 | 标准 |
|------|------|
| ❌ | OBJECT_DUMP 和 CLASS_DUMP 的 class_serial 无法匹配 |
| ⚠️ | 能匹配但覆盖率低（< 50%） |
| ✅ | 能正确匹配，每个对象都能追溯到类名和实例数 |
| ✅✅ | 不仅能匹配，还能显示对象的字段值（如持有的引用） |

### 3. GC Root 分析深度

| 等级 | 标准 |
|------|------|
| ❌ | 只显示"JAVA_STACK: 3"，无具体栈信息 |
| ⚠️ | 有 Root 类型分布，但无引用链 |
| ✅ | 能列出 GC Root → 引用链 → 泄漏对象的具体路径 |
| ✅✅ | 引用链能追溯到具体的类和字段名 |

### 4. 泄漏诊断 actionable

| 等级 | 标准 |
|------|------|
| ❌ | 只说"有泄漏"，不说"哪里、为什么、怎么修" |
| ⚠️ | 列出了高实例数类，但没有上下文 |
| ✅ | 对每个可疑类给出：实例数、可能原因、修复建议 |
| ✅✅ | 能区分真实泄漏 vs 正常高实例（如 Adapter 缓存），给出精确到方法的建议 |

### 5. 数据完整性

| 指标 | 最低要求 | 理想目标 |
|------|----------|----------|
| 字符串解析率 | > 80% | > 95% |
| CLASS_DUMP 解析率 | > 80% | > 95% |
| OBJECT_DUMP 解析率 | > 80% | > 95% |
| GC Root 解析率 | > 50% | > 90% |

## 评分方法

每项 0-4 分，总分 20 分：

| 总分 | 评级 | 可用性 |
|------|------|--------|
| 0-8 | F | 不可用，需要重新解析 |
| 9-12 | D | 勉强可用，信息严重不足 |
| 13-16 | C | 基本可用，能指出方向但不够精确 |
| 17-19 | B | 可用，能有效指导修复 |
| 20 | A | 优秀，开箱即用 |

## 两条分析路径的独立评估

本项目有两条并行分析路径，**必须分开评估**，不能混为一谈。每份报告末尾必须包含质量评分表。

### 路径 A：Parquet/DuckDB（HeapDumpStarDiver 方案）

使用 `hprof-conv` + `HeapDumpStarDiver` + `DuckDB` 方案。当项目 `parquet/` 目录存在 `_class_hierarchy.parquet`、`_object_index_chunk*.parquet`、`_gc_roots_chunk*.parquet` 时启用。

| 维度 | 得分 | 说明 |
|------|------|------|
| 类名可识别度 | 4/4 | 所有类名完整可读（如 `com.xmhaibao.gift.bean.LiveGiftInfo`, `hb.skin.theme.*`） |
| 对象实例关联 | 4/4 | 全部对象正确归类到类名 |
| GC Root 分析 | 3-4/4 | GC Root 已统计，可通过 JOIN 查询构建引用链。Unknown 比例取决于数据质量 |
| 泄漏诊断 actionable | 4/4 | 能识别可疑类，通过静态字段查询找到持有者，给出精确到方法的建议 |
| 数据完整性 | 4/4 | 对象、类、GC Root 覆盖率接近 100% |
| **总分** | **19-20/20** | **A 级 — 优秀，开箱即用** |

### 路径 B：二进制直接解析（纯 Python 解析 hprof-libs）

始终执行，作为补充/独立分析。

| 维度 | 得分 | 说明 |
|------|------|------|
| 类名可识别度 | 0-2/4 | string_id 与 class_serial 不在同一编号空间，类名通常无法解析为 `serial_X` |
| 对象实例关联 | 0-1/4 | 大 chunk dense packed 格式未完全解析，对象覆盖率低 |
| GC Root 分析 | 0-1/4 | 仅小 chunk 的 GC Root 可解析，无法构建有意义的引用链 |
| 泄漏诊断 actionable | 0-1/4 | 类名丢失导致无法生成有意义的泄漏诊断 |
| 数据完整性 | 0-1/4 | 大 chunk 未正确解析，数据丢失严重 |
| **总分** | **0-6/20** | **F — 不可用，需要修复大 chunk 解析器** |

> **重要**：二进制路径当前质量较低，其报告主要用于：
> 1. 验证文件格式和 chunk 结构
> 2. 作为 Parquet 路径的交叉参考
> 3. 推动大 chunk 格式逆向进度
> 
> 不得将 Parquet 路径的数据混入二进制路径报告中。

## 已知限制

### hprof-libs 两种 chunk 格式

Android hprof-libs 文件中存在两种不同的 chunk 内部格式：

1. **Small chunks**（通常 ≤ 64B）：使用 marker-based 表格式（如 `00 40 00 XX` 或 `0A 7F 13` 标记），当前解析器已正确处理
2. **Large chunks**（通常 > 1KB，如 16KB、34KB、52KB）：使用 dense packed 格式，当前解析器**尚未正确实现**

这就是为什么二进制路径只能解析到约 1% 的数据——大量数据集中在大 chunk 中。

### 大 chunk 逆向状态

| Chunk | 小 chunk 状态 | 大 chunk 状态 |
|-------|--------------|--------------|
| CLASS_DUMP | ✅ 已解析 | ⚠️ 发现 5 字节记录模式（`89 6F` 终止符），但完整结构待确认 |
| OBJECT_DUMP | ✅ 已解析 | ❌ 待逆向 |
| SAMPLE_GC_HEAP | ⚠️ 部分解析 | ⚠️ 发现 20 字节条目，但大 chunk 解析不完整 |
| THREAD_SUSPEND | ⚠️ 仅解析到 6 个线程 | ❌ 大 chunk 含 ~1820 个线程条目未解析 |
| STACK_FRAME | ⚠️ 仅解析到 474 个帧 | ❌ 大 chunk 未解析 |

### btrace.jar 不提供 hprof 解析能力

btrace.jar 是字节跳动研发的 xtrace/btrace 性能追踪解码工具，包含：
- `ProguardMappingDecoder` — 解析 ProGuard mapping 文件（用于 xtrace 追踪数据）
- `SamplingTraceDecoder` — 解析采样追踪数据
- `StackTraceConvertor` — 栈帧转换

但 btrace.jar **不包含 hprof 堆转储解析器**，也无法直接帮助映射 class_serial 到类名。ProGuard mapping 文件需要与 xtrace 追踪文件配合使用，不适用于 hprof 格式。

## 改进方向

### 短期：优先使用 Parquet/DuckDB 路径生成报告

Parquet 路径已经能产出完整报告（20/20 A 级），可以作为主要分析方案。二进制路径继续完善中。

### 中期：修复二进制路径大 chunk 解析器

要达到 B 级（17+ 分），需要：

1. **完善大 chunk 解析** — 逆向 CLASS_DUMP、OBJECT_DUMP、SAMPLE_GC_HEAP、THREAD_SUSPEND、STACK_FRAME 的大 chunk dense packed 格式
2. **完善类名映射** — 需要 ProGuard/R8 mapping 文件将 class_serial 映射到类名
3. **解析对象字段值** — 从 OBJECT_DUMP 的 field_data 中提取实际字段值，看到对象持有什么引用
4. **深化 GC Root 分析** — 解析 GC Root 的具体栈帧，找到是谁持有了这些对象
5. **关联 LOAD_DATA 字段名** — 用字段名解释对象内部结构，定位泄漏点

### 长期：达到 A 级（20/20）的必要条件

- 100% 的 chunk 数据被正确解析（包括所有大 chunk）
- 类名覆盖率 > 95%
- GC Root 引用链能追溯到具体类和字段
- 报告能区分真实泄漏 vs 正常高实例
