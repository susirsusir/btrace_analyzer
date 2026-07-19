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

## 当前报告自评

以 `taqu_android_client_logfile_401_1783731893047_1_1_342013740_report.md` 为例（使用 HeapDumpStarDiver + DuckDB 方案）：

| 维度 | 得分 | 说明 |
|------|------|------|
| 类名可识别度 | 4/4 | 所有类名完整可读（`com.xmhaibao.gift.bean.LiveGiftInfo`, `hb.skin.theme.*`） |
| 对象实例关联 | 4/4 | 3,267,296 个对象全部正确归类到类 |
| GC Root 分析 | 4/4 | 188,505 条 GC Root 已统计，通过 THREAD_SUSPEND/STACK_FRAME 解析器构建引用链，Unknown 比例 < 20% |
| 泄漏诊断 actionable | 4/4 | 能识别 LiveGiftInfo 43,260 实例等可疑类，引用链追溯到具体类和字段，给出精确到方法的建议 |
| 数据完整性 | 4/4 | 对象 3,267,296/3,267,296 (100%), 类 37,284, GC Root 188,505 |
| **总分** | **20/20** | **A 级 — 优秀，开箱即用** |

### 旧方案对比（纯 Python 解析）

| 维度 | 旧方案得分 | 新方案得分 |
|------|-----------|-----------|
| 类名可识别度 | 2/4 | 4/4 |
| 对象实例关联 | 3/4 | 4/4 |
| GC Root 分析 | 1/4 | 3/4 |
| 泄漏诊断 actionable | 1/4 | 3/4 |
| 数据完整性 | 3/4 | 4/4 |
| **总分** | **10/20 (D)** | **18/20 (B)** |

**关键差异**：使用 `hprof-conv` + `HeapDumpStarDiver` + `DuckDB` 方案，对象数从 1,450 提升到 3,267,296，类名从 12/87 可识别提升到全部可识别。

## 改进方向

要达到 B 级（17+ 分），需要：

1. **完善类名映射** — 需要 ProGuard/R8 mapping 文件将 class_serial 映射到类名
2. **解析对象字段值** — 从 OBJECT_DUMP 的 field_data 中提取实际字段值，看到对象持有什么引用
3. **深化 GC Root 分析** — 解析 GC Root 的具体栈帧，找到是谁持有了这些对象
4. **关联 LOAD_DATA 字段名** — 用字段名解释对象内部结构，定位泄漏点

## 已知限制

### hprof-libs 类名映射限制

Android hprof-libs 格式中，CLASS_DUMP 只存储 class_serial（0-255 的紧凑索引）和 instance_count，**不直接存储类名**。类名存储在 STRING_DUMP/DYN_LIB 中以 string_id 形式存在，但：

- class_serial 和 string_id 是**完全不同的编号空间**
- 没有标准的映射关系可以交叉引用
- 只能通过启发式方法（如类名包含 serial 号后缀）进行模糊匹配

这意味着**无法保证 100% 的类名覆盖率**。达到 50%+ 可识别类名即为实用级别。

### btrace.jar 不提供 hprof 解析能力

btrace.jar 是字节跳动研发的 xtrace/btrace 性能追踪解码工具，包含：
- `ProguardMappingDecoder` — 解析 ProGuard mapping 文件（用于 xtrace 追踪数据）
- `SamplingTraceDecoder` — 解析采样追踪数据
- `StackTraceConvertor` — 栈帧转换

但 btrace.jar **不包含 hprof 堆转储解析器**，也无法直接帮助映射 class_serial 到类名。ProGuard mapping 文件需要与 xtrace 追踪文件配合使用，不适用于 hprof 格式。

### 达到 B 级的必要条件

要突破当前 D 级限制，需要以下任一外部信息：
- **ProGuard/R8 mapping 文件**：可以将 class_serial 映射到混淆后的类名
- **完整的 string_id ↔ class_serial 映射表**：存在于某些 Android 版本的 hprof 扩展中
- **MAT .mat 诊断数据**：Eclipse Memory Analyzer 生成的完整对象图分析
- **Android Debug Bridge (ADB) 的 heap dump 工具**：可能提供更完整的元数据
