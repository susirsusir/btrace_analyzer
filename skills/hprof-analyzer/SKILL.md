---
name: hprof-analyzer
description: 分析 Android hprof 堆转储文件以检测内存泄漏、分析对象分布并生成全面的内存报告。当用户提供 .hprof 文件路径、想要分析内存泄漏或需要堆转储检查时使用。支持标准 hprof-heap 和 Android hprof-libs 两种格式。
---

## 概述

本技能分析 Android `.hprof` 堆转储文件，检测内存泄漏、分析对象分布并生成全面的内存报告。支持 **标准 hprof-heap 格式**和 **Android hprof-libs 格式**（Android 7.0+，现代 Android 设备默认格式）。

> **参考**: `android-hprof-analyzer` 技能使用 Parquet/DuckDB 方案，可作为格式逆向和报告生成的参考，但不要照抄其 MCP 工具调用方式。本技能采用纯 Python 直接解析 hprof 二进制格式。

## 双路径分析架构

本技能支持两条独立的分析路径，**必须分别输出报告**，不能混为一谈：

| 路径 | 数据源 | 输出文件 | 适用场景 |
|------|--------|----------|----------|
| **路径 A：Parquet/DuckDB** | 项目中的 `parquet/` 目录（HeapDumpStarDiver 产物） | `hprof_analysis/<文件名>_parquet_report.md` | 项目已有 Parquet 结构化数据时优先使用 |
| **路径 B：二进制直接解析** | 原始 `.hprof` 文件，Python 直接解析 hprof-libs 二进制 | `hprof_analysis/<文件名>_binary_report.md` | 始终执行，作为补充/独立分析 |

### 路径选择规则

```
如果项目目录中存在 parquet/ 子目录且包含以下文件：
  - _class_hierarchy.parquet
  - _object_index_chunk*.parquet
  - _gc_roots_chunk*.parquet
则路径 A 可用，必须执行并输出报告。

无论路径 A 是否可用，路径 B 都必须执行并输出报告。
```

> **重要**：两条路径的数据质量可能差异巨大。Parquet 路径通常能解析完整类名和全部对象；二进制路径受限于 hprof-libs 大 chunk dense packed 格式的逆向难度，可能只能解析少量数据。**质量评估必须分开打分**，参见 [references/quality-standards.md](references/quality-standards.md)。

## 输入要求

- 一个 `.hprof` 文件路径（本地文件）
- （可选）项目中的 `parquet/` 目录，包含 HeapDumpStarDiver 转换后的结构化数据

---

## 路径 A：Parquet/DuckDB 分析（当项目有 parquet/ 数据时）

### 前置检查

在项目目录中查找 `parquet/` 子目录，确认存在以下关键文件：

```bash
ls <project_dir>/parquet/_class_hierarchy.parquet
ls <project_dir>/parquet/_object_index_chunk*.parquet
ls <project_dir>/parquet/_gc_roots_chunk*.parquet
ls <project_dir>/parquet/_static_fields_chunk*.parquet  # 可选
ls <project_dir>/parquet/_stack_traces_chunk*.parquet   # 可选
```

如果文件不存在，跳过路径 A，仅输出路径 B 报告。

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

**输出文件**: `hprof_analysis/<hprof文件名_无扩展名>_parquet_report.md`

示例：
- 输入：`dump.hprof` → 输出：`hprof_analysis/dump_parquet_report.md`
- 输入：`taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` → 输出：`hprof_analysis/taqu_android_client_logfile_401_1783731893047_1_1_342013740_parquet_report.md`

---

## 路径 B：二进制直接解析（始终执行）

### 第一步：验证文件格式

检查二进制头部以确定 hprof 格式变体：

```bash
xxd -l 32 "<hprof_file>"
```

**两种格式共享前 12 字节的 `JAVA PROFILE 1.0` magic**。区分方式是偏移 16 处的 stated header size：

- **hprof-heap（标准）**：stated header size 较小（通常 128）
- **hprof-libs（Android 7.0+）**：stated header size 较大（通常 13102+）

> **详细格式对比**：参见 [references/hprof-libs-vs-heap.md](references/hprof-libs-vs-heap.md)，了解两种格式的完整差异，包括为什么 `hprof-conv` 在转换 hprof-libs 时会丢失大量数据。

### 第二步：检测格式和记录布局

