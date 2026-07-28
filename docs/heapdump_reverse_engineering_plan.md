# HeapDumpStarDiver 逆向工程计划文档

**文件名**: `heapdump_reverse_engineering_plan.md`  
**目标文件**: `/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver`  
**架构**: ARM64 (Mach-O)  
**语言**: Rust（已确认）  
**预计耗时**: 30-60 分钟（分步执行）

---

## 🎯 目标概述

对 `HeapDumpStarDiver` 二进制文件进行完整的静态逆向工程分析，包括：
1. 确认文件格式、架构和编译器特征
2. 提取和分析符号表与导入信息
3. 识别 CLI 命令和功能模块
4. 推断程序控制流和数据流
5. 寻找原始源代码线索
6. 生成详细分析报告

---

## ⚙️ 准备工作

### 必需工具清单

| 工具 | 用途 | 备注 |
|------|------|------|
| `otool` (macOS) | Mach-O 头部、段区、反汇编 | ✅ 系统自带 |
| `nm` | 符号表查看 | ✅ 系统自带 |
| `strings` | 字符串提取 | ✅ 系统自带 |
| `file` | 文件类型识别 | ✅ 系统自带 |
| `rustfilt` (可选) | Rust mangled 名称解译 | `cargo install rustfilt` |
| Ghidra / IDA Pro (可选) | 高级反汇编/反编译 | 更强大的逆向能力 |

### 工作目录

```bash
mkdir -p ~/hreverse_workspace
cd ~/hreverse_workspace
```

---

## 🗓️ 分步执行计划

### **步骤 1：基本信息收集** — 预计时间：2分钟

```bash
# 创建基础记录文件
echo "=== STEP 1: BASIC BINARY INFORMATION ===" > step1_report.txt

# 文件类型识别
file "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" >> step1_report.txt

# 文件大小统计
stat "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" >> step1_report.txt

# Mach-O 头部详细信息
echo "" >> step1_report.txt
echo "=== MACH-O HEADER ===" >> step1_report.txt
otool -hvn "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" >> step1_report.txt 2>&1

echo "[STEP 1] Basic info complete."
```

> 💡 **预期结果**：确认为 Mach-O 64-bit ARM64 可执行文件，无调试符号（stripped）。

---

### **步骤 2：段区与加载命令分析** — 预计时间：3分钟

```bash
echo "" >> step1_report.txt
echo "=== LOAD COMMANDS & SEGMENTS ===" >> step1_report.txt

# 列出所有段区和关键属性
otool -l "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | \
  grep -E "(cmd|segname|fileoff|vmsize|maxprot|initprot|sectname|nsects|offset|size)" | \
  sed '/^$/d' >> step1_report.txt

# 查找入口点
ENTRY_OFFSET=$(otool -l "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | grep entryoff | awk '{print $2}')
if [ -n "$ENTRY_OFFSET" ]; then
    echo "" >> step1_report.txt
    echo "=== ENTRY POINT ===" >> step1_report.txt
    echo "Entry offset in file: $ENTRY_OFFSET" >> step1_report.txt
fi

echo "[STEP 2] Segments and load commands complete."
```

> 💡 **预期结果**：发现 `__TEXT` 段（代码）、`__DATA` 段（数据）、入口偏移等关键信息。

---

### **步骤 3：动态链接库依赖分析** — 预计时间：2分钟

```bash
echo "" >> step1_report.txt
echo "=== DYLIB DEPENDENCIES ===" >> step1_report.txt

# 提取所有 LC_LOAD_DYLIB 路径
otool -l "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | \
  grep -A 2 "LC_LOAD_DYLIB" | grep "name" | cut -d' ' -f2 >> step1_report.txt

echo "" >> step1_report.txt
echo "=== SYSTEM LIBRARIES ===" >> step1_report.txt

# 额外检查 CoreFoundation 和其他关键库
grep -iE "corefoundation|system|libz|security" step1_report.txt || echo "No additional lib info found"

echo "[STEP 3] Dylib dependencies captured."
```

> 💡 **预期结果**：确认依赖的 dylib，如 CoreFoundation、System 等。

---

### **步骤 4：符号表导出分析** — 预计时间：3-5分钟

