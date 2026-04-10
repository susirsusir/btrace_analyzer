# Report Format & Method Name Annotation Rules

## Report Template

Save to `trace-analysis/<traceID>/report.md`. All screenshots go in the same directory with relative paths.

```markdown
# BTrace Performance Analysis Report

**Trace file**: <trace_url>
**Device**: <extracted from trace if available>
**Date**: <current date>

## Summary

<1-2 sentence overview of the trace health and key findings>

## Issues

### [P0] Issue Title
- **What**: Brief description
- **Where**: `com.example.ClassName.method()`
- **Duration**: Xms (single) or Xms avg × N occurrences
- **Impact**: What the user experiences
- **Call Stack**:
  ```
  com.example.BusinessClass.entryMethod()          ← business entry point
    → com.library.SomeClass.intermediateCall()
      → com.problematic.Class.slowMethod()          ← bottleneck
  ```
- **Suggestion**: Concrete fix referencing the specific class/method to modify

![Issue 1 - Description](screenshot_1.png)

### [P1] Issue Title
...

## Timeline Overview

![Full timeline](screenshot_overview.png)
```

## Report Rules

- Order issues strictly by severity: P0 → P1 → P2 → P3
- Include a full timeline overview screenshot at the end
- Use relative image paths so they render on GitHub
- If no significant issues found, state the trace is healthy with a summary screenshot
- `trace-analysis/` is in `.gitignore` — reports are local analysis artifacts

## Severity Classification

| Severity | Threshold | Meaning |
|----------|-----------|---------|
| P0 | > 500ms | Blocking — user perceives freeze |
| P1 | > 200ms | Severe — noticeable lag |
| P2 | > 100ms | Moderate — may cause jank on low-end devices |
| P3 | ≤ 100ms | Minor — worth noting |

## Method Name Annotation Rules

BTrace mapping files restore obfuscated names, but Kotlin-generated and ProGuard names still need annotation:

| Pattern in trace | Meaning | How to annotate |
|---|---|---|
| `ClassName$methodName$1$2.invokeSuspend()` | Kotlin coroutine lambda: 2nd nested lambda inside `methodName` | `← Kotlin lambda in ClassName.methodName()` |
| `ClassName$methodName$1.invokeSuspend()` | Kotlin suspend lambda / coroutine continuation | `← coroutine continuation of ClassName.methodName()` |
| `ClassName$propertyName$2.invoke()` | Kotlin lazy property initializer | `← lazy init of ClassName.propertyName` |
| `ClassName$1.run()` | Anonymous Runnable/Thread | `← anonymous Runnable in ClassName` |
| `ClassName.a3()` / `.v3()` / `.F3()` | ProGuard-obfuscated (single letter + digit) | `← obfuscated method (ProGuard)` — do NOT guess original name |
| `msdocker.*` / `Ill111l` / `illi` | DroidPlugin + string encryption internals | `← DroidPlugin hook / string decryption` |

Always annotate Kotlin-generated class names with `←` comments in call stacks so readers understand what the synthetic class represents.
