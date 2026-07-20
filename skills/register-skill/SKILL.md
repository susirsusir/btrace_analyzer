---
name: register-skill
description: Register a skill directory into the project's .claude/skills/ and .kiro/skills/ directories so it appears as an available skill. Use when a new skill has been created in skills/ and needs to be linked for use.
---

## Overview

This skill registers newly created skills by creating symlinks under `.claude/skills/` and `.kiro/skills/`, pointing back to the canonical `skills/` directory. This keeps skills tied to the project — cloning the repo makes them immediately available without any global setup.

```
<project>/
  skills/
    hprof-analyzer/        ← source of truth (SKILL.md lives here)
    btrace-analyzer/
    register-skill/
  .claude/
    skills/
      hprof-analyzer → ../../skills/hprof-analyzer
      register-skill → ../../skills/register-skill
  .kiro/
    skills/
      hprof-analyzer → ../../skills/hprof-analyzer
      register-skill → ../../skills/register-skill
```

## How It Works

- Symlinks are used, not copies. Changes to `skills/<name>/SKILL.md` are reflected immediately in both Kiro and Claude Code — no restart needed.
- The relative symlink target is `../../skills/<skill-name>`, which works from either `.claude/skills/` or `.kiro/skills/`.
- If the symlink already exists with the correct target, it is left alone. If it points elsewhere, it is updated.

## Registration Steps

### Step 1: Identify the Skill Directory

The skill directory should contain a `SKILL.md` file at its root, under `skills/`:

```bash
ls skills/<skill-name>/SKILL.md
```

### Step 2: Register the Skill

Run this from the project root:

```bash
SKILL_NAME="<skill-name>"

for PROJECT_DIR in .claude .kiro; do
  SKILL_LINK="$PROJECT_DIR/skills/$SKILL_NAME"
  TARGET="../../skills/$SKILL_NAME"

  if [ -L "$SKILL_LINK" ]; then
    CURRENT=$(readlink "$SKILL_LINK")
    if [ "$CURRENT" = "$TARGET" ]; then
      echo "Already linked: $SKILL_LINK -> $TARGET"
    else
      rm "$SKILL_LINK"
      ln -s "$TARGET" "$SKILL_LINK"
      echo "Updated link: $SKILL_LINK -> $TARGET"
    fi
  elif [ -e "$SKILL_LINK" ]; then
    echo "Path exists and is not a symlink, skipping: $SKILL_LINK — manual review needed"
  else
    mkdir -p "$PROJECT_DIR/skills"
    ln -s "$TARGET" "$SKILL_LINK"
    echo "Linked: $SKILL_LINK -> $TARGET"
  fi
done
```

### Step 3: Verify Registration

```bash
ls -la .claude/skills/<skill-name>/SKILL.md
ls -la .kiro/skills/<skill-name>/SKILL.md
```

Both paths should resolve to the same `SKILL.md` under `skills/<skill-name>/`. The skill description from the frontmatter will now appear in the available skills list for auto-triggering.

## Unregistering a Skill

```bash
SKILL_NAME="<skill-name>"

for PROJECT_DIR in .claude .kiro; do
  SKILL_LINK="$PROJECT_DIR/skills/$SKILL_NAME"
  if [ -L "$SKILL_LINK" ]; then
    rm "$SKILL_LINK"
    echo "Removed: $SKILL_LINK"
  else
    echo "Not found or not a symlink: $SKILL_LINK"
  fi
done
```

## One-Liner for Quick Registration

```bash
SKILL_NAME="<skill-name>"; for D in .claude .kiro; do mkdir -p "$D/skills"; ln -sf ../../skills/$SKILL_NAME "$D/skills/$SKILL_NAME"; done && echo "Registered $SKILL_NAME"
```

## Notes

- The skill name must not contain spaces or special characters (use kebab-case or snake_case)
- The `SKILL.md` file must be at the root of the `skills/<skill-name>/` directory
- The frontmatter `name` and `description` fields determine how the skill appears in the AI assistant's skill picker
- Symlinked skills are read-only references to the canonical source under `skills/`; edit the source directly
- If the destination already exists as a non-symlink file/directory, the script skips it to avoid overwriting