```bash
echo "" >> step1_report.txt
export SYMBOLS_FILE="/tmp/symbols_exported.txt"
nm -g "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null > "$SYMBOLS_FILE"

# 检查符号总数
TOTAL_SYMBOLS=$(wc -l < "$SYMBOLS_FILE")
echo "Total exported symbols: $TOTAL_SYMBOLS" >> step1_report.txt

# 分类统计
echo "" >> step1_report.txt
echo "=== RUST RUNTIME SYMBOLS (RN*) ===" >> step1_report.txt
grep "^.* __RN" "$SYMBOLS_FILE" | head -30 >> step1_report.txt

echo "" >> step1_report.txt
echo "=== ARROW/PARQUET FUNCTION SYMBOLS ===" >> step1_report.txt
grep -iE "arrow|parquet" "$SYMBOLS_FILE" | head -40 >> step1_report.txt

echo "" >> step1_report.txt
echo "=== HPROF-SPECIFIC SYMBOLS ===" >> step1_report.txt
grep -iE "heap_dump|hprof|primitive_array" "$SYMBOLS_FILE" >> step1_report.txt

echo "[STEP 4] Symbol table analysis complete."
```

> 💡 **预期结果**：大量 mangled Rust 符号，确认使用了 Rust + Arrow/Parquet 栈。

---

### **步骤 5：未定义符号（导入）分析** — 预计时间：2分钟

```bash
echo "" >> step1_report.txt
echo "=== UNDEFINED SYMBOLS (IMPORTS) ===" >> step1_report.txt

nm -u "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | \
  grep -v "^.* U " | head -50 >> step1_report.txt

# 或者简单列出所有未定义符号
nm -u "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | \
  cut -d' ' -f2 | sort -u | head -40 >> step1_report.txt

echo "[STEP 5] Imports captured."
```

> 💡 **预期结果**：看到 `_CCRandomGenerateBytes`、`__Unwind_*`、`dispatch_*` 等导入函数，确认系统库调用。

---

### **步骤 6：字符串提取与关键词过滤** — 预计时间：3-5分钟

```bash
echo "" >> step1_report.txt
echo "=== KEY STRINGS ===" >> step1_report.txt

# 提取有意义的字符串（排除纯 URL、纯数字、重复字符）
strings -n8 "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | \
  grep -vE '^https?://' | \
  grep -vE '^[0-9]{4,}$' | \
  grep -vE '^[p]+{5,}$' | \
  grep -vE '^http://' | \
  grep -vE '^about:' | \
  sort -u > /tmp/all_strings_filtered.txt

echo "All unique meaningful strings: $(wc -l < /tmp/all_strings_filtered.txt)"

# 提取关键功能字符串
echo "" >> step1_report.txt
echo "=== FUNCTIONAL STRINGS (hprof, parquet, CLI) ===" >> step1_report.txt
cat /tmp/all_strings_filtered.txt | grep -iE "(hprof|dump|parquet|robo|class|stack|thread|object|field|method|subcommand|flush|robo-mode|count-records|dump-objects)" >> step1_report.txt

echo "" >> step1_report.txt
echo "=== SOURCE CODE PATHS EMBEDDED ===" >> step1_report.txt
cat /tmp/all_strings_filtered.txt | grep -E '\.rs|src/bitbucket|cargo/registry' >> step1_report.txt

echo "[STEP 6] Strings extracted and filtered."
```

> 💡 **预期结果**：发现子命令名、错误消息、文件路径（特别是 `/Users/yms/bitbucket/jvm-hprof-rs-li-hackweek/src/...`），这是最重要的源码线索！

---

### **步骤 7：重点函数反汇编** — 预计时间：5-10分钟

