# Android hprof-libs 格式规范

本文档描述了现代 Android 设备使用的 Android hprof-libs 格式（Android 7.0+）。

> **来源**：从 `taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof`（159 MB，Android 7.0+ hprof-libs）逆向工程得出。
> 已针对 HeapDumpStarDiver + DuckDB 输出和 Eclipse MAT 参考中的已知良好数据进行验证。

## 文件结构

```
[0-15]:     Magic "JAVA PROFILE 1.0" (12 bytes) + stated_header_size(4B LE)，位于偏移 16
[16-19]:    Stated header size (uint32 LE)，hprof-libs 通常为 13102+
[20-N]:     扩展头部数据（类元数据、字符串表、字段定义）
[0x80+]:    Chunk 流从固定偏移 0x80 开始（不是 16 + stated_header_size）
```

**格式检测**：如果 `stated_header_size > 2000`，使用 hprof-libs 格式；否则使用标准 hprof-heap。

## Chunk 格式

记录流中的每个 chunk：

```
tag:      uint16 LE（chunk 类型）
length:   uint16 LE（包含 4 字节头部的 chunk 总大小）
payload:  length - 4 bytes 的 chunk 数据
```

## Chunk 类型

| Tag | 名称 | 描述 |
|-----|------|------|
| 0x0000 | ZERO | 填充/零填充数据 |
| 0x0001 | CLASS_DUMP | 类元数据（serial、实例数、静态字段） |
| 0x0002 | STACK_FRAME | 栈帧信息 |
| 0x0003 | THREAD_SUSPEND | 线程挂起快照 |
| 0x0004 | OBJECT_DUMP | 对象实例数据 |
| 0x0005 | SAMPLE_GC_HEAP | GC 堆采样（roots 和可达对象） |
| 0x0010 | STRING_DUMP | 字符串表条目 |
| 0x0011 | LOAD_DATA | 类/实例字段数据 |
| 0x0013 | DUMP_COMPLETED | Dump 完成标记 |
| 0x0014 | SESSION_START | 会话开始元数据 |
| 0x0015 | SESSION_FINISH | 会话结束元数据 |
| 0x0016 | BUFFER_START | 缓冲区开始标记 |
| 0x0017 | BUFFER_END | 缓冲区结束标记 |
| 0x0019 | CHAIN_INSTANCE | 链式实例（字段元数据） |
| 0x0030 | DYNAMIC_SYSTEM_LIBRARY | 动态系统库信息 |
| 0x0031 | STATIC_SYSTEM_LIBRARY | 静态系统库信息 |
| 0x0032 | ROM_PING | ROM 分区信息 |

## Chunk Payload 格式

### CLASS_DUMP (0x0001)

观察到两种布局：

**大 CLASS_DUMP**（如 @0x56bfd1，len=51989）：紧凑排列的条目，前几个字节中看不到 `00 40 00` 标记。可能是类元数据记录的原始数组。

**小 CLASS_DUMP**（如 @0x8b9381，len=64）：使用 `00 40 00 XX` 分隔符的 marker-based 表。每条条目包含类元数据，后跟一个 marker，其 `XX` 字节是下一个 class_serial。

```
小 CLASS_DUMP 布局（marker-based 表）：
  [entry_data(variable)] [00 40 00 XX]

  entry_data 包含：
    type_code(1B) — 0x02=有实例, 0x0A=无实例, 0x0B=静态/其他
    instance_count(1B) — 0xFF 表示未知/特殊
    field_info(variable)

  marker 的 XX 字节是下一个 class_serial（顺序计数器）。
  每个 chunk 的第一个条目前有 7 字节头部。
```

### STRING_DUMP (0x0010)

```
hprof-libs 格式（每条变长）：
  string_text:    变长字节（可打印 ASCII，以 0x01 终止）
  separator:      0x01
  zeros:          7 字节 (0x00)
  value:          1 字节（元数据）
  ref:            4 字节 LE（string_id 或指针）
  总条目长度：len(string_text) + 13 字节
```

**重要**：chunk 的第一个条目可能有一个垃圾首字节（来自前一个 chunk 的 ref 残留）。在接受之前，请验证文本是否为可打印 ASCII。

### OBJECT_DUMP (0x0004)

```
hprof-libs 格式（marker-based 表，与小 CLASS_DUMP 布局相同）：
  [entry_data(variable)] [00 40 00 class_serial(1B)]

  entry_data 包含：
    object_id(4B LE)
    type_code(1B) — e.g., 0x0B, 0x02, 0xFF
    field_data(variable, 通常是 padding 或小值)

  每个 chunk 多条条目，由 00 40 00 XX markers 分隔。
  XX 字节是 class_serial（与 CLASS_DUMP 编号相同）。
  条目大小可变：通常 5-9 字节。
```

**逆向工程示例**（@0x5f7d01，len=16384）：
- 在位置 [7, 20, 33, 46, 59, ...] 找到 markers，间距约 13 字节
- 第一条：`00 F3 02 14 C4 DA 00 00 40` → object_id=0xDA C4 14 02, type=0xF3, marker class_serial=0xF4
- 第二条：`00 F4 0B 00 00 01 9F 4E 97 2B 58 00 40` → object_id=0x2B 58 4E 9F, type=0xF4, marker class_serial=0xF5

### SAMPLE_GC_HEAP (0x0005)

```
hprof-libs 格式（每条 20 字节）：
  object_id:      uint32 LE（可达对象）
  root_info:      uint32 LE（上下文相关）
  root_kind:      uint16 LE（0-10）
  class_serial:   uint32 LE（通常是常量系统类）
  pad:            uint32 LE
  extra:          uint16 LE
```

