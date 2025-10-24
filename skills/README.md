# Skills Directory

This directory is for **Skills** auto-discovery. Skills are modular instruction packages that teach Claude specialized tasks.

## How to Use

1. Drop `.zip` skill files into this directory
2. The bot will automatically detect and upload them to Anthropic's API on startup
3. Skills are cached (hash-based) to prevent re-uploading unchanged files

## Skill Structure

A valid skill is a folder (or zip) containing:
- **SKILL.md** (required): Main instruction file with YAML frontmatter
- Optional: Python scripts, templates, examples, assets

## Built-in Skills

The following Anthropic skills are automatically available:
- `xlsx`: Excel spreadsheet creation/editing
- `pptx`: PowerPoint presentation creation
- `docx`: Word document creation/editing
- `pdf`: PDF manipulation

## Custom Skills

You can create your own skills or download them from:
- [Anthropic Skills Repository](https://github.com/anthropics/skills)
- [Claude MCP Marketplace](https://www.claudemcp.com/)

## Reloading Skills

If you add skills while the bot is running, you can reload them:
- Use a bot command (if implemented): `!reload_skills`
- Restart the bot

## Notes

- Skills require the code execution tool to be enabled
- Each skill file is hashed (SHA256) for change detection
- Cache is stored in `.skills_cache.json` in the project root
- Only install skills from trusted sources

## Example Skill

See `docs/reference/MCP_AND_SKILLS_INTEGRATION_ARCHITECTURE.md` for detailed documentation on creating custom skills.
