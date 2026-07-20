---
name: register-skill
description: Register a skill directory to Kiro and/or Claude Code so it appears in the available skills list. Use when a new skill has been created and needs to be installed globally. Detects the actual setup (symlink-based or independent directories) and registers into the correct locations.
---

## Overview

This skill registers newly created skills so they are available as **top-level skills** in Kiro and/or Claude Code sessions. It detects the actual environment and installs into the right location(s).

## How It Works

There are two common setups. This script auto-detects which one you have:

### Setup A: Symlink-based shared directory

```
~/.agents/skills/          ← source of truth
  ├── android-cli
  ├── find-skills
  └── <new-skill>
        ↑
  ~/.kiro/skills/   →  ../../.agents/skills/   (symlink)
  ~/.claude/skills/ →  ../../.agents/skills/   (symlink)
```

In this case, placing a skill in `~/.agents/skills/` makes it visible everywhere.

### Setup B: Independent directories (most common)

```
~/.agents/skills/        ← may exist but is NOT linked by Kiro/Claude
~/.kiro/skills/          ← independent directory, Kiro reads from here
  ├── android-cli        ← copied or symlinked here
  └── <new-skill>

~/.claude/skills/        ← independent directory, Claude Code reads from here
  ├── android-cli        ← copied or symlinked here
  ├── kotlin-file-headers
  └── <new-skill>
```

In this case, the skill must be registered into **each** directory separately.

### Setup C: Mixed

Some entries in `~/.kiro/skills/` or `~/.claude/skills/` may be symlinks to `~/.agents/skills/`, while others are independent copies. The script handles both per-directory.

## Detection Logic

For each target directory (`~/.kiro/skills/` and `~/.claude/skills/`):

1. If the directory **itself** is a symlink → resolve it and install into the **target**.
2. If the directory is a real directory → install into the directory directly.

This means the same command works regardless of setup.

## Registration Steps

### Step 1: Identify the Skill Directory

The skill directory should contain a `SKILL.md` file at its root.

```bash
# From the project root:
ls /path/to/skill-dir/SKILL.md
```

### Step 2: Register the Skill

Run the registration script. It will print what it does for each target:

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
    # Update symlink if source changed
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

**Why symlink instead of copy?** During development, symlinks keep the skill in sync with the project. For final distribution, replace `ln -s` with `cp -r`.

### Step 3: Verify Registration

```bash
# Check each target resolved correctly
for DIR in ~/.kiro/skills ~/.claude/skills; do
  [ -d "$DIR" ] || continue
  echo "=== $DIR ($(readlink -f "$DIR")) ==="
  ls -la "$DIR"/<skill-name>/
done

# In Claude Code, the skill description from SKILL.md frontmatter should now appear
# in the available skills list for auto-triggering.
```

## Unregistering a Skill

```bash
for DIR in ~/.kiro/skills ~/.claude/skills; do
  [ -d "$DIR" ] || continue
  REAL_DIR=$(readlink -f "$DIR" 2>/dev/null || echo "$DIR")
  rm -f "$REAL_DIR/<skill-name>"
done
echo "Unregistered <skill-name> from all available skill directories."
```

## One-Liner for Quick Registration

```bash
SKILL_SRC="/path/to/skill-dir"; SKILL_NAME=$(basename "$SKILL_SRC"); for D in ~/.kiro/skills ~/.claude/skills; do R=$(readlink -f "$D" 2>/dev/null || echo "$D"); [ -d "$R" ] && ln -sf "$SKILL_SRC" "$R/$SKILL_NAME"; done; echo "Registered $SKILL_NAME"
```

## Notes

- The skill name must not contain spaces or special characters (use kebab-case or snake_case)
- The `SKILL.md` file must be at the root of the skill directory
- The frontmatter `name` and `description` fields determine how the skill appears in the AI assistant's skill picker
- Changes take effect immediately — no restart needed
- If a destination already exists, the script skips it to avoid overwriting manual installs
- If `~/.kiro/skills/` or `~/.claude/skills/` doesn't exist, that target is silently skipped