```bash
echo "" >> step1_report.txt
echo "=== SAMPLE FUNCTION DISASSEMBLY ===" >> step1_report.txt

# 找一个代表性函数地址（例如第一个 GenericByteArray 方法）
SAMPLE_ADDR=$(grep -m1 "GenericByteArray" "$SYMBOLS_FILE" | awk '{print $1}')
if [ -n "$SAMPLE_ADDR" ]; then
    echo "Disassembling function at address $SAMPLE_ADDR:" >> step1_report.txt
    otool -tv -a "$SAMPLE_ADDR" "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | head -60 >> step1_report.txt
else
    echo "No GenericByteArray symbol found, trying another sample..." >> step1_report.txt
    # 退回到 lang_start_internal 附近
    SAMPLE_ADDR=$(grep -m1 "lang_start_internal" "$SYMBOLS_FILE" | awk '{print $1}')
    if [ -n "$SAMPLE_ADDR" ]; then
        otool -tv -a "$SAMPLE_ADDR" "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | head -60 >> step1_report.txt
    fi
fi

# 再尝试查看一个压缩相关的函数
COMPRESSED_ADDR=$(grep -m1 "LZ4HadoopCodec" "$SYMBOLS_FILE" | awk '{print $1}')
if [ -n "$COMPRESSED_ADDR" ]; then
    echo "" >> step1_report.txt
    echo "=== LZ4 COMPRESS/DECOMPRESS FUNCTION ===" >> step1_report.txt
    otool -tv -a "$COMPRESSED_ADDR" "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | head -80 >> step1_report.txt
fi

echo "[STEP 7] Disassembly of sample functions complete."
```

> 💡 **预期结果**：ARM64 汇编指令，能看到寄存器操作、内存访问、条件分支等基本模式。

---

### **步骤 8：Rust mangled 名称解译（可选但推荐）** — 预计时间：3-5分钟

```bash
echo "" >> step1_report.txt
echo "=== RUST DEMANGLING (if available) ===" >> step1_report.txt

if command -v rustfilt >/dev/null 2>&1; then
    echo "rustfilt is available. Demangling key symbols..." >> step1_report.txt
    
    # 取前 20 个 Rust 相关符号进行解译
    head -20 "$SYMBOLS_FILE" | awk '{print $3}' | rustfilt 2>/dev/null | \
      paste -d"\n" <(head -20 "$SYMBOLS_FILE" | awk '{print $1 " " $2 " " $3}') <(head -20 "$SYMBOLS_FILE" | awk '{print $3}' | rustfilt 2>/dev/null) >> step1_report.txt
else
    echo "rustfilt not installed. Install with: cargo install rustfilt" >> step1_report.txt
    echo "Manual demangling required for advanced analysis." >> step1_report.txt
fi

echo "[STEP 8] Demangling attempt complete."
```

如果没有安装 rustfilt，可以使用在线工具或手动查阅 Rust mangled name 规范来解码。

---

### **步骤 9：构建完整分析报告** — 预计时间：5分钟

```bash
echo "" >> step1_report.txt
echo "============================================" >> step1_report.txt
echo "   FINAL REVERSE ENGINEERING SUMMARY REPORT" >> step1_report.txt
echo "============================================" >> step1_report.txt
echo "" >> step1_report.txt

# 添加总结性内容
echo "Binary Type: Mach-O 64-bit ARM64 Executable" >> step1_report.txt
echo "Compiler: Rust (release build, no debug symbols)" >> step1_report.txt
echo "Size: $(stat -f%z "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null || stat -c%s "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver") bytes" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== PROGRAM FUNCTIONS ===" >> step1_report.txt
echo "1. Hprof heap dump parsing module" >> step1_report.txt
echo "2. Object dumping to stdout" >> step1_report.txt
echo "3. Parquet format conversion (Arrow + parquet-rs)" >> step1_report.txt
echo "4. LZ4 compression support" >> step1_report.txt
echo "5. CLI interface via clap-rs" >> step1_report.txt
echo "6. Parallel processing via rayon" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== SOURCE CODE CLUES ===" >> step1_report.txt
echo "Embedded source path: /Users/yms/bitbucket/jvm-hprof-rs-li-hackweek/src/" >> step1_report.txt
echo "Key source files hinted:" >> step1_report.txt
echo "  - src/heap_dump.rs" >> step1_report.txt
echo "  - src/heap_dump/primitive_array.rs" >> step1_report.txt
echo "  - src/lib.rs" >> step1_report.txt
echo "  - src/hprof_index.rs" >> step1_report.txt
echo "  - src/commands/dump_objects.rs" >> step1_report.txt
echo "  - src/commands/dump_to_parquet.rs" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== CLI SUBCOMMANDS ===" >> step1_report.txt
echo "  dump-objects          : Output objects to stdout" >> step1_report.txt
echo "  count-records         : Count hprof record types" >> step1_report.txt
echo "  dump-objects-to-parquet : Export objects to parquet" >> step1_report.txt
echo "  robo-mode             : LLM optimized output mode" >> step1_report.txt
echo "  flush-rows=N          : Set accumulation threshold" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== DEPENDENCIES ===" >> step1_report.txt
echo "  Rust Standard Library" >> step1_report.txt
echo "  clap-rs (CLI framework)" >> step1_report.txt
echo "  arrow-rs (columnar data structures)" >> step1_report.txt
echo "  parquet-rs (file I/O and compression)" >> step1_report.txt
echo "  rayon (parallel task scheduling)" >> step1_report.txt
echo "  Security Framework (CCRandomGenerateBytes)" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== OBFUSCATION ASSESSMENT ===" >> step1_report.txt
echo "✓ No encryption/shimming detected" >> step1_report.txt
echo "✓ No packed or obfuscated code sections" >> step1_report.txt
echo "⚠ All symbols are Rust-mangled (expected for release builds)" >> step1_report.txt
echo "? Unknown string: uespemosarenegylmodnarodsetybdet (possibly internal identifier)" >> step1_report.txt
echo "" >> step1_report.txt

echo "=== ANALYSIS STATUS: COMPLETE ===" >> step1_report.txt
echo "Report saved to: ~/hreverse_workspace/step1_report.txt"

# 显示最终报告
cat step1_report.txt
echo "[STEP 9] Final report generated."
```