```python
import struct, sys, os

filepath = sys.argv[1]
with open(filepath, 'rb') as f:
    magic = f.read(16)
    stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
    file_size = os.path.getsize(filepath)

if stated_size > 2000:
    fmt = "hprof-libs (Android 7.0+)"
    record_start = 0x80
    tag_size = 2  # 2-byte tag
else:
    fmt = "hprof-heap (standard)"
    record_start = 16 + stated_size
    tag_size = 4  # 4-byte tag

print(f"Format: {fmt}, Records start at: 0x{record_start:X}")
```

### 第三步：扫描所有 Chunks/Records

#### 对于 hprof-libs 格式（Android 7.0+）：

记录使用 `tag(2B LE) + length(2B LE) + payload(length-4 bytes)`：

```python
import struct
from collections import Counter, defaultdict

def scan_hprof_libs_chunks(filepath):
    """扫描 Android hprof-libs 格式文件中的所有有效 chunk。"""
    valid_tags = {
        0x0000, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005,
        0x0010, 0x0011, 0x0013, 0x0014, 0x0015,
        0x0016, 0x0017, 0x0019, 0x0030, 0x0031, 0x0032,
    }
    
    file_size = os.path.getsize(filepath)
    chunks = []
    pos = 0x80
    
    while pos < file_size - 4:
        with open(filepath, 'rb') as f:
            f.seek(pos)
            header = f.read(4)
            if len(header) < 4:
                break
            tag, length = struct.unpack_from('<HH', header, 0)
        
        if tag in valid_tags and 4 <= length <= file_size - pos:
            chunks.append((pos, tag, length))
            pos += length
        else:
            pos += 1  # 向前扫描下一个有效 chunk
    
    return chunks
```

#### 对于标准 hprof-heap 格式：

记录使用 `tag(4B LE) + length(4B LE) + payload(length-8 bytes)`：

```python
def scan_standard_chunks(filepath):
    """扫描标准 hprof-heap 格式文件。"""
    valid_tags = {0x01, 0x02, 0x03, 0x04, 0x05,
                  0x10, 0x11, 0x13, 0x14, 0x15, 0x19}
    
    file_size = os.path.getsize(filepath)
    chunks = []
    
    with open(filepath, 'rb') as f:
        f.read(16)  # magic
        stated_size = struct.unpack_from('<I', f.read(4), 0)[0]
        f.seek(16 + stated_size)
        
        while f.tell() < file_size - 8:
            pos = f.tell()
            tag = struct.unpack_from('<I', f.read(4), 0)[0]
            length = struct.unpack_from('<I', f.read(4), 0)[0]
            
            if tag in valid_tags and 8 <= length <= file_size - pos:
                chunks.append((pos, tag, length))
                f.seek(pos + length)
            else:
                f.seek(pos + 1)
    
    return chunks
```

### 第四步：解析 STRING_DUMP Chunks

提取字符串表（类名、字段名、常量字符串）：

```python
def parse_string_dumps(chunks, filepath):
    """解析 STRING_DUMP chunks 并构建字符串表。"""
    strings = {}  # string_id -> text
    
    for pos, tag, length in chunks:
        if tag not in (0x0010, 0x10):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # hprof-libs STRING_DUMP 格式：
        # string_text + 0x01 + 7_zero_bytes + 1B_value + 4B_ref
        # 7个零和1B_value是元数据；4B_ref是 string_id/指针。
        # 重要：chunk 的第一个条目可能有一个垃圾首字节
        # （来自前一个 chunk 的 ref 残留），因此需要验证文本是否可打印。
        
        p = 0
        while p < len(payload) - 13:
            sep = payload.find(b'\x01', p)
            if sep == -1:
                break
            
            text_bytes = payload[p:sep]
            
            # 验证：所有字节必须是可打印 ASCII
            if len(text_bytes) > 0 and all(32 <= b < 127 for b in text_bytes):
                meta = payload[sep+1:sep+13]
                if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                    string_id = struct.unpack_from('<I', meta, 8)[0]
                    text = text_bytes.decode('utf-8')
                    strings[string_id] = text
            
            p = sep + 13
    
    return strings
```

### 第五步：解析 CLASS_DUMP Chunks

提取类元数据（serial 编号、实例数量）。

**已观察到的两种布局：**

1. **小 chunk（~64B）**：使用 `00 40 00 XX` 分隔符的 marker-based 表
2. **大 chunk（>1KB，如 @0x56bfd1 len=51989）**：紧凑排列的记录，**每条 5 字节**，以 `89 6F` 作为记录终止符/标记

