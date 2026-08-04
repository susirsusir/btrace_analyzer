---
name: hprof-analyzer
description: 分析 Android hprof 堆转储文件以检测内存泄漏、分析对象分布并生成全面的内存报告。当用户提供 .hprof 文件路径、想要分析内存泄漏或需要堆转储检查时使用。支持标准 hprof-heap 和 Android hprof-libs 两种格式。
---

## 概述

本技能分析 Android `.hprof` 堆转储文件，检测内存泄漏、分析对象分布并生成全面的内存报告。支持 **标准 hprof-heap 格式**和 **Android hprof-libs 格式**（Android 7.0+，现代 Android 设备默认格式）。

## 分析架构

本技能采用 hprof-conv + 标准 HPROF 解析 + DuckDB 分析的统一路径：

1. **转换**：如果检测到 Android hprof-libs 格式，使用 `hprof-conv` 转换为标准 JVM HPROF 格式
2. **解析**：使用 `StandardHprofParser` 解析标准 HPROF，提取全部对象、GC Root、静态字段、数组数据
3. **写入**：通过 `write_standard_parser_data` 按类分文件写入 Parquet
4. **分析**：使用 DuckDB 查询 Parquet 数据进行内存分析
5. **报告**：生成单一综合报告

> 如果 `references/HeapDumpStarDiver` 二进制可用，将作为加速器使用（6 秒 vs 9 分钟），输出格式完全一致。

无论项目目录中是否已有 Parquet 数据，都会重新执行转换，确保分析基于最新数据。

## 输入要求

- 一个 `.hprof` 文件（本地文件，位于 `<project_root>/hprof/<hprof_name>.hprof`）

## 输出目录结构

```
<project_root>/hprof_analysis/<hprof_name>/
├── raw/                    — 原始 hprof 副本
├── standard/              — hprof-conv 转换的标准 HPROF
├── parquet/                — Parquet 结构化数据（按类分文件）
│   ├── _gc_roots.parquet
│   ├── _static_fields.parquet
│   ├── _object_arrays.parquet
│   ├── _primitive_arrays_byte.parquet
│   ├── java.lang.String_<id>.parquet
│   ├── com.xmhaibao.urd.bean.ResourceItemBean_<id>.parquet
│   └── ... (每类一个文件)
└── <hprof_name>_report.md  — 分析报告
```

---

## 解析流程

### 步骤 1: 格式检测与转换

```python
from skills.hprof_analyzer.lib.standard_parser import is_hprof_libs, convert_to_standard

if is_hprof_libs(hprof_path):
    # Android hprof-libs 格式 → 用 hprof-conv 转换
    convert_to_standard(hprof_path, standard_hprof_path)
```

### 步骤 2: 解析标准 HPROF

`StandardHprofParser` 解析标准 HPROF 格式，提取：

- **STRING_DUMP (0x01)**: 106K+ 字符串
- **LOAD_CLASS (0x02)**: 21K+ 类定义（类名映射）
- **HEAP_DUMP (0x0C/0x0E)**: 子记录解析

HEAP_DUMP 子记录 tag 映射（基于 jvm-hprof 源码）：

| Tag | 类型 | 格式 |
|-----|------|------|
| 0xFF | GcRootUnknown | obj_id(id_size) |
| 0x08 | GcRootThreadObj | obj_id + thread_serial(u32) + stack_trace(u32) |
| 0x01 | GcRootJniGlobal | obj_id + ref_id(id_size) |
| 0x02 | GcRootJniLocalRef | obj_id + thread_serial(u32) + frame_index(opt u32) |
| 0x03 | GcRootJavaStackFrame | obj_id + thread_serial(u32) + frame_index(opt u32) |
| 0x04 | GcRootNativeStack | obj_id + thread_serial(u32) |
| 0x05 | GcRootSystemClass | obj_id(id_size) |
| 0x06 | GcRootThreadBlock | obj_id + thread_serial(u32) |
| 0x07 | GcRootBusyMonitor | obj_id(id_size) |
| 0x20 | Class | obj_id + stack_trace + super + loader + ... + static_fields + instance_fields |
| 0x21 | Instance | obj_id + stack_trace(u32) + class_obj_id + num_bytes(u32) + field_data |
| 0x22 | ObjectArray | obj_id + stack_trace(u32) + num_elements(u32) + array_class_id + elements |
| 0x23 | PrimitiveArray | obj_id + stack_trace(u32) + num_elements(u32) + type(u8) + data |