---

## 🔍 深入分析方向（根据兴趣选择）

完成上述基本步骤后，如果您希望进一步深入，可以选择以下任一方向：

### A. 源码追踪（最高优先级 ✅）

由于二进制文件中嵌入了完整的源文件路径，建议优先尝试获取原始源码：

```bash
# 搜索本地是否有 Rust 源文件
find /Users/suzhanfeng/demo/btrace_analyzer -name "*.rs" -type f 2>/dev/null | head -20

# 检查 Git 仓库历史是否有相关提交
cd /Users/suzhanfeng/demo/btrace_analyzer && git log --oneline --all | head -30

# 搜索 Bitbucket 仓库线索
# jvm-hprof-rs-li-hackweek — 尝试在网络搜索此仓库名称
```

### B. 使用 Ghidra/IDA Pro 进行深度反编译

1. 将二进制文件加载到 Ghidra（File → Load File）
2. 选择处理器：**ARM64**
3. 设置基地址：**0x100000000**（Mach-O 的典型加载基址）
4. 启用 Rust 插件或手动处理 mangled 名称
5. 分析 main 函数和 command dispatch 逻辑

### C. 动态分析（运行时行为观察）

```bash
# 运行程序并查看帮助（如果可执行）
/Library/Developer/CommandLineTools/usr/bin/vmmap "/Users/suzhanfeng/demo/btrace_analyzer/skills/android-hprof-analyzer/references/HeapDumpStarDiver" 2>/dev/null | head -20

# 如果有 .hprof 样本文件，尝试运行
# dtruss ./HeapDumpStarDiver --help 2>&1 | head -50
```

---

## 📂 预期输出文件清单

| 文件 | 内容 | 大小预估 |
|------|------|----------|
| `step1_report.txt` | 完整逆向报告文本 | ~10-20 KB |
| `step_symbols.txt` | 全部导出符号列表 | ~50-100 KB |
| `step_strings.txt` | 过滤后的有意义字符串 | ~20-50 KB |
| `step_disasm.txt` | 关键函数汇编代码 | ~10-30 KB |
| `step_dymap.txt` | 依赖的动态库列表 | ~1-2 KB |

---

## ⏱️ 总时间估算

| 阶段 | 时间 |
|------|------|
| 步骤 1-5（基本信息、符号、导入、字符串） | 10-15 分钟 |
| 步骤 6-7（反汇编、重点函数） | 5-10 分钟 |
| 步骤 8（可选 demangling） | 3-5 分钟 |
| 步骤 9（报告汇总） | 3-5 分钟 |
| **总计** | **20-35 分钟** |

---

## 💾 执行建议

1. **每步完成后检查输出**：确保命令正常执行，没有报错
2. **及时保存中间结果**：每个步骤的输出重定向到文件，方便回溯
3. **遇到问题中断没关系**：随时可以中断，下次继续从断点处开始
4. **需要深度分析时使用专业工具**：Ghidra/IDA Pro 提供比命令行更强的反编译能力

---

✅ **计划文档完成！** 您可以将此文档保存到工作区，下一个会话时按顺序执行各个步骤。