```python
def parse_class_dumps(chunks, filepath):
    """解析 CLASS_DUMP chunks 获取类元数据。
    
    处理两种格式：
    
    格式 A - 小 chunk（marker-based）：
      条目格式：[type_code(1B)] [instance_count(1B)] [field_info(variable)] [00 40 00 XX]
      marker 的 XX 字节是下一个 class_serial（顺序计数器）
    
    格式 B - 大 chunk（紧凑排列）：
      固定 5 字节记录：[class_serial(1B)] [instance_count(1B)] [type_code(1B)] [0x89] [0x6F]
      无头部，记录之间无标记
    """
    classes = {}  # class_serial -> {num_instances, ...}
    
    for pos, tag, length in chunks:
        if tag not in (0x0001, 0x01):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # 检测格式：大 chunk (>1KB) 且包含 89 6F 模式的使用紧凑格式
        # 检查整个 payload 中的 89 6F 模式（不仅检查前 200 字节）
        if length > 1000 and b'\x89\x6f' in payload:
            # 格式 B：紧凑排列的 5 字节记录
            p = 0
            while p + 5 <= len(payload):
                class_serial = payload[p]
                instance_count = payload[p+1]
                type_code = payload[p+2]
                
                # 验证：type_code 应该是有效的（0x02, 0x0A, 0x0B, 等）
                if type_code in (0x02, 0x0A, 0x0B, 0x0C, 0x0D):
                    if instance_count != 0xFF:
                        classes[class_serial] = {
                            'serial': class_serial,
                            'num_instances': instance_count,
                            'type_code': type_code,
                        }
                
                p += 5
        else:
            # 格式 A：marker-based 表
            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue
            
            p = first_marker + 4  # 跳过第一个 marker
            
            while p < len(payload) - 4:
                next_marker = payload.find(b'\x00\x40\x00', p)
                if next_marker == -1:
                    break
                
                entry_data = payload[p:next_marker]
                class_serial = payload[next_marker + 3]  # marker 的 XX 字节
                
                if len(entry_data) >= 2:
                    instance_count = entry_data[1]
                    
                    # 0xFF 表示未知/特殊，跳过
                    if instance_count != 0xFF:
                        classes[class_serial] = {
                            'serial': class_serial,
                            'num_instances': instance_count,
                        }
                
                p = next_marker + 4
    
    return classes
```

### 第六步：解析 LOAD_DATA Chunks

提取类字段布局（字段名 → 偏移映射）：

```python
def parse_load_data(chunks, filepath):
    """解析 LOAD_DATA chunks 获取类字段布局。"""
    field_layouts = {}  # class_serial -> list of field info
    
    for pos, tag, length in chunks:
        if tag not in (0x0011, 0x11):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        if len(payload) < 8:
            continue
        
        class_serial = struct.unpack_from('<I', payload, 0)[0]
        object_id = struct.unpack_from('<I', payload, 4)[0]
        
        # 字段数据从偏移 8 开始
        # 在 hprof-libs 中，字段使用：field_name + 0x01 + metadata
        fields = []
        p = 8
        while p < len(payload) - 13:
            name_end = payload.find(b'\x01', p)
            if name_end == -1:
                break
            
            field_name_bytes = payload[p:name_end]
            
            # 验证：字段名必须是可打印 ASCII
            if len(field_name_bytes) > 0 and all(32 <= b < 127 for b in field_name_bytes):
                meta = payload[name_end+1:name_end+13]
                if len(meta) >= 12 and all(b == 0 for b in meta[:7]):
                    field_name = field_name_bytes.decode('utf-8')
                    fields.append({
                        'name': field_name,
                        'offset': p,
                    })
            
            p = name_end + 13
        
        if fields:
            field_layouts[class_serial] = fields
    
    return field_layouts
```

### 第七步：解析 OBJECT_DUMP Chunks

提取对象实例及其字段值。

**已观察到的两种布局：**

1. **小 chunk**：使用 `00 40 00 XX` 分隔符的 marker-based 表，每条 5-9 字节
2. **大 chunk（>1KB）**：固定宽度的紧凑排列记录，与 CLASS_DUMP 类似

