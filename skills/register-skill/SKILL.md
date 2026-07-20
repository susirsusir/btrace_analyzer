---
name: register-skill
description: Register a skill directory to Kiro and/or Claude Code so it appears in the available skills list. Use when a new skill has been created and needs to be installed globally. Detects the actual setup (symlink-based or independent directories) and registers into the correct locations.
---

## Overview

This skill registers newly created skills so they are available as **top-level skills** in Kiro and/or Claude Code sessions. It detects the actual environment and installs into the right location(s).

## How It Works

### Preferred: Project-level registration

Skills are registered inside the project under `.claude/skills/` and `.kiro/skills/`, pointing to the canonical `skills/` directory. This keeps skills tied to the project — cloning the repo makes them immediately available without any global setup.

```
<project>/
  skills/
    hprof-analyzer/        ← source of truth
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

### Fallback: Global/shared directory registration

If the project-level approach isn't desired, or if `~/.kiro/skills/` / `~/.claude/skills/` are symlinks to a shared directory, install into the resolved real path instead.

## Registration Steps

### Step 1: Identify the Skill Directory

The skill directory should contain a `SKILL.md` file at its root.

```bash
# From the project root:
ls /path/to/skill-dir/SKILL.md
```

### Step 2: Register the Skill (project-level)

Run this from the project root. It creates symlinks under `.claude/skills/` and `.kiro/skills/` pointing back to `skills/`:

```bash
SKILL_SRC="skills/<skill-name>"
SKILL_NAME=$(basename "$SKILL_SRC")

for PROJECT_DIR in .claude .kiro; do
  SKILL_LINK="$PROJECT_DIR/skills/$SKILL_NAME"

  if [ -L "$SKILL_LINK" ]; then
    TARGET=$(readlink "$SKILL_LINK")
    if [ "$TARGET" = "$SKILL_SRC" ]; then
      echo "Already linked: $SKILL_LINK -> $SKILL_SRC"
    else
      rm "$SKILL_LINK"
      ln -s "$SKILL_SRC" "$SKILL_LINK"
      echo "Updated link: $SKILL_LINK -> $SKILL_SRC"
    fi
  elif [ -e "$SKILL_LINK" ]; then
    echo "Path exists (not a symlink), skipping: $SKILL_LINK — manual review needed"
  else
    mkdir -p "$PROJECT_DIR/skills"
    ln -s "$SKILL_SRC" "$SKILL_LINK"
    echo "Linked: $SKILL_LINK -> $SKILL_SRC"
  fi
done
```

**Why symlink instead of copy?** During development, symlinks keep the skill in sync with the project. For final distribution, replace `ln -s` with `cp -r`.

### Step 2b: Fallback — Global/shared directory registration

If you prefer global registration (e.g., the project-level approach isn't suitable), install into the resolved real path of each target:

```bash
SKILL_SRC="/path/to/skill-dir"
SKILL_NAME=$(basename "$SKILL_SRC")

for TARGET_DIR in ~/.kiro/skills ~/.claude/skills; do
  [ -d "$TARGET_DIR" ] || continue

  # Resolve symlinks: if TARGET_DIR itself is a symlink, install into its target
  REAL_TARGET=$(readlink -f "$TARGET_DIR" 2>/dev/null || echo "$TARGET_DIR")

  DEST="$REAL_TARGET/$SKILL_NAME"

  if [ -L "$DEST" ]; then
    echo "Already registered (symlink): $DEST -> $(readlink "$DEST")"
    rm "$DEST"
    ln -s "$SKILL_SRC" "$DEST"
    echo "Updated symlink: $DEST -> $SKILL_SRC"
  elif [ -d "$DEST" ]; then
    echo "Already exists (copy/directory): $DEST — skipping (manual review needed)"
  else
    ln -sf "$SKILL_SRC" "$DEST"
    echo "Registered: $DEST -> $SKILL_SRC"
  fi
done
```

### Step 3: Verify Registration

```bash
# Check project-level links resolve correctly
ls -la .claude/skills/<skill-name>/
ls -la .kiro/skills/<skill-name>/

# In Claude Code / Kiro, the skill description from SKILL.md frontmatter should now appear
# in the available skills list for auto-triggering.
```

## Unregistering a Skill (project-level)

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

## One-Liner for Quick Project-Level Registration

```bash
SKILL_NAME="<skill-name>"; for D in .claude .kiro; do mkdir -p "$D/skills"; ln -sf ../../skills/$SKILL_NAME "$D/skills/$SKILL_NAME"; done && echo "Registered $SKILL_NAME in project-level skills directories"
```

## Notes

- The skill name must not contain spaces or special characters (use kebab-case or snake_case)
- The `SKILL.md` file must be at the root of the skill directory
- The frontmatter `name` and `description` fields determine how the skill appears in the AI assistant's skill picker
- Changes take effect immediately — no restart needed
- If a destination already exists, the script skips it to avoid overwriting manual installs
- If `~/.kiro/skills/` or `~/.claude/skills/` doesn't exist, that target is silently skipped