> **注意**: tag 0xFF 是 GcRootUnknown，不是 HEAP_DUMP_END！不要在遇到 0xFF 时停止解析。

HPROF 类型码大小映射：

| 类型码 | 类型 | 大小(字节) |
|--------|------|-----------|
| 2 | object | id_size (通常 4) |
| 4 | boolean | 1 |
| 5 | char | 2 |
| 6 | float | 4 |
| 7 | double | 8 |
| 8 | byte | 1 |
| 9 | short | 2 |
| 10 | int | 4 |
| 11 | long | 8 |

### 步骤 3: Parquet 输出

`write_standard_parser_data()` 按类分文件写入 Parquet，兼容 HeapDumpStarDiver schema：

- 每个类一个 `.parquet` 文件（文件名: `ClassName_classObjId.parquet`）
- `_gc_roots.parquet`: root_type, obj_id, thread_serial, frame_index
- `_static_fields.parquet`: class_name, field_name, field_type, ref_id
- `_object_arrays.parquet`: obj_id, class_name, elements
- `_primitive_arrays_*.parquet`: obj_id（按类型分文件）

### 步骤 4: DuckDB 分析

```python
import duckdb

con = duckdb.connect()
parquet_dir = "<project_root>/hprof_analysis/<hprof_name>/parquet"

# 按类统计对象数（读所有非 _ 前缀的 parquet 文件）
class_files = [f for f in glob.glob(f'{parquet_dir}/*.parquet') if not basename(f).startswith('_')]
for f in class_files:
    cn = basename(f).rsplit('_', 1)[0]
    cnt = con.execute(f"SELECT COUNT(*) FROM read_parquet('{f}')").fetchone()[0]

# GC Root 分布
gc_dist = con.execute(f"""
    SELECT root_type, COUNT(*) FROM read_parquet('{parquet_dir}/_gc_roots.parquet')
    GROUP BY root_type ORDER BY COUNT(*) DESC
""").fetchall()

# 静态字段持有者
sf_holders = con.execute(f"""
    SELECT class_name, COUNT(*) FROM read_parquet('{parquet_dir}/_static_fields.parquet')
    WHERE ref_id > 0 GROUP BY class_name ORDER BY COUNT(*) DESC LIMIT 50
""").fetchall()
```

---

## 使用方式

```python
from skills.hprof_analyzer.analyzer import analyze_hprof

result = analyze_hprof('taqu_android_client_logfile_401_1784076054085_1_1_112892590')
```

或命令行：

```bash
python skills/hprof_analyzer/tools/hprof_analyzer.py <hprof_file> --output-dir hprof_analysis/<name>
```

## 依赖

- **Python 3.10+**
- **duckdb** (`pip install duckdb`)
- **pyarrow** (`pip install pyarrow`)
- **hprof-conv** (Android SDK platform-tools)
- **HeapDumpStarDiver** (可选加速器, 位于 `references/HeapDumpStarDiver`)

## 质量评估标准

分析完成后，在报告末尾包含质量评分表：

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
- **ProGuard 混淆**：单个字母方法名（如 `a()`、`b()`）加数字后缀是 ProGuard 混淆的。
- **hprof-libs 格式**：Android 7.0+ 的 hprof-libs 格式与标准 hprof-heap 不同，必须先用 `hprof-conv` 转换。
- **HEAP_DUMP 中的 0x3F 填充**：hprof-conv 输出的 HEAP_DUMP payload 中可能包含 0x3F 0x00 填充模式，解析时需要跳过。
