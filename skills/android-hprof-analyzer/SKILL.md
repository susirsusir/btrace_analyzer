---
name: heap-analysis
description: 使用 HeapDumpStarDiver（HPROF 转 Parquet）和 DuckDB 分析 JVM 堆转储。自动化内存浪费检测、内存分析和问题分类。

---

# 使用 HeapDumpStarDiver 进行堆转储分析

所有堆转储分析请使用 **heapdump MCP 工具**，不要使用基于 bash 的工作流。

HPROF 到 Parquet 的转换由 MCP 服务器内部调用 `HeapDumpStarDiver` 二进制文件完成，**禁止**通过 bash 直接运行该二进制文件。

## 前置步骤：获取并准备 HPROF 文件

在开始分析之前，必须先完成以下步骤：

### 1. 接收用户输入

向用户询问待分析的 hprof 目标，支持以下两种输入格式：

- **完整 URL**：例如 `https://cfile.jiaoliuqu.com/taqu_android_client_logfile_401_1775740407695_1_1_146915823.hprof`
- **文件名（不含扩展名）**：例如 `taqu_android_client_logfile_401_1775736479981_1_1_995469047`

解析规则：
- 如果用户输入的是完整 URL（以 `http://` 或 `https://` 开头），则直接将该 URL 作为下载地址，并从 URL 中提取文件名（去掉路径前缀和 `.hprof` 扩展名）作为 `<hprof_name>`。例如从 `https://cfile.jiaoliuqu.com/taqu_android_client_logfile_401_1775740407695_1_1_146915823.hprof` 中提取 `taqu_android_client_logfile_401_1775740407695_1_1_146915823`。
- 如果用户输入的是文件名（不含扩展名），则直接作为 `<hprof_name>`，下载 URL 需要在第 3 步中拼接。

后续步骤中将提取/输入的文件名称为 `<hprof_name>`。

### 2. 检查标准格式 HPROF 文件是否已存在

检查以下路径是否已存在标准格式的 hprof 文件：

```
<项目根目录>/.hprof-analy-result/standard_hprof/<hprof_name>.standard.hprof
```

- **如果已存在** → 跳到「分析工作流程」第 1 步
- **如果不存在** → 继续第 3 步

### 3. 下载原始 HPROF 文件

根据第 1 步的用户输入确定下载 URL：

- **如果用户输入的是完整 URL**：直接使用该 URL 作为下载地址
- **如果用户输入的是文件名**：拼接下载 URL 为 `https://cfile.jiaoliuqu.com/<hprof_name>.hprof`

保存路径：`<项目根目录>/.hprof-analy-result/raw_hprof/<hprof_name>.hprof`

确保目录存在后再下载：

```bash
mkdir -p <项目根目录>/.hprof-analy-result/raw_hprof
# 如果用户提供了完整 URL，直接使用；否则拼接 URL
curl -o <项目根目录>/.hprof-analy-result/raw_hprof/<hprof_name>.hprof "<下载URL>"
```

### 4. 使用 hprof-conv 转换为标准格式

使用 Android SDK 的 `hprof-conv` 工具将 Android 格式的 hprof 转换为标准 JVM 格式。

查找 `hprof-conv` 的优先级：

1. 先检查 `$HOME/Library/Android/sdk/platform-tools/hprof-conv` 是否存在
2. 如果不存在，检查环境变量 `$ANDROID_HOME/platform-tools/hprof-conv`
3. 如果仍不存在，使用 `find` 搜索常见路径：`find $HOME/Library/Android $HOME/Android /opt/android-sdk /usr/local/android-sdk -name "hprof-conv" -type f 2>/dev/null | head -1`
4. 如果以上都找不到，提示用户手动指定 `hprof-conv` 路径

找到 `hprof-conv` 后执行转换：

```bash
mkdir -p <项目根目录>/.hprof-analy-result/standard_hprof
<hprof-conv路径> \
  <项目根目录>/.hprof-analy-result/raw_hprof/<hprof_name>.hprof \
  <项目根目录>/.hprof-analy-result/standard_hprof/<hprof_name>.standard.hprof
```

转换完成后进入「分析工作流程」。

## 分析工作流程