```python
def parse_object_dumps(chunks, filepath):
    """解析 OBJECT_DUMP chunks 获取对象实例。
    
    处理两种格式：
    
    格式 A - 小 chunk（marker-based）：
      [entry_data(variable)] [00 40 00 class_serial(1B)]
      条目结构（通常 5-9 字节）：
        - object_id(4B LE)
        - type_code(1B) — e.g., 0x0B, 0x02, 0xFF
        - [padding/field_data(variable, 通常很小)]
    
    格式 B - 大 chunk（紧凑排列）：
      固定宽度记录，带有 00 40 00 标记或 89 6F 终止符
    """
    objects = []  # {object_id, class_serial, payload} 列表
    
    for pos, tag, length in chunks:
        if tag not in (0x0004, 0x04):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # 检测格式：大 chunk 且包含紧凑模式
        if length > 1000 and b'\x89\x6f' in payload[:200]:
            # 格式 B：紧凑排列记录（类似 CLASS_DUMP）
            # 尝试按 5 字节记录和 89 6F 终止符解析
            p = 0
            while p + 5 <= len(payload):
                # 检查预期位置的 89 6F 终止符
                if payload[p+3] == 0x89 and payload[p+4] == 0x6F:
                    obj_id = struct.unpack_from('<I', payload, p)[0]
                    class_serial = payload[p+3] if p+3 < len(payload) else 0
                    
                    if obj_id > 0:
                        objects.append({
                            'object_id': obj_id,
                            'class_serial': class_serial,
                            'payload': payload[p+5:p+9],  # 剩余字段数据
                        })
                    p += 5
                else:
                    p += 1
        else:
            # 格式 A：marker-based 表
            first_marker = payload.find(b'\x00\x40\x00')
            if first_marker == -1:
                continue
            
            p = first_marker + 4  # 跳过第一个 marker
            while p < len(payload) - 4:
                next_marker = payload.find(b'\x00\x40\x00', p)
                if next_marker == -1 or next_marker + 4 >= len(payload):
                    break
                
                entry_data = payload[p:next_marker]
                class_serial = payload[next_marker + 3]  # marker 的 XX 字节
                
                if len(entry_data) >= 4:
                    obj_id = struct.unpack_from('<I', entry_data, 0)[0]
                    objects.append({
                        'object_id': obj_id,
                        'class_serial': class_serial,
                        'payload': entry_data[4:],
                    })
                
                p = next_marker + 4
    
    return objects
```

### 第八步：解析 SAMPLE_GC_HEAP Chunks

提取 GC Root 信息和可达对象：

```python
def parse_gc_heap_samples(chunks, filepath):
    """解析 SAMPLE_GC_HEAP chunks 获取 GC Root 信息。"""
    gc_roots = []
    
    for pos, tag, length in chunks:
        if tag not in (0x0005, 0x05):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # hprof-libs SAMPLE_GC_HEAP 格式：
        # 每条 20 字节：
        #   object_id(4B LE) + root_info(4B LE) + root_kind(2B LE) +
        #   class_serial(4B LE) + pad(4B LE) + extra(2B LE)
        # root_kind 值：0=JAVA_STACK, 1=NATIVE_STACK, 2=SYSTEM_CLASS,
        #   3=GC_STATIC_FIELD, 4=GC_LOCAL, 5=GC_MONITOR, 6=GC_JAVA_FRAME,
        #   7=GC_NATIVE_FRAME, 8=UNREACHABLE, 9=DAEMON_WORKER, 10=UNKNOWN
        # class_serial 通常是一个常量系统类（如 0x78c6096f = java/lang/Object）
        
        p = 0
        while p + 20 <= len(payload):
            object_id = struct.unpack_from('<I', payload, p)[0]
            root_info = struct.unpack_from('<I', payload, p+4)[0]
            root_kind = struct.unpack_from('<H', payload, p+8)[0]
            class_serial = struct.unpack_from('<I', payload, p+10)[0]
            
            if root_kind <= 10 and object_id > 0:
                kind_names = {
                    0: 'JAVA_STACK', 1: 'NATIVE_STACK', 2: 'SYSTEM_CLASS',
                    3: 'GC_STATIC_FIELD', 4: 'GC_LOCAL', 5: 'GC_MONITOR',
                    6: 'GC_JAVA_FRAME', 7: 'GC_NATIVE_FRAME', 8: 'UNREACHABLE',
                    9: 'DAEMON_WORKER', 10: 'UNKNOWN',
                }
                
                gc_roots.append({
                    'kind': kind_names.get(root_kind, f'0x{root_kind:04X}'),
                    'root_kind_raw': root_kind,
                    'root_info': root_info,
                    'object_id': object_id,
                    'class_serial': class_serial,
                })
                p += 20
            else:
                p += 4  # 跳过无效条目而不是中断
    
    return gc_roots
```

### 第八点五步：解析 THREAD_SUSPEND Chunks

提取线程挂起快照——线程对象 ID 和帧 ID 列表。这些对于构建 GC Root → Thread → Stack Frame 引用链至关重要。

**关键**：线程名**不**存储在 THREAD_SUSPEND chunk 中。它们通过 STRING_DUMP 表使用 string_id 查找来解析。THREAD_SUSPEND chunk 包含线程对象 ID 的密集 marker 表。

