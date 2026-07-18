---
name: register-skill
description: Register a skill directory to both Kiro and Claude skill locations. Use when a new skill has been created and needs to be installed globally so it's available in both Kiro workspaces and Claude Code sessions. Automatically detects the source skill directory and creates symlinks in the shared skills location.
---

## Overview

This skill registers newly created skills so they are available globally in both **Kiro** (workspace-scoped and global) and **Claude Code** sessions. It works by placing the skill in the shared `~/.agents/skills/` directory, which is symlinked by both `~/.kiro/skills/` and `~/.claude/skills/`.

## How It Works

The registration uses a single shared directory:

```
~/.agents/skills/          ← source of truth (symlinked by both)
  ├── android-cli          ← already registered
  ├── find-skills          ← already registered
  └── <new-skill>          ← newly registered here
        ↑                    ↑
  ~/.kiro/skills/  ←→  ~/.claude/skills/
```

Both `~/.kiro/skills/` and `~/.claude/skills/` are **symlinks** pointing to `~/.agents/skills/`. Any skill placed in `~/.agents/skills/` is automatically available in both Kiro and Claude Code.

## Registration Steps

### Step 1: Identify the Skill Directory

The skill directory should contain a `SKILL.md` file at its root. Find it:

```bash
# If you just created a skill in the project:
find /path/to/skill-dir -name SKILL.md
```

### Step 2: Register the Skill

Copy or symlink the skill into the shared skills directory:

```bash
# Option A: Copy (skill files are now part of global config)
cp -r /path/to/skill-dir ~/.agents/skills/<skill-name>

# Option B: Symlink (keeps skill in original location, good for development)
ln -s /path/to/skill-dir ~/.agents/skills/<skill-name>
```

**Recommendation**: Use **symlink** during development so changes to the skill are reflected immediately. Use **copy** for final, stable skills.

### Step 3: Verify Registration

```bash
# Check the skill appears in the shared directory
ls -la ~/.agents/skills/<skill-name>/

# Verify symlinks resolve correctly
ls -la ~/.kiro/skills/<skill-name>/
ls -la ~/.claude/skills/<skill-name>/
```

### Step 4: Confirm Availability

- **Kiro**: Open any workspace — the skill should appear in the skills list
- **Claude Code**: The skill description (from `SKILL.md` frontmatter `description` field) should be available for auto-triggering

## Automation Script

For convenience, here's a one-liner to register a skill:

```bash
SKILL_SRC="/path/to/skill-dir"
SKILL_NAME=$(basename "$SKILL_SRC")
mkdir -p ~/.agents/skills
ln -sf "$SKILL_SRC" ~/.agents/skills/"$SKILL_NAME"
echo "Registered '$SKILL_NAME' → ~/.agents/skills/$SKILL_NAME"
echo "Available in: ~/.kiro/skills/$SKILL_NAME  |  ~/.claude/skills/$SKILL_NAME"
```

## Unregistering a Skill

```bash
rm ~/.agents/skills/<skill-name>
# If using copy (not symlink):
# rm -rf ~/.agents/skills/<skill-name>
```

## Notes

- The skill name must not contain spaces or special characters (use kebab-case or snake_case)
- The `SKILL.md` file must be at the root of the skill directory
- The frontmatter `name` and `description` fields in `SKILL.md` determine how the skill appears in the AI assistant's skill picker
- Changes to the skill directory (whether copied or symlinked) take effect immediately — no restart needed
- If `~/.agents/skills/` doesn't exist, create it: `mkdir -p ~/.agents/skills`
