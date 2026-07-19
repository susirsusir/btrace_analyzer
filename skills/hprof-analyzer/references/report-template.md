# HPROF 内存分析报告模板

## 使用说明

将此模板复制到 `/tmp/hprof_analysis/report.md`，由分析脚本填充实际数据。

---

# HPROF 内存分析报告

**文件**: `<filepath>`
**大小**: `<size> MB`
**格式**: `<hprof-heap | hprof-libs>`
**设备/应用**: `<package name>`
**日期**: `<YYYY-MM-DD HH:mm>`

## 概要

> 1-3 句话总结内存健康状况，指出最关键的发现。

**关键指标：**

| 指标 | 数值 |
|------|------|
| 总类数 | `<N>` |
| 总对象数 | `<N>` |
| GC Root 数量 | `<N>` |
| 线程快照数 | `<N>` |
| 字符串表条目 | `<N>` |

## 堆分布

### 对象数量 Top 20

| 排名 | 类名 | 实例数 | 占比 |
|------|------|--------|------|
| 1 | `com.example.LeakyClass` | 12,345 | 23.5% |
| 2 | `android.widget.ListView` | 8,901 | 16.9% |
| ... | ... | ... | ... |

> 标注 Kotlin synthetic 类：`MyClass$onCreate$1` ← Kotlin synthetic
> 标注 ProGuard 混淆类：`a3()` ← obfuscated method (ProGuard)

### 内存占用 Top 20（浅大小 Shallow Size）

| 排名 | 类名 | 实例数 | 浅大小 |
|------|------|--------|--------|
| 1 | `byte[]` | 45,678 | 128.5 MB |
| 2 | `char[]` | 23,456 | 64.2 MB |
| ... | ... | ... | ... |

### 类实例分布

| 实例范围 | 类数量 | 对象数量 |
|----------|--------|----------|
| 0 | `<N>` | 0 |
| 1-9 | `<N>` | `<N>` |
| 10-99 | `<N>` | `<N>` |
| 100-999 | `<N>` | `<N>` |
| 1K-10K | `<N>` | `<N>` |
| 10K-100K | `<N>` | `<N>` |
| 100K+ | `<N>` | `<N>` |

## GC Root 分析

### Root 类型分布

| Root 类型 | 数量 | 说明 |
|-----------|------|------|
| JAVA_STACK | `<N>` | Java 栈帧引用 |
| NATIVE_STACK | `<N>` | Native 栈帧引用 |
| SYSTEM_CLASS | `<N>` | 系统类加载器引用 |
| GC_STATIC_FIELD | `<N>` | 静态字段引用 |
| GC_LOCAL | `<N>` | 局部变量引用 |
| GC_MONITOR | `<N>` | Monitor 锁引用 |
| GC_JAVA_FRAME | `<N>` | Java 帧引用 |
| HPROF_LIBS_BINARY | `<N>` | hprof-libs 专有格式 |

### 可疑 GC Root 引用链

> 以下 Root 引用链可能持有不应持有的强引用，导致内存泄漏。

#### [P0] `<标题>`

- **持有对象**: `<class name>`
- **实例数**: `<N>`
- **总大小**: `<size>`
- **Root 路径**:
  ```
  GC Root: JAVA_STACK (main)
    → android.app.ActivityThread.handleStopActivity(Activity.java:8901)
    → com.example.LiveGiftManager.mCachedGifts
  ```
- **字段**: `<field_name>` (`<type>`) ← `<说明>`
- **建议**: `<具体修复建议>`

#### [P1] `<标题>`

- **持有对象**: `<class name>`
- **实例数**: `<N>`
- **Root 路径**:
  ```
  GC Root: GC_STATIC_FIELD
    → com.example.CacheManager.instance
      → HashMap<...>
        → com.example.LargeObject[567 instances]
  ```
- **字段**: `<field_name>` (`<type>`) ← `<说明>`
- **建议**: `<具体修复建议>`

> 如果无法解析出具体字段名，显示 "字段: 未知"。
> 如果 root_kind 为 UNKNOWN，保留原始 root_info 值供调试。

## 内存泄漏检测

### 高实例数类（潜在泄漏）

以下类在 CLASS_DUMP 中有异常高的实例数，可能存在内存泄漏：

| 类名 | 实例数 | 风险评估 |
|------|--------|----------|
| `com.example.UnreleasedActivity` | 48,693,312 | 🔴 P0 |
| `com.example.LeakCollector` | 35,192,896 | 🔴 P0 |
| `com.example.NormalAdapter` | 662,528 | 🟡 P2 |

### 泄漏模式分析

#### 常见泄漏模式匹配

| 模式 | 匹配类 | 说明 |
|------|--------|------|
| Activity 未释放 | `<class>` | Activity 被静态集合持有 |
| 监听器未注销 | `<class>` | Listener/Callback 强引用未清理 |
| WebView 泄漏 | `<class>` | WebView 持有 Context 强引用 |
| Handler 泄漏 | `<class>` | Handler 持有外部类引用 |
| 静态集合膨胀 | `<class>` | static Map/List 无限增长 |

## 线程快照

### 活跃线程

| 线程名 | 状态 | 优先级 |
|--------|------|--------|
| main | RUNNABLE | 10 |
| Worker-1 | WAITING | 5 |
| GC | RUNNABLE | 1 |

### 线程栈快照

<来自 THREAD_SUSPEND / STACK_FRAME 的数据>

## 类字段布局

<来自 LOAD_DATA 的字段信息，展示关键类的字段结构>

| Class | 字段名 | 类型 | 偏移 |
|-------|--------|------|------|
| `MyClass` | `mCallback` | REF | 0x10 |
| `MyClass` | `mData` | REF | 0x14 |

## 风险评级

| 等级 | 条件 | 结论 |
|------|------|------|
| 🔴 P0 | > 50MB 泄漏对象 | 严重 — 需立即修复 |
| 🟠 P1 | > 20MB 泄漏对象 | 显著 — 应尽快修复 |
| 🟡 P2 | > 5MB 泄漏对象 | 中等 — 计划修复 |
| 🟢 P3 | ≤ 5MB 泄漏对象 | 轻微 — 值得关注 |

**总体评级**: `<P0/P1/P2/P3>`

## 附录

### 完整类实例统计表

<所有类的实例数排序列表>

### 格式说明

- **浅大小 (Shallow Size)**: 对象自身占用的内存，不包括引用的对象
- **深大小 (Retained Size)**: 对象自身 + 仅通过该对象可达的所有对象
- **GC Root**: 垃圾回收的起点，无法被 GC 的对象
- **hprof-libs**: Android 7.0+ 默认堆转储格式，与标准 hprof-heap 格式不同

### 方法名注释规则

| 模式 | 含义 | 注释方式 |
|------|------|----------|
| `$methodName$1.invokeSuspend()` | Kotlin 协程 lambda | `← coroutine continuation of ...` |
| `$propertyName$2.invoke()` | Kotlin lazy 初始化 | `← lazy init of ...` |
| `a3()` / `v3()` | ProGuard 混淆 | `← obfuscated method (ProGuard)` |
| `msdocker.*` | DroidPlugin | `← DroidPlugin hook` |
