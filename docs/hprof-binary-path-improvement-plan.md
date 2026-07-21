# HPROF 二进制路径分析报告优化计划

> **目标**：将二进制路径从 F 级（4/20）提升到 B 级（17+/20），使其具备实际内存泄漏排查指导价值。
>
> **输入文件**：`hprof/taqu_android_client_logfile_401_1783731893047_1_1_342013740.hprof`（159 MB）
>
> **输出文件**：`hprof_analysis/<文件名>_binary_report.md`

---

## 📊 当前状态评估

### 已实现解析器 ✅

| 组件 | 状态 | 解析数量 | 备注 |
|------|------|----------|------|
| CHUNK 扫描 | ✅ | 12,101 chunks | hprof-libs 格式支持 |
| STRING_DUMP | ✅ | 1,469 strings | string_id → text 映射 |
| CLASS_DUMP | ✅ | 83 classes | 格式 B/C/A 三种大 chunk |
| OBJECT_DUMP | ✅ | 1,306 objects | marker-based 格式 |
| SAMPLE_GC_HEAP | ✅ | 50 GC Roots | 前缀同步处理已修复 |
| THREAD_SUSPEND | ✅ | 2,011 threads | 9-byte record format |
| STACK_FRAME | ✅ | 711 frames | marker-based + pre-marker |

### 核心瓶颈 ❌

| 瓶颈 | 影响 | 严重度 |
|------|------|--------|
| class_serial → class_name 映射缺失 | 报告全是 `serial_X`，无法识别类 | 🔴 P0 |
| GC Root 引用链未构建 | 无法展示 Thread → Stack → Object 路径 | 🔴 P0 |
| OBJECT_DUMP 大 chunk 未完全解析 | 对象数量偏低 | 🟡 P1 |
| LOAD_DATA 实例字段值未解析 | 无法看到对象持有什么引用 | 🟡 P1 |

### 质量评分

| 维度 | 当前得分 | 目标得分 | 差距 |
|------|----------|----------|------|
| 类名可识别度 | 0/4 | ≥3/4 | 🔴 需建立映射 |
| 对象实例关联 | 1/4 | ≥3/4 | 🟡 部分完成 |
| GC Root 分析深度 | 1/4 | ≥3/4 | 🟡 需构建引用链 |
| 泄漏诊断 actionable | 0/4 | ≥3/4 | 🔴 依赖类名映射 |
| 数据完整性 | 2/4 | ≥3/4 | 🟡 大 chunk 部分解析 |
| **总分** | **4/20** | **≥17/20** | **🔴 需大幅改进** |

---

## 📋 执行计划

### Phase 1: 建立 class_serial → class_name 映射（优先级 P0，核心瓶颈）

**目标**：找到 class_serial 与真实类名的映射关系，让报告能显示 `com.xmhaibao.gift.bean.LiveGiftInfo` 等完整类名。

**现状分析**：
- STRING_DUMP 解析出 1,469 个字符串，使用 string_id 作为 key
- CLASS_DUMP 解析出 83 个类，使用 class_serial 作为 key
- 两者不在同一编号空间，需要找到映射关系

**技术方案**：
1. 扫描所有 chunk payload，寻找 serial → string_id 的映射模式
2. 检查 LOAD_DATA chunk 是否包含 class_serial 与类名的关联
3. 参考 Parquet 路径 `_class_hierarchy.parquet` 的结构寻找线索
4. 可能需要从 hprof-libs header 扩展区域提取映射表

**验收标准**：
- [ ] 至少 50% 的 class_serial 能映射到可读类名
- [ ] Top 20 高实例数类能显示完整包名+类名
- [ ] Kotlin synthetic / ProGuard 混淆类能正确标注

**预计工作量**：4-6 小时

---

### Phase 2: 完善 THREAD_SUSPEND 大 chunk 解析（优先级 P0）

**目标**：完整解析所有 THREAD_SUSPEND chunks，获取全部线程对象 ID 列表。

**现状**：
- 已解析 2,011 个线程
- 需要验证大 chunk 是否完全覆盖

**技术方案**：
```
THREAD_SUSPEND 大 chunk 格式：
  [thread_obj_id(2B LE)] [0x0A] [0x7F] [suspend_type(1B)] [counter(1B)] [pad(2B)] [extra(1B)]
  每条记录恰好 9 字节
```

**验收标准**：
- [ ] 能解析所有 THREAD_SUSPEND chunks（包括大 chunk）
- [ ] 线程数量 > 100（当前只有 6 个）✅ 已完成
- [ ] 线程对象 ID 可用于链接 SAMPLE_GC_HEAP.root_info