```python
def parse_thread_suspended(chunks, filepath):
    """解析 THREAD_SUSPEND chunks 提取线程对象 ID。
    
    hprof-libs THREAD_SUSPEND 使用带可变 suspend_type 的 marker-based 表：
      [thread_obj_id(4B LE)] [0x0A] [0x7F] [suspend_type(1B)] [counter(1B)] [pad(2B: 0x00 0x40)]
    
    每条记录恰好 9 字节：
      - thread_obj_id(4B LE) — 用于从 SAMPLE_GC_HEAP.root_info 链接（当 root_kind=JAVA_STACK 时）
      - marker(2B: 0x0A 0x7F)
      - suspend_type(1B) — 可变字节（0x0A, 0x08, 0x06, 0x13 等）
      - counter(1B) — 顺序线程索引
      - pad(2B: 0x00 0x40)
    
    线程名必须通过 STRING_DUMP string_table 使用 string_id 解析。
    帧 ID 通过 STACK_FRAME chunks 解析。
    
    返回：dict mapping thread_obj_id -> {
        'name': str,          # 通过 STRING_DUMP 解析
        'suspend_type': int,
        'frame_ids': [int, ...],  # 通过 STACK_FRAME chunks 解析
    }
    """
    threads = {}  # thread_obj_id -> thread info
    
    for pos, tag, length in chunks:
        if tag not in (0x0003, 0x03):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # 解析 marker 表：每条记录固定 9 字节
        # 模式：obj_id(4) + 0x0A + 0x7F + suspend_type(1) + counter(1) + pad(2)
        p = 0
        while p + 9 <= len(payload):
            thread_obj_id = struct.unpack_from('<I', payload, p)[0]
            b4 = payload[p+4]
            b5 = payload[p+5]
            b6 = payload[p+6]
            counter = payload[p+7]
            pad = struct.unpack_from('<H', payload, p+8)[0]
            
            # 验证 marker 模式：0A 7F 后跟任意 suspend_type 字节
            # 以及 pad 字节 0x00 0x40
            if b4 == 0x0A and b5 == 0x7F and pad == 0x0040:
                if thread_obj_id > 0:
                    threads[thread_obj_id] = {
                        'name': '',  # 将通过 STRING_DUMP 解析
                        'suspend_type': b6,
                        'frame_ids': [],
                        'class_serial': counter,  # 在 marker 中用作 class_serial
                    }
                p += 9
            else:
                # 不是有效 marker — 向前扫描
                p += 1
        
        # marker 表之后，搜索线程元数据
        # 线程名可能以 string_id 引用形式存储在尾部
        # 查找类似模式：string_id(4B) + thread_name_string(0x01 终止符)
        # 这需要基于实际 chunk 布局的额外解析

    return threads
```

### 第八点六步：解析 STACK_FRAME Chunks

提取栈帧详情——每个 frame ID 对应的类/方法名和行号。用于解析 SAMPLE_GC_HEAP 条目中的 `root_info` 引用。

**逆向得到的布局**：STACK_FRAME 使用 `00 40 00 XX` marker-based 表。每个 marker-based 条目只有 **5 字节**：
```
frame_id(4B LE) + type_code(1B)
```
marker 的 `XX` 字节是下一个 class_serial（顺序计数器）。

该 chunk 还可能包含第一个 `00 40 00` 之前的**预 marker 块**，其格式不同，可能包含：
```
frame_id(4B LE) + class_serial(4B LE) + pad(4B LE) + method_index(4B LE) + line_number(4B LE)
```

返回：dict mapping frame_id -> {
    'class_serial': int,
    'class_name': str,       # 通过 string_table 解析
    'method_index': int,
    'method_name': str,      # 通过 string_table 解析
    'line_number': int,
    'type_code': int
}