以下所有步骤均通过 **heapdump MCP 工具**完成，不要通过 bash 直接运行任何二进制文件。

1. **检查是否已转换：** 在执行转换之前，先检查当前项目根目录下的 `.hprof-analy-result/standard_hprof/<session_id>/parquet/` 目录是否已存在且包含 `.parquet` 文件。如果已存在，说明之前已经转换过，直接使用 MCP 工具 `open_session(parquet_dir="<项目根目录>/.hprof-analy-result/standard_hprof/<session_id>/parquet")` 打开已有会话，跳到第 3 步。如果不存在，执行第 2 步进行转换。
2. **转换（通过 MCP）：** 调用 MCP 工具 `convert_heap_dump(hprof_path="<项目根目录>/.hprof-analy-result/standard_hprof/<hprof_name>.standard.hprof", session_id="<hprof_name>")` — MCP 服务器内部会调用 `HeapDumpStarDiver` 将标准格式 HPROF 文件解析为 Parquet 格式并打开一个命名会话。**注意：** 转换完成后，需要将生成的 Parquet 文件移动到当前项目根目录下的 `.hprof-analy-result/standard_hprof/<session_id>/parquet/` 目录中（而非 hprof 文件所在目录），然后使用 MCP 工具 `open_session` 重新指向新路径。
3. **分析（通过 MCP）：** 调用 MCP 工具 `analyze_heap()` — 运行自动化浪费检测（重复字符串、低效集合、装箱基本类型等）
4. **查询（通过 MCP）：** 调用 MCP 工具 `query_heap(sql="SELECT ...")` — 使用 `read_parquet('pattern')` 对 Parquet 文件执行 DuckDB SQL 即席查询
5. **清理（通过 MCP）：** 调用 MCP 工具 `close_session(id)` 保留文件，或 `cleanup_session(id, confirm=True)` 删除文件

## 分析报告输出

分析完成后，必须将分析报告保存为 Markdown 文件：

- 保存路径：`<项目根目录>/.hprof-analy-result/<日期>-<hprof_name>.md`
- 日期格式：`YYYY-MM-DD-HH-mm`，例如 `2025-04-09-20-17`
- 完整示例：`<项目根目录>/.hprof-analy-result/2025-04-09-20-17-taqu_android_client_logfile_401_1775736479981_1_1_995469047.md`

### 报告格式要求

报告文件开头必须包含以下 front-matter 信息：

```markdown
---
name: <根据报告核心内容总结的简短名称>
description: <根据报告核心内容总结的简短描述>
---
```

其中 `name` 和 `description` 需要根据当前报告的核心分析结论进行总结提炼，要求言简意赅。

示例：

```markdown
---
name: 重复字符串导致内存膨胀
description: 堆中存在大量重复 String 对象，占用约 120MB，建议使用字符串池化优化
---

# 堆转储分析报告
...（报告正文）
```

### GC Root 引用链分析

在报告中必须包含以下章节，使用新增的 GC Root JOIN SQL 查询填充：

```markdown
## GC Root 引用链分析

### Root 类型分布（含类名关联）

| Root 类型 | 目标类 | 数量 |
|-----------|--------|------|
| JAVA_STACK | com.xmhaibao.gift.bean.LiveGiftInfo | 7,829 |
| SYSTEM_CLASS | java.lang.Object | 9,998 |
| ... | ... | ... |

### 可疑引用链

#### [P0] LiveGiftInfo 被主线程持有

- **持有对象**: `com.xmhaibao.gift.bean.LiveGiftInfo`
- **实例数**: 43,260
- **Root 路径**:
  ```
  GC Root: JAVA_STACK (main)
    → android.app.ActivityThread.handleStopActivity
    → com.example.LiveGiftManager.mCachedGifts
  ```
- **建议**: 将缓存改为 WeakReference
```

**注意**：JOIN 条件取决于 HeapDumpStarDiver 实际生成的 Parquet schema。需要根据实际列名调整 `thread_id`、`frame_ids` 等字段名。如果 JOIN 返回空结果，说明 Parquet 路径的 schema 不包含线程栈信息，此时应回退到二进制路径的结果。

## 会话管理

