# OMC (oh-my-claudecode) 使用指南

## 概述

OMC 是 Claude Code 的多智能体编排层，已在本项目通过 **Claude Code Plugin** 方式安装（v4.15.4）。它让 Claude Code 能够自动委派任务给专业 agent，使用智能模型路由，并在会话内执行并行编排工作流。

### 已安装内容

- **插件**：`oh-my-claudecode@omc`（用户级，全局可用）
- **项目配置**：`.claude/CLAUDE.md`（项目级，仅影响本项目）
- **Skills**：`omc-reference` 已注册到 `.claude/skills/`
- **状态目录**：`.omc/`（运行时状态、计划、日志等，已被 gitignore）

### 作用域说明

| 配置 | 位置 | 作用范围 |
|------|------|----------|
| 插件全局配置 | `~/.claude/CLAUDE.md` | 所有项目 |
| 本项目配置 | `.claude/CLAUDE.md` | 仅 btrace_analyzer |

项目级配置优先于全局配置。

---

## 快速开始

在 Claude Code 会话中直接输入以下命令即可使用。不需要终端 CLI。

### 基础用法

```
/team 3:executor "修复所有 TypeScript 编译错误"
/autopilot "实现一个 hprof 文件解析器"
/ralph "重构内存泄漏检测逻辑"
/ultrawork "同时分析 3 个 hprof 文件并生成报告"
```

### 自然语言触发

除了 slash 命令，还可以直接在对话框中使用关键词触发：

| 关键词 | 触发的工作流 |
|--------|-------------|
| `autopilot` | 全自主执行（从想法到可运行代码） |
| `ralph` | 持续循环直到完成并验证 |
| `ulw` / `ultrawork` | 高并发并行执行 |
| `ccg` | Codex + Antigravity + Claude 三方综合 |
| `deep interview` | 苏格拉底式需求澄清 |
| `tdd` | TDD 模式 |
| `deepsearch` | 代码库深度搜索 |
| `ultrathink` | 深度推理模式 |
| `cancelomc` | 取消当前 OMC 模式 |

---

## 核心工作流

### 1. Team（团队编排）— 推荐

按阶段流水线执行：`team-plan → team-prd → team-exec → team-verify → team-fix`

```bash
# 3 个 executor 并行修复错误
/team 3:executor "fix all build errors"

# 1 个 debugger 排查根因
/team 1:debugger "分析 btrace 解析器中的空指针问题"

# Ralph 模式：团队执行 + 持续验证
/team ralph "完成 hprof 文件分析功能"

# 带 Codex CLI worker（需安装 @openai/codex）
/team 2:codex "review auth module for security issues"
```

**适用场景**：需要多个 agent 协作完成一个复杂任务。

### 2. Autopilot（自动驾驶）

从一句话描述到完整可运行代码，自动完成需求分析、设计、规划、并行实现、QA 验证全流程。

```bash
/autopilot "为 btrace_analyzer 添加 Perfetto trace 可视化预览功能"
```

**适用场景**：从零构建新功能，希望全自动完成。

### 3. Ralph（坚持模式）

带 PRD 驱动的持久化循环，失败自动重试，必须全部验证通过才算完成。

```bash
/ralph "重构 hprof 对象图遍历逻辑，确保内存泄漏检测准确"
```

**适用场景**：任务必须完成、不允许部分交付，需要多轮迭代验证。

### 4. Ultrawork（高并发）

并行派发多个独立任务，适合一次性批量处理。

```bash
/ultrawork "同时分析这 5 个 .hprof 文件，每个生成一份独立的内存报告"
```

**适用场景**：多个独立任务需要同时执行，但由用户自己管理完成。

### 5. Deep Interview（深度访谈）

通过苏格拉底式提问澄清模糊需求，在写代码前暴露隐藏假设。

```bash
/deep-interview "我想做一个 Android 性能分析工具"
```

**适用场景**：需求不明确、想先理清思路再动手。

---

## 可用 Agent 角色

OMC 内置了 19+ 个专业 agent，自动根据任务类型路由：