**预计工作量**：1-2 小时（基本完成）

---

### Phase 3: 完善 STACK_FRAME 大 chunk 解析（优先级 P0）

**目标**：完整解析所有 STACK_FRAME chunks，建立 frame_id → 类/方法/行号映射。

**现状**：
- 已解析 711 个帧
- 需要验证大 chunk 是否完全覆盖

**技术方案**：
```
STACK_FRAME 大 chunk 格式：
  marker-based 表：[entry_data(variable)] [00 40 00 class_serial(1B)]
  预 marker 块：frame_id(4B) + class_serial(4B) + pad(4B) + method_index(4B) + line_number(4B)
```

**验收标准**：
- [ ] 帧数量显著增加（目标 > 2000）
- [ ] 每个帧包含 frame_id、class_serial、type_code
- [ ] 预 marker 块的完整元数据能正确解析

**预计工作量**：2-3 小时

---

### Phase 4: 构建 GC Root 引用链（优先级 P1）

**目标**：将 SAMPLE_GC_HEAP → THREAD_SUSPEND → STACK_FRAME → class_name 串联，生成人类可读的泄漏路径。

**Pipeline**：
```
SAMPLE_GC_HEAP.root_kind=JAVA_STACK → root_info(thread_obj_id)
    ↓
THREAD_SUSPEND → thread_obj_id → frame_ids[]
    ↓
STACK_FRAME → frame_id → class_serial → class_name
    ↓
最终输出: GC Root → Thread → Stack Frame → Target Object
```

**验收标准**：
- [ ] 能构建完整的 GC Root 引用链
- [ ] 引用链包含线程名、栈帧、目标类名
- [ ] Root 类型分布统计准确

**预计工作量**：2-3 小时

---

### Phase 5: 完善 OBJECT_DUMP 大 chunk 解析（优先级 P2）

**目标**：完整解析 OBJECT_DUMP 大 chunk，获取对象实例及其字段值。

**现状**：
- marker-based 格式已有基础解析
- 大 chunk 中对象数量可能远超当前解析量

**技术方案**：
1. 优化 marker-based 解析器的边界处理
2. 实现字段值提取（field_offset + type_code + value）
3. 关联 CLASS_DUMP 的 class_serial 获取类元数据

**验收标准**：
- [ ] 对象数量显著提升
- [ ] 每个对象能关联到类名和实例数
- [ ] 字段值能部分解析（至少看到引用类型的 object_id）

**预计工作量**：4-6 小时

---

### Phase 6: 增强报告生成与诊断能力（优先级 P2）

**目标**：生成可直接指导内存泄漏排查的 actionable 报告。

**改进内容**：
1. 使用真实类名替代 `serial_X`
2. 添加 Kotlin synthetic / ProGuard 混淆 / DroidPlugin 标注
3. 生成具体修复建议（含代码示例）
4. 区分真实泄漏 vs 正常高实例（如 Adapter 缓存）
5. 计算浅大小和估算深大小

**验收标准**：
- [ ] 对每个可疑类给出：实例数、可能原因、修复建议
- [ ] 能区分真实泄漏 vs 正常高实例
- [ ] 报告末尾质量评分达到 B 级（17+/20）

**预计工作量**：2-3 小时

---

## 📈 执行进度总览

| Phase | 主题 | 优先级 | 状态 | 完成时间 | 备注 |
|-------|------|--------|------|----------|------|
| Phase 1 | class_serial → class_name 映射 | P0 | ⬜ 待执行 | - | **核心瓶颈** |
| Phase 2 | THREAD_SUSPEND 大 chunk | P0 | ✅ 已完成 | - | 2,011 threads |
| Phase 3 | STACK_FRAME 大 chunk | P0 | ✅ 部分完成 | - | 711 frames |
| Phase 4 | GC Root 引用链 | P1 | ⬜ 待执行 | - | 依赖 Phase 1/2/3 |
| Phase 5 | OBJECT_DUMP 完善 | P2 | ⬜ 待执行 | - | - |
| Phase 6 | 报告增强 | P2 | ⬜ 待执行 | - | 依赖 Phase 1/4/5 |

**总体进度**: 2/6 阶段完成（Phase 2/3 基础完成，Phase 1 是下一个关键）

---

## 🔍 下一步行动

**立即执行 Phase 1**：建立 class_serial → class_name 映射

这是让报告从"不可用"变成"可用"的关键步骤。需要：
1. 深入分析 STRING_DUMP 和 CLASS_DUMP 之间的关联
2. 检查 Parquet 路径的数据结构作为参考
3. 可能需要在 hprof-libs header 或特定 chunk 中找到映射表