**Root kinds：**
- 0 = JAVA_STACK
- 1 = NATIVE_STACK
- 2 = SYSTEM_CLASS
- 3 = GC_STATIC_FIELD
- 4 = GC_LOCAL
- 5 = GC_MONITOR
- 6 = GC_JAVA_FRAME
- 7 = GC_NATIVE_FRAME
- 8 = UNREACHABLE
- 9 = DAEMON_WORKER
- 10 = UNKNOWN

**观察到两种 chunk 尺寸：**
- 小 chunk（~63B）：包含 2-3 条条目加上 prefix/padding。第一条条目前可能有非标准的 4 字节 prefix（`3F 21` 或 `3F 00`）。
- 大 chunk（最大 34KB）：紧密排列约 1700 条条目。

**已验证解析**（@0x665310，len=63）：
```
Prefix: 3F 21 14 9E
Entry 0: obj_id=0x00000024, root_info=0x096F0000, root_kind=30918, class_serial=0x14000000
Entry 1: obj_id=0x096F1024, root_info=0x000078C6, root_kind=0 (JAVA_STACK), class_serial=0x249E1423
```

注意：解释之前必须验证 root_kind 值（<=10）。某些 chunk 全是 padding（重复的 `3F 00`）。

### THREAD_SUSPEND (0x0003)

```
hprof-libs 格式（带 0A 7F 13 markers 的 marker-based 表）：
  [entry_data(variable)] [0A 7F 13 counter(1) pad(2: 00 40)]

  entry_data 包含：
    thread_obj_id(4B LE) — 用于从 SAMPLE_GC_HEAP.root_info 链接的对象 ID（当 root_kind=JAVA_STACK 时）
    suspend_type(1B) — 0=suspend, 1=yield, 等。

  marker 序列为 0A 7F 13。counter 字节是顺序线程索引。
  pad 字节始终为 0x00 0x40。
  条目大小固定为 9 字节：obj_id(4) + 0x0A + 0x7F + 0x13 + counter(1) + pad(2)。
```

**在 `taqu_android_client_logfile_*.hprof` 上验证的逆向布局**：
- chunk payload 以密集的 9 字节条目表开始，由 `0A 7F 13` markers 分隔
- marker 表之后，跟随线程元数据（变长，可能包含作为 string_id 引用形式的线程名）
- 单个 16KB chunk 可包含约 1820 个线程条目（16384/9）
- **线程名不内联存储在 THREAD_SUSPEND 中**。它们通过 STRING_DUMP 表使用 string_id 查找来解析。
- 线程引用的 Frame IDs 通过 STACK_FRAME chunks 解析。

**返回**：dict mapping thread_obj_id -> {
    'name': str,          # 通过 STRING_DUMP string_table 解析
    'suspend_type': int,
    'frame_ids': [int, ...],  # 通过 STACK_FRAME chunks 解析
}

### STACK_FRAME (0x0002)

```
hprof-libs 格式（使用 00 40 00 XX 分隔符的 marker-based 表）：
  [entry_data(variable)] [00 40 00 class_serial(1B)]

  entry_data 包含：
    frame_id(4B LE) — 唯一帧标识符
    type_code(1B) — 0x02=有实例, 0x0A=无实例, 等。

  marker 的 XX 字节是下一个 class_serial。
  Marker-based 条目通常每条 5 字节。
```

**Pre-marker block**：第一个 `00 40 00` marker 之前的数据可能以不同格式包含初始帧记录：
```
frame_id(4B LE) + class_serial(4B LE) + pad(4B LE) + method_index(4B LE) + line_number(4B LE)
```

**返回**：dict mapping frame_id -> {
    'class_serial': int,
    'class_name': str,       # 通过 string_table 解析
    'method_index': int,
    'method_name': str,      # 通过 string_table 解析
    'line_number': int,
    'type_code': int
}

### LOAD_DATA (0x0011)

```
class_serial:   uint32 LE
object_id:      uint32 LE
field_data:     variable
```

字段数据格式取决于上下文：
- 对于类定义：`field_name + 0x01 + 7_zero_bytes + string_length(1B) + pointer(4B)`
- 对于实例数据：`field_offset(2B) + type_code(1B) + value(variable)`

**已验证示例**（@0x5eb2a3b，len=104）：
```
class_serial=0x6FC60900, object_id=0x00000014
field_data: 3F 00 00 00 00 1F 11 00 78 6F 09 C6 78 ...
```

### CHAIN_INSTANCE (0x0019)

```
包含 Kotlin synthetic 字段元数据和类链信息。
条目：field_name + 0x01 + 8_bytes_metadata
示例字符串："$this$coroutineScope", "$this$launchWhenResumed", "$this$liveData"
```

## 解析策略

1. 从文件头部读取 magic 和 stated header size
2. 如果 stated_header_size > 2000，使用 hprof-libs 格式（chunks 在 0x80）
3. 否则，使用标准 hprof-heap 格式（records 在 16 + stated_header_size）
4. 扫描 chunk 流以查找有效 tags，跳过未知/填充 chunks
5. 根据各 chunk 类型的 payload 格式解析每个 chunk
6. 使用 serial numbers 和 object IDs 跨 chunk 类型关联数据

## 与标准 hprof-heap 的差异

| 方面 | hprof-heap（标准） | hprof-libs（Android 7+） |
|------|-------------------|--------------------------|
| 记录头部 | 8 字节（tag 4B + length 4B） | 4 字节（tag 2B + length 2B） |
| 记录起始 | 16 + stated_header_size | 0x80（固定） |
| 字节序 | 小端 | 小端 |
| Header 大小 | 通常 128 | 通常 13102+ |
| 压缩 | 无 | 可能包含压缩段 |