- 会话默认以 HPROF 文件名命名（例如 `heap-dump-2024.hprof` 对应会话名 `heap-dump-2024`）
- 如果只有一个会话处于打开状态，所有查询工具可以省略 `session_id` 参数
- 使用 MCP 工具 `list_sessions()` 查看所有活跃会话
- 可以同时打开多个会话，用于对比不同的堆转储
- `query_heap` 支持通过 `limit` 和 `offset` 参数进行分页查询
- 要恢复之前的分析，使用 MCP 工具 `open_session(parquet_dir="<项目根目录>/.hprof-analy-result/standard_hprof/<session_id>/parquet")` 而不是重新转换
- Parquet 文件统一存放在当前项目根目录的 `.hprof-analy-result/standard_hprof/<session_id>/parquet/` 目录下，按 session_id 分子目录组织

## MCP 服务器配置与前置条件

HPROF 到 Parquet 的转换由 `heapdump-stardiver` MCP 服务器完成。MCP 配置文件位于 `.kiro/settings/mcp.json`，其中通过环境变量 `HEAP_DUMP_STAR_DIVER_BINARY_OVERRIDE` 指定了 `HeapDumpStarDiver` 二进制文件的路径。

### 二进制文件

`HeapDumpStarDiver` 二进制文件已预置在本技能的 `references` 目录下：

```
<项目根目录>/skills/android-hprof-analyzer/references/HeapDumpStarDiver
```

MCP 服务器会通过环境变量自动找到并使用该二进制文件，**不需要也不应该**通过 bash 直接运行它。

如果 MCP 工具 `convert_heap_dump` 报告二进制文件权限不足，可通过 bash 赋予执行权限：

```bash
chmod +x <项目根目录>/skills/android-hprof-analyzer/references/HeapDumpStarDiver
```

> **注意：** 不要执行 `cargo build --release`，也不要将二进制文件复制到其他位置。MCP 服务器通过 `HEAP_DUMP_STAR_DIVER_BINARY_OVERRIDE` 环境变量直接引用 `skills/android-hprof-analyzer/references/HeapDumpStarDiver`。

### MCP 服务器依赖

MCP 服务器通过 `uvx heapdump-stardiver-mcp` 运行，`uvx` 会自动处理 Python 依赖的安装。如果 MCP 服务器无法启动，请确保已安装 `uv`（Python 包管理器）。

## 浪费检测参考

MCP 工具 `analyze_heap` 运行以下检查，通过 `waste_tier` 参数控制检测深度：

| 层级 | 检查项                                   | 检测内容                                                     |
| ---- | ---------------------------------------- | ------------------------------------------------------------ |
| 1    | 重复字符串（Duplicate Strings）          | 具有相同 byte[] 内容的字符串                                 |
| 1    | 低效集合（Bad Collections）              | 空的或只有单个元素的 HashMap、ArrayList、LinkedList、TreeMap、ConcurrentHashMap |
| 1    | 低效对象数组（Bad Object Arrays）        | 零长度、全 null、单元素、稀疏（>70% 为 null）的对象数组      |
| 1    | 低效基本类型数组（Bad Primitive Arrays） | 零长度、全零、单元素的 8 种基本类型数组                      |
| 1    | 装箱基本类型（Boxed Primitives）         | Integer、Long、Double 等包装类的额外开销                     |
| 2    | 集合容量问题（Collection Sizing）        | 稀疏 HashMap（利用率 <33%）、ArrayList 后备数组过大          |
| 2    | 重复 byte[]（Duplicate byte[]）          | 相同的字节数组（MD5 哈希比较，数组 ≤10KB）                   |
| 2    | 类数量（Class Count）                    | 超过 20K 个类，可能存在类加载器泄漏                          |
| 2    | GC 根（GC Roots）                        | 根类型分布（线程膨胀、JNI 泄漏）                             |
| 2    | DirectByteBuffer                         | 堆外内存容量、空缓冲区                                       |
| 2    | 线程栈（Thread Stacks）                  | 线程数量和栈深度分析                                         |
| 3    | 重复对象数组（Duplicate Object Arrays）  | 元素相同且顺序一致的数组                                     |
| 3    | 估算浅层大小（Estimated Shallow Size）   | 按类型估算的堆内存使用量                                     |