```python
def parse_stack_frames(chunks, filepath, string_table):
    """解析 STACK_FRAME chunks 提取帧详情。
    
    hprof-libs STACK_FRAME 使用带 00 40 00 XX 分隔符的 marker-based 表：
      [entry_data(variable)] [00 40 00 class_serial(1B)]
    
    Marker-based 条目结构（通常 5 字节）：
      - frame_id(4B LE)
      - type_code(1B)
    
    预 marker 块（第一个 00 40 00 之前）可能包含带有完整元数据的初始帧：
      frame_id(4B) + class_serial(4B) + pad(4B) + method_index(4B) + line_number(4B)
    
    返回：dict mapping frame_id -> {
        'class_serial': int,
        'class_name': str,       # 通过 string_table 解析
        'method_index': int,
        'method_name': str,      # 通过 string_table 解析
        'line_number': int,
        'type_code': int
    }
    """
    frames = {}  # frame_id -> frame info
    
    for pos, tag, length in chunks:
        if tag not in (0x0002, 0x02):
            continue
        
        with open(filepath, 'rb') as f:
            f.seek(pos + 4)
            payload = f.read(length - 4)
        
        # 首先，解析预 marker 数据（第一个 00 40 00 之前）
        first_marker = payload.find(b'\x00\x40\x00')
        if first_marker == -1:
            continue
        
        # 预 marker 块可能包含初始帧数据
        pre_data = payload[:first_marker]
        p = 0
        while p + 20 <= len(pre_data):
            frame_id = struct.unpack_from('<I', pre_data, p)[0]
            class_serial = struct.unpack_from('<I', pre_data, p+4)[0]
            pad = struct.unpack_from('<I', pre_data, p+8)[0]
            method_index = struct.unpack_from('<I', pre_data, p+12)[0]
            line_number = struct.unpack_from('<I', pre_data, p+16)[0] if p + 20 <= len(pre_data) else -1
            
            if frame_id > 0:
                class_name = string_table.get(class_serial, f'class_serial_{class_serial}')
                method_name = _resolve_method_index(method_index, string_table)
                
                frames[frame_id] = {
                    'class_serial': class_serial,
                    'class_name': class_name,
                    'method_index': method_index,
                    'method_name': method_name,
                    'line_number': line_number,
                }
            p += 20
        
        # 然后解析 marker-based 条目
        p = first_marker + 4
        while p < len(payload) - 4:
            next_marker = payload.find(b'\x00\x40\x00', p)
            if next_marker == -1 or next_marker + 4 >= len(payload):
                break
            
            entry_data = payload[p:next_marker]
            class_serial = payload[next_marker + 3]
            
            if len(entry_data) >= 5:
                frame_id = struct.unpack_from('<I', entry_data, 0)[0]
                type_code = entry_data[4]
                
                if frame_id > 0 and frame_id in frames:
                    frames[frame_id]['type_code'] = type_code
                    frames[frame_id]['class_serial'] = class_serial
            
            p = next_marker + 4
    
    return frames


def _resolve_method_index(method_index, string_table):
    """使用字符串表将方法索引解析为可读名称。
    
    在 hprof-libs 中，method_index 映射到 STRING_DUMP chunks 中的 string_id。
    string_table 将 string_id 映射到 text。
    """
    if method_index == 0:
        return '<unknown>'
    
    # method_index 通常是 string_id；直接查找
    if method_index in string_table:
        return string_table[method_index]
    
    # 尝试作为 serial 号码查找（某些实现使用 serial lookup）
    if method_index < 256:
        return f'method_index_{method_index}'
    
    return f'method_index_{method_index}'
```

### 第八点七步：解码 root_info 并构建引用链

将 SAMPLE_GC_HEAP 条目与 THREAD_SUSPEND 和 STACK_FRAME 数据关联，生成人类可读的引用链。

