---
name: hprof-analyzer
description: 分析 Android hprof 堆转储文件以检测内存泄漏、分析对象分布并生成全面的内存报告。当用户提供 .hprof 文件路径、想要分析内存泄漏或需要堆转储检查时使用。支持标准 hprof-heap 和 Android hprof-libs 两种格式。
---

## 概述

本技能分析 Android `.hprof` 堆转储文件，检测内存泄漏、分析对象分布并生成全面的内存报告。支持 **标准 hprof-heap 格式**和 **Android hprof-libs 格式**（Android 7.0+，现代 Android 设备默认格式）。

> **参考**: `android-hprof-analyzer` 技能使用 Parquet/DuckDB 方案，可作为格式逆向和报告生成的参考，但不要照抄其 MCP 工具调用方式。本技能采用纯 Python 直接解析 hprof 二进制格式。

## 分析架构

本技能采用统一的 Parquet/DuckDB 分析路径：

1. **转换**：将 `.hprof` 文件通过 Python 解析器转换为 Parquet 结构化数据
2. **分析**：使用 DuckDB 查询 Parquet 数据进行内存分析
3. **报告**：生成单一综合报告

无论项目目录中是否已有 Parquet 数据，都会重新执行转换，确保分析基于最新数据。

## 输入要求

- 一个 `.hprof` 文件（本地文件，位于 `<project_root>/hprof/<hprof_name>.hprof`）

---

## Parquet/DuckDB 分析

### 前置步骤：转换 HPROF 为 Parquet

使用 `HPROFParser` 解析原始 hprof 文件，通过 `convert_hprof_to_parquet` 生成结构化 Parquet 数据。

**解析器支持三种 chunk 格式**：
- **Marker-based** (`00 40 00 XX`)：小 chunk 的标准格式
- **Dense packed 89_6f**：大 chunk 紧凑格式，5 字节记录
- **Dense packed 89_14_cb**：大 chunk 紧凑格式，6 字节记录

转换后的文件保存在 `<project_root>/hprof_analysis/<basename>/parquet/` 目录下。

### 分析步骤

使用 DuckDB 查询 Parquet 文件生成结构化分析数据。所有 Python 代码写入 `/tmp/hprof_analysis/`，不要在工作区创建临时文件。

#### A1. 查询对象类型分布

```python
import duckdb

con = duckdb.connect()
parquet_dir = "<project_dir>/parquet"

# 总对象数
total_objects = con.execute(f"""
    SELECT COUNT(*) FROM read_parquet('{parquet_dir}/_object_index_chunk*.parquet')
""").fetchone()[0]

# Top 50 类按实例数排序
top_classes = con.execute(f"""
    SELECT type_name, COUNT(*) as instance_count
    FROM read_parquet('{parquet_dir}/_object_index_chunk*.parquet')
    GROUP BY type_name
    ORDER BY instance_count DESC
    LIMIT 50
""").fetchdf()

# 包级别分布
package_dist = con.execute(f"""
    SELECT 
        CASE 
            WHEN type_name LIKE 'com.taqu.%' THEN 'com.taqu.*'
            WHEN type_name LIKE 'com.xmhaihao.%' THEN 'com.xmhaihao.*'
            WHEN type_name LIKE 'hb.%' THEN 'hb.*'
            WHEN type_name LIKE 'androidx.%' THEN 'androidx.*'
            WHEN type_name LIKE 'android.%' THEN 'android.*'
            WHEN type_name LIKE 'java.%' THEN 'java.*'
            ELSE 'other'
        END as package_group,
        COUNT(*) as object_count
    FROM read_parquet('{parquet_dir}/_object_index_chunk*.parquet')
    GROUP BY package_group
    ORDER BY object_count DESC
    LIMIT 20
""").fetchdf()
```

#### A2. 查询 GC Root 分布

```python
# GC Root 类型分布
gc_root_dist = con.execute(f"""
    SELECT root_type, COUNT(*) as count
    FROM read_parquet('{parquet_dir}/_gc_roots_chunk*.parquet')
    GROUP BY root_type
    ORDER BY count DESC
""").fetchdf()

# JavaStackFrame 持有的 Top 类
java_stack_holders = con.execute(f"""
    SELECT oi.type_name, COUNT(*) as held_count
    FROM read_parquet('{parquet_dir}/_gc_roots_chunk*.parquet') gr
    JOIN read_parquet('{parquet_dir}/_object_index_chunk*.parquet') oi ON gr.obj_id = oi.obj_id
    WHERE gr.root_type = 'JavaStackFrame'
    GROUP BY oi.type_name
    ORDER BY held_count DESC
    LIMIT 30
""").fetchdf()

# ThreadObj 持有分析
thread_obj_analysis = con.execute(f"""
    SELECT thread_serial, COUNT(*) as held_count
    FROM read_parquet('{parquet_dir}/_gc_roots_chunk*.parquet')
    WHERE root_type = 'ThreadObj'
    GROUP BY thread_serial
    ORDER BY held_count DESC
    LIMIT 20
""").fetchdf()
```

#### A3. 查询静态字段持有者（泄漏诊断核心）

