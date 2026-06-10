# Skills Directory

This directory is for **Skills** auto-discovery. Skills are modular instruction packages that teach Claude specialized tasks.

## Supported Formats

Skills can be added in two formats:

### 1. Folder-based (recommended)

Create a subdirectory containing a `SKILL.md` file:

```
skills/
  my-skill/
    SKILL.md          # Required
    template.md       # Optional supporting files
    examples/
      sample.md
```

### 2. Zip archive

Drop a `.zip` file containing a `SKILL.md`:

```
skills/
  my-skill.zip        # Contains SKILL.md + supporting files
```

## SKILL.md Format

Every skill needs a `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill-name
description: Brief description of what this skill does
---

# Skill Instructions

[Detailed instructions for Claude to follow when using this skill]
```

## How It Works

1. On startup, the bot scans this directory for `.zip` files and folders containing `SKILL.md`
2. Each skill is hashed (SHA256) for change detection
3. New or changed skills are uploaded to Anthropic's API
4. Results are cached in `.skills_cache.json` to prevent re-uploading unchanged skills
5. Folder-based skills are automatically zipped in memory before upload

## Default Skills

Skills committed to this directory in the repository ship as defaults with the framework. To add a default skill, create a folder here with a `SKILL.md` and commit it.

## Built-in Anthropic Skills

The following Anthropic skills are automatically available (no files needed):
- `xlsx`: Excel spreadsheet creation/editing
- `pptx`: PowerPoint presentation creation
- `docx`: Word document creation/editing
- `pdf`: PDF manipulation

## Custom Skills

You can create your own skills or download them from:
- [Anthropic Skills Repository](https://github.com/anthropics/skills)
- [Agent Skills Specification](https://agentskills.io/)

## Reloading Skills

Skills are scanned on bot startup. To pick up new skills, restart the bot.

## Notes

- Skills require the code execution tool to be enabled
- Cache is stored in `.skills_cache.json` in the project root
- Only install skills from trusted sources