```python
def decode_root_info(gc_root, thread_map, frame_map):
    """根据 root_kind 解释 root_info 字段并生成人类可读的上下文。
    
    root_kind -> root_info 解释：
      0 (JAVA_STACK):  root_info = 线程对象 ID → 在 thread_map 中查找
      1 (NATIVE_STACK): root_info = native 栈指针 → 保留原始值
      2 (SYSTEM_CLASS): root_info = 通常为 0 → 保留原样
      3 (GC_STATIC_FIELD): root_info = 静态字段引用 → 需要 class + field
      4 (GC_LOCAL): root_info = 局部变量引用 → 在 frame_map 中查找
      5 (GC_MONITOR): root_info = monitor ID → 保留原始值
      6 (GC_JAVA_FRAME): root_info = Java 帧信息 → 在 frame_map 中查找
      7+ (其他): 保留原始值
    """
    kind = gc_root['root_kind_raw']
    root_info = gc_root['root_info']
    decoded = {'raw': root_info, 'context': None}
    
    if kind == 0:  # JAVA_STACK
        if root_info in thread_map:
            t = thread_map[root_info]
            decoded['context'] = {
                'type': 'thread',
                'name': t['name'],
                'obj_id': root_info,
                'frame_ids': t['frame_ids'],
            }
    elif kind == 4:  # GC_LOCAL
        # root_info 可能是 frame_id
        if root_info in frame_map:
            decoded['context'] = {
                'type': 'local_var',
                'frame': frame_map[root_info],
            }
    elif kind == 6:  # GC_JAVA_FRAME
        if root_info in frame_map:
            decoded['context'] = {
                'type': 'java_frame',
                'frame': frame_map[root_info],
            }
    
    return decoded


def build_reference_chain(gc_root, thread_map, frame_map, class_map, string_table):
    """构建单条 GC Root → Thread/Local → Stack Frame → Target Object 引用链。
    
    返回适合报告生成的格式化 chain dict。
    """
    kind = gc_root['kind']
    decoded = decode_root_info(gc_root, thread_map, frame_map)
    
    # 查找目标对象类
    obj_id = gc_root['object_id']
    target_class = class_map.get(obj_id, 'unknown')
    target_name = target_class.get('class_name', 'unknown')
    target_instances = target_class.get('num_instances', '?')
    
    chain = {
        'root_kind': kind,
        'target_class': target_name,
        'target_instance_count': target_instances,
        'stack_trace': [],
        'decoded_context': decoded,
    }
    
    if decoded['context'] and decoded['context']['type'] == 'thread':
        thread = decoded['context']
        chain['thread_name'] = thread['name']
        # 通过 frame_map 遍历 frame_ids 构建栈跟踪
        for fid in thread.get('frame_ids', []):
            if fid in frame_map:
                f = frame_map[fid]
                chain['stack_trace'].append(
                    f"{f['class_name']}.{f['method_name']}({f['line_number']})"
                )
            else:
                chain['stack_trace'].append(f'<frame_id={fid}>')
    elif decoded['context']:
        ctx = decoded['context']
        if ctx.get('type') == 'local_var' and ctx.get('frame'):
            f = ctx['frame']
            chain['stack_trace'].append(
                f"{f['class_name']}.{f['method_name']} (local variable)"
            )
        elif ctx.get('type') == 'java_frame' and ctx.get('frame'):
            f = ctx['frame']
            chain['stack_trace'].append(
                f"{f['class_name']}.{f['method_name']} (Java frame)"
            )
    
    return chain


def build_reference_chains(gc_roots, thread_map, frame_map, class_map, string_table):
    """为所有 roots 构建 GC Root → Thread → Stack Frame → Leaked Object 引用链。
    
    按 (target_class, root_kind) 分组以合并相似的泄漏。
    
    返回按 target_instance_count 降序排列的 chain dicts 列表。
    """
    # 按 (target_class_name, root_kind) 分组
    groups = {}
    unknown_count = 0
    
    for root in gc_roots:
        chain = build_reference_chain(root, thread_map, frame_map, class_map, string_table)
        key = (chain['target_class'], chain['root_kind'])
        
        if key not in groups:
            groups[key] = {
                'chains': [],
                'total_roots': 0,
            }
        
        groups[key]['chains'].append(chain)
        groups[key]['total_roots'] += 1
        
        if not chain['stack_trace']:
            unknown_count += 1
    
    result = []
    for (target_class, root_kind), group in groups.items():
        # 选择栈帧最多的 chain 作为代表
        best_chain = max(group['chains'], key=lambda c: len(c['stack_trace']))
        
        # 根据实例数确定优先级
        try:
            inst_count = int(best_chain['target_instance_count']) if best_chain['target_instance_count'] != '?' else 0
        except ValueError:
            inst_count = 0
        
        if inst_count > 50000:
            priority = 'P0'
        elif inst_count > 20000:
            priority = 'P1'
        elif inst_count > 5000:
            priority = 'P2'
        else:
            priority = 'P3'
        
        result.append({
            'priority': priority,
            'root_kind': root_kind,
            'target_class': target_class,
            'target_instance_count': best_chain['target_instance_count'],
            'total_roots': group['total_roots'],
            'stack_trace': best_chain['stack_trace'],
            'thread_name': best_chain.get('thread_name', ''),
            'decoded_context': best_chain['decoded_context'],
        })
    
    # 按实例数降序排列
    result.sort(key=lambda x: (int(x['target_instance_count']) if isinstance(x['target_instance_count'], int) else 0), reverse=True)
    
    return result
```

### 第九步：生成二进制路径综合报告

参考 [references/report-template.md](references/report-template.md) 中的完整 Markdown 报告模板。用它作为分析输出的结构。

**报告必须包含**：

1. **概要** — 整体健康状况摘要，包含关键指标表
2. **堆分布** — Top 20 对象数量和浅大小，类实例分布直方图
3. **GC Root 分析** — Root 类型分布，可疑引用链及栈跟踪
4. **内存泄漏检测** — 高实例数类，模式匹配（Activity 泄漏、监听器未注销、WebView 泄漏、Handler 泄漏、静态集合膨胀）
5. **线程快照** — 活跃线程和栈快照
6. **风险评级** — P0-P3 严重性评估