```python
def find_static_field_holders(con, parquet_dir, target_type_name, limit=20):
    """查找通过静态字段持有指定类型对象的类。"""
    result = con.execute(f"""
        SELECT sf.class_name, sf.field_name, sf.field_type, 
               COUNT(*) as ref_count
        FROM read_parquet('{parquet_dir}/_static_fields_chunk*.parquet') sf
        JOIN read_parquet('{parquet_dir}/_object_index_chunk*.parquet') oi ON sf.ref_id = oi.obj_id
        WHERE oi.type_name = '{target_type_name}'
        GROUP BY sf.class_name, sf.field_name, sf.field_type
        ORDER BY ref_count DESC
        LIMIT {limit}
    """).fetchdf()
    return result

# 对可疑类执行查询
suspicious_classes = [
    'com.xmhaibao.gift.bean.LiveGiftInfo',
    'java.util.ArrayList',
    'com.jakewharton.disklrucache.DiskLruCache$Entry',
    'sun.misc.Cleaner',
    'libcore.util.NativeAllocationRegistry$CleanerThunk',
]

for cls in suspicious_classes:
    holders = find_static_field_holders(con, parquet_dir, cls)
    if len(holders) > 0:
        print(f"\n=== {cls} 的静态字段持有者 ===")
        print(holders.to_string())
```

#### A4. 查询类层次结构

```python
live_gift_info_hierarchy = con.execute(f"""
    SELECT class_obj_id, class_name, super_class_obj_id, super_class_name
    FROM read_parquet('{parquet_dir}/_class_hierarchy.parquet')
    WHERE class_name LIKE '%LiveGiftInfo%'
    LIMIT 20
""").fetchdf()
```

#### A5. 生成 Parquet 报告

参考 [references/report-template.md](references/report-template.md) 中的完整 Markdown 报告模板。

**报告必须包含**：
1. **概要** — 整体健康状况摘要，包含关键指标表
2. **堆分布** — Top 30 对象类型、包级别分布
3. **GC Root 分析** — Root 类型分布、JavaStackFrame 持有 Top 类、ThreadObj 分析
4. **内存泄漏深度分析** — 对每个可疑类给出：
   - 实例数、估算浅层大小和持有大小
   - GC Root 持有者（SystemClass、JavaStackFrame、Unknown）
   - 静态字段持有者列表（类名、字段名、引用数）
   - 泄漏模式分类（Activity 泄漏、监听器未注销、WebView 泄漏、Handler 泄漏、静态集合膨胀等）
   - **具体修复建议**（含代码示例）
5. **线程快照** — 活跃线程和栈快照
6. **风险评级** — P0-P3 严重性评估
7. **下一步行动** — 可操作的排查步骤

**输出文件**: `hprof_analysis/<hprof文件名_无扩展名>_report.md`

示例：
- 输入：`dump.hprof` → 输出：`hprof_analysis/dump_report.md`
- 输入：`taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` → 输出：`hprof_analysis/taqu_android_client_logfile_401_1783731893047_1_1_342013740_report.md`

---

如果 `hprof_analysis/` 目录不存在，请创建它。

## 质量评估标准

分析完成后，在报告末尾包含质量评分表，格式如下：

```markdown
## 质量评估（对照 quality-standards.md）

| 维度 | 得分 | 说明 |
|------|------|------|
| 类名可识别度 | X/4 | ... |
| 对象实例关联 | X/4 | ... |
| GC Root 分析深度 | X/4 | ... |
| 泄漏诊断 actionable | X/4 | ... |
| 数据完整性 | X/4 | ... |
| **总分** | **X/20** | **评级** |
```

## 内存问题严重性分类

| 严重性 | 阈值 | 含义 |
|--------|------|------|
| P0 | > 50MB 泄漏 | 严重泄漏 — 需立即修复 |
| P1 | > 20MB 泄漏 | 显著泄漏 — 应尽快修复 |
| P2 | > 5MB 泄漏 | 中等泄漏 — 计划修复 |
| P3 | ≤ 5MB 泄漏 | 轻微 — 值得关注但优先级低 |

## 注意事项

- **Kotlin synthetic 字段**：包含 `$` 的类名（如 `MyClass$onCreate$1`）是 Kotlin 生成的。在报告中添加 `← Kotlin synthetic` 注释。
- **ProGuard 混淆**：单个字母方法名（如 `a()`、`b()`）加数字后缀（如 `a3()`）是 ProGuard 混淆的。不要猜测原始名称。
- **DroidPlugin**：`msdocker.*` 或 `Ill111l` 等不寻常命名的类是 DroidPlugin 内部组件。
- **Coroutines**：`$this$coroutineScope`、`$this$launchWhenResumed` 是 Kotlin 协程 synthetic 字段。
- **Android hprof-libs 格式**：CLASS_DUMP、LOAD_DATA 和 SAMPLE_GC_HEAP 的 chunk 格式与标准 hprof-heap 不同。当解析产生明显无效值时（例如实例数 > 100M），尝试替代的字段偏移或格式。
- **如果在文件中找不到期望的数据**，这可能意味着你的解析方法有误——尝试不同的方法，而不是得出结论说数据不存在。