| Agent | 模型 | 用途 |
|-------|------|------|
| `explore` | haiku | 快速代码搜索和映射 |
| `analyst` | opus | 需求分析和隐藏约束发现 |
| `planner` | opus | 执行计划和任务排序 |
| `architect` | opus | 系统设计和边界权衡 |
| `debugger` | sonnet | 根因分析和故障诊断 |
| `executor` | sonnet | 代码实现和重构 |
| `verifier` | sonnet | 完成证据和验证 |
| `tracer` | sonnet | 追踪和证据收集 |
| `security-reviewer` | sonnet | 信任边界和安全漏洞 |
| `code-reviewer` | opus | 全面代码审查 |
| `test-engineer` | sonnet | 测试策略和回归覆盖 |
| `designer` | sonnet | UX 和交互设计 |
| `writer` | haiku | 文档和简洁内容 |
| `git-master` | sonnet | 提交策略和版本历史 |
| `critic` | opus | 计划和设计的挑战与评审 |

**模型路由规则**：
- `haiku` — 快速查找、轻量检查、窄范围文档
- `sonnet` — 标准实现、调试、审查
- `opus` — 架构设计、深度分析、高风险审查

---

## 实用技能

### 查看 OMC 参考

```bash
/oh-my-claudecode:omc-reference
```

### 自定义 Skill

```bash
/oh-my-claudecode:skill list        # 列出所有 skills
/oh-my-claudecode:skill add <name>  # 添加新 skill
/oh-my-claudecode:skill remove <name> # 移除 skill
/oh-my-claudecode:skill edit <name>   # 编辑 skill
/oh-my-claudecode:skill search <query> # 搜索 skill
```

### 更新 CLAUDE.md

```bash
/oh-my-claudecode:omc-setup              # 首次设置或更新
/oh-my-claudecode:omc-setup --local      # 仅更新本项目配置
/oh-my-claudecode:omc-setup --global     # 仅更新全局配置
/oh-my-claudecode:omc-setup --force      # 强制重新运行完整向导
```

### 停止 OMC 模式

```bash
/oh-my-claudecode:cancel
# 或在对话中输入 "cancelomc" / "stopomc"
```

### 健康检查

```bash
/oh-my-claudecode:doctor
```

---

## 在 btrace_analyzer 项目中的典型用法

### 分析 hprof 文件

```
/autopilot "分析这个 hprof 文件并找出内存泄漏根因"
/ralph "对 demo.hprof 进行完整的 GC root 追踪分析"
```

### 批量处理

```
/ultrawork "并行分析 src/ 下所有 .java 文件中的内存泄漏风险"
/team 2:analyzer "对比两个 hprof 文件的对象分布差异"
```

### 代码重构

```
/team 3:executor "重构 hprof 解析器，将大方法拆分为小函数"
/ralph "将 btrace 分析结果导出为 JSON 格式"
```

### 质量检查

```
/team 1:verifier "验证所有 hprof 解析测试用例通过"
/team 1:security-reviewer "审查 hprof 文件解析的安全性"
```

### 需求澄清

```
/deep-interview "我想添加一个实时 hprof 文件预览功能"
```

---

## 配置与状态

### 状态存储

OMC 在项目中创建 `.omc/` 目录存放运行时状态：

```
.omc/
├── state/          # 会话状态和工作流状态
├── plans/          # 生成的计划
├── research/       # 研究产出
├── logs/           # 日志
├── artifacts/      # 产出物
├── handoffs/       # 团队交接记录
└── skills/         # 项目级 OMC skills（可提交到 git）
```

其中 `.omc/skills/**` 是唯一的例外——如果团队想共享 OMC 提取的经验模式，可以提交这些文件。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DISABLE_OMC` | 未设置 | 设为任意值可禁用所有 OMC hooks |
| `OMC_SKIP_HOOKS` | 未设置 | 逗号分隔的 hook 名称列表，跳过指定 hooks |
| `OMC_STATE_DIR` | 未设置 | 集中状态目录，worktree 删除后仍可保留 |

### 临时禁用

不想用 OMC 时，设置环境变量：

```bash
export DISABLE_OMC=1
claude
```

---

## 注意事项

1. **不要重复启动**：同一会话中只使用一种主工作流（Team / Ralph / Autopilot），避免互相干扰。
2. **Cancel 要彻底**：完成或遇到问题时，用 `/oh-my-claudecode:cancel` 或输入 `cancelomc` 正确退出模式。
3. **状态会跨会话恢复**：Ralph 和 Team 的状态保存在 `.omc/` 中，中断后可以继续。
4. **外部 AI provider 可选**：Codex / Gemini / Antigravity 不是必须的，OMC 在没有它们的情况下也能正常工作。
5. **Claude Code 原生团队**：需要 Claude Code 2.1.178+，并在 `~/.claude/settings.json` 中启用：
   ```json
   { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
   ```