**在每个可疑类中，必须标注**：
- 如果类名无法解析（显示为 `serial_X`），明确说明原因
- 如果实例数异常高，给出风险评估

**在所有 chunks 解析完成后，调用以下 pipeline 构建 GC Root 引用链**：

```python
# Pipeline: 解析 THREAD_SUSPEND + STACK_FRAME，然后构建引用链
thread_map = parse_thread_suspended(chunks, filepath)
frame_map = parse_stack_frames(chunks, filepath, string_table)

# 从 gc_roots + thread_map + frame_map 构建引用链
chains = build_reference_chains(
    gc_roots=gc_roots,
    thread_map=thread_map,
    frame_map=frame_map,
    class_map=classes_with_object_ids,  # {object_id: {'class_name': ..., 'num_instances': ...}}
    string_table=string_table,
)

# 按 root_kind 统计 roots 用于根类型分布表
from collections import Counter
root_kind_counts = Counter(root['kind'] for root in gc_roots)
unknown_count = sum(1 for root in gc_roots if root['kind'] == 'UNKNOWN')
total_roots = len(gc_roots)
resolved_ratio = 1 - unknown_count / total_roots if total_roots > 0 else 0

# 同时解码所有 root_info 字段以改进分类
decoded_roots = []
for root in gc_roots:
    decoded = decode_root_info(root, thread_map, frame_map)
    decoded_roots.append(decoded)
```

**标注类名/方法名时**：
- Kotlin synthetic 字段（`$this$coroutineScope`、`$onCreate$1`）：添加 `← Kotlin synthetic` 注释
- ProGuard 混淆名称（`a3()`、`v3()`）：添加 `← obfuscated method (ProGuard)` — 不要猜测原始名称
- DroidPlugin 内部（`msdocker.*`、`Ill111l`）：添加 `← DroidPlugin hook` 注释

### 第十步：与 hprof-conv 交叉验证（可选）

为了验证，使用 Android SDK 的 `hprof-conv` 转换文件并比较关键统计数据：

```bash
# 转换 hprof-libs 为标准格式
hprof-conv -z <input.hprof> <output_converted.hprof>

# 使用标准 hprof-heap 解析器解析转换后的文件
# 比较类数量和对象数量
```

**重要**：`hprof-conv` 在转换 hprof-libs 格式时会丢失大量数据。仅将其用于交叉验证它能解析的部分。直接解析方法（步骤 3-9）从 Android hprof-libs 文件中提取的数据远多于标准工具。

---

## 执行约束

- **切勿**在用户工作区目录中创建临时文件。使用内联 Python 或将临时文件写入 `/tmp/`。
- 所有中间文件（解析的 JSON、分析结果）应放入 `/tmp/hprof_analysis/`。
- 分析完成后，工作区应保持干净。
- hprof 文件可能很大（100MB+）。使用 seek 访问高效解析，不要全文件读取。
- 如果文件太大无法单次处理，请增量处理 chunks。

## 输出约定

**必须输出两份独立报告**，分别对应两条分析路径：

### 路径 A 输出（Parquet/DuckDB）

将最终分析报告保存到 `hprof_analysis/<hprof文件名_无扩展名>_parquet_report.md`。

示例：
- 输入：`dump.hprof` → 输出：`hprof_analysis/dump_parquet_report.md`
- 输入：`taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` → 输出：`hprof_analysis/taqu_android_client_logfile_401_1783731893047_1_1_342013740_parquet_report.md`

### 路径 B 输出（二进制直接解析）

将最终分析报告保存到 `hprof_analysis/<hprof文件名_无扩展名>_binary_report.md`。

示例：
- 输入：`dump.hprof` → 输出：`hprof_analysis/dump_binary_report.md`
- 输入：`taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof` → 输出：`hprof_analysis/taqu_android_client_logfile_401_1783731893047_1_1_342013740_binary_report.md`

如果 `hprof_analysis/` 目录不存在，请创建它。

> **关键**：两份报告必须独立生成、独立评估质量。不得将 Parquet 路径的数据混入二进制路径报告中，也不得将二进制路径的有限数据混入 Parquet 路径报告中。

## 质量评估标准

两条路径必须**分开打分**，参见 [references/quality-standards.md](references/quality-standards.md)。

在最终输出中，每份报告末尾必须包含该路径的质量评分表，格式如下：

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
- **路径 A 的报告不应引用路径 B 的数据**，反之亦然。两条路径是独立的分析视图。
