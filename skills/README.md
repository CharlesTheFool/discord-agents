# Skills

A skill is a folder here with a `SKILL.md`: a guide for one kind of work,
plus any scripts, templates or references it needs. Where a bot's workshop
runs, its system prompt lists every skill by name and description, and the
bot reads a guide with `request_skill` when one fits what it's doing. The
whole folder is copied into each workshop at `/skills/<name>/`, so its
scripts run there and its references can be opened there. Nothing is
uploaded anywhere.

## Layout

```
skills/
  my-skill/
    SKILL.md          # required
    scripts/          # optional: anything the guide points at
    references/
```

`SKILL.md` starts with YAML frontmatter:

```markdown
---
name: my-skill
description: One or two sentences on what it's for and when to reach for it.
---

# My skill

The instructions the bot reads when it asks for this skill.
```

Keep the description short: every skill's description rides in the cached
system prompt of every conversation where the workshop runs. The body is
returned only on request, and long material belongs in `references/`, which
the body can point at by its workshop path (`/skills/my-skill/references/...`).

A skill that is only a guide, needing no scripts and no shell, can say so:

```yaml
metadata:
  text-only: true
```

Text-only skills are offered everywhere, including channels and DMs where
the workshop is off; everything else is offered only where the workshop
runs. Unmarked skills count as needing it, since most skills (Anthropic's
included) carry scripts.

A zip someone sends you needs unpacking into a folder first. If unzipping
adds a wrapper folder (`skills/my-skill/my-skill/SKILL.md`), that one level
is tolerated.

## What ships

| Skill | For |
| --- | --- |
| `docx` | Word documents with python-docx |
| `pptx` | PowerPoint decks with python-pptx |
| `xlsx` | Spreadsheets with openpyxl and pandas |
| `pdf` | Reading and reshaping PDFs with pypdf; making them with reportlab |
| `storytelling` | Planning and writing fiction with people, one author or a whole channel (text-only) |
| `fiction-criticism` | Notes on a draft, reviews, and a cold read of the bot's own fiction (text-only) |

The document guides use only what the workshop image ships, and each ends
the same way: save into `/workspace`, attach with `send_message`, and copy
it onto the repository if the server should keep it.

## Preloading and reloading

`skills.default_skills` in a bot's config lists skills whose instructions
are preloaded into the system prompt instead of fetched on request. Leave it
empty unless a bot uses a skill in most conversations.

Adding, editing or removing a skill takes effect on the next request; no
restart is needed. A workshop picks up the new files the next time the bot
uses it.

## Trust

A skill is instructions the bot follows and code it runs. Install skills
only from sources you trust, and read them first.