## query_heap 示例 SQL

Parquet 文件使用 robo 模式约定：分块文件（`_chunk*.parquet`），引用使用裸 UInt64 ID，类型查找使用单独的 `_object_index` 表。

```sql
-- 按数量排序的前 N 个类型
SELECT type_name, COUNT(*) as cnt
FROM read_parquet('_object_index_chunk*.parquet')
GROUP BY type_name ORDER BY cnt DESC LIMIT 20

-- 线程栈分析
SELECT sf.class_name, sf.method_name, COUNT(*) as appearances
FROM '_stack_traces.parquet' st, UNNEST(st.frame_ids) AS t(fid)
JOIN '_stack_frames.parquet' sf ON sf.frame_id = t.fid
GROUP BY sf.class_name, sf.method_name
ORDER BY appearances DESC LIMIT 10

-- 重复字符串及浪费估算
WITH str_bytes AS (
    SELECT s.obj_id, s.value as byte_id,
           md5(CAST(b.values AS VARCHAR)) as hash, len(b.values) as len
    FROM read_parquet('java.lang.String_*_chunk*.parquet') s
    JOIN read_parquet('_primitive_arrays_byte_chunk*.parquet') b ON s.value = b.obj_id
)
SELECT hash, COUNT(*) as dups, MIN(len) as str_len
FROM str_bytes GROUP BY hash HAVING COUNT(*) > 1
ORDER BY dups * str_len DESC LIMIT 20

-- 根据对象 ID 查找其所属类型
SELECT * FROM read_parquet('_object_index_chunk*.parquet')
WHERE obj_id = 12345678

-- 按类型统计 GC 根
SELECT root_type, COUNT(*) as cnt
FROM read_parquet('_gc_roots_chunk*.parquet')
GROUP BY root_type ORDER BY cnt DESC

-- ============================================================================
-- GC Root 引用链查询（新增）
-- ============================================================================

-- 1. GC Root → 类名关联：消除 Unknown，按类名统计
SELECT
    gr.root_type,
    oi.type_name,
    COUNT(*) as cnt
FROM read_parquet('_gc_roots_chunk*.parquet') gr
LEFT JOIN read_parquet('_object_index_chunk*.parquet') oi
    ON gr.object_id = oi.obj_id
GROUP BY gr.root_type, oi.type_name
ORDER BY gr.root_type, cnt DESC

-- 2. GC Root → 线程栈引用链：构建完整引用路径
SELECT
    gr.root_type,
    gr.root_info,
    gr.object_id,
    oi.type_name as target_class,
    sf.class_name as frame_class,
    sf.method_name as frame_method,
    sf.line_number
FROM read_parquet('_gc_roots_chunk*.parquet') gr
LEFT JOIN read_parquet('_object_index_chunk*.parquet') oi
    ON gr.object_id = oi.obj_id
LEFT JOIN read_parquet('_stack_traces.parquet') st
    ON gr.root_info::BIGINT = st.thread_id
LEFT JOIN read_parquet('_stack_frames.parquet') sf
    ON sf.frame_id = st.frame_ids
WHERE gr.root_type = 'JAVA_STACK'
ORDER BY gr.root_type, target_class
LIMIT 100

-- 3. 高实例数类 + GC Root 关联：找出被 GC Root 直接持有的高实例数类
WITH high_count_classes AS (
    SELECT type_name, COUNT(*) as cnt
    FROM read_parquet('_object_index_chunk*.parquet')
    GROUP BY type_name
    HAVING cnt > 1000
),
rooted_classes AS (
    SELECT oi.type_name, COUNT(DISTINCT gr.root_info) as root_count
    FROM read_parquet('_gc_roots_chunk*.parquet') gr
    JOIN read_parquet('_object_index_chunk*.parquet') oi
        ON gr.object_id = oi.obj_id
    GROUP BY oi.type_name
)
SELECT h.type_name, h.cnt as instance_count, r.root_count
FROM high_count_classes h
JOIN rooted_classes r ON h.type_name = r.type_name
ORDER BY r.root_count DESC, h.cnt DESC
```