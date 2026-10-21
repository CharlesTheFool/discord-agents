"""request_skill (0.14): a skill's instructions, as text, on demand.

Skills are local folders (core/skills.py); their files are already in the
workshop at /skills/<name>/, so loading one is just reading its guide.
Where the workshop is off, only text-only skills can be read (0.14.1).
"""

SKILL_REQUEST_TOOL = {
    "name": "request_skill",
    "description": (
        "Read the instructions for one of the skills in <available_skills>. "
        "Its scripts and templates are in /skills/<name>/ in your workshop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "A name from <available_skills>"},
        },
        "required": ["skill_name"],
    },
}

# Where the workshop is off there are no files to point at
SKILL_READ_TOOL = {**SKILL_REQUEST_TOOL,
                   "description": "Read the instructions for one of the skills in <available_skills>."}


def run_request_skill(catalog, tool_input: dict, workshop: bool = True) -> str:
    name = (tool_input.get("skill_name") or "").strip()
    skills = catalog.available(workshop) if catalog else {}
    skill = skills.get(name)
    if skill is None:
        known = ", ".join(skills)
        return f"There's no skill named {name!r}." + (f" Skills here: {known}." if known else "")
    if not workshop:
        return catalog.instructions(name)
    return f"{catalog.instructions(name)}\n\n(Files for this skill: /skills/{skill.rel}/ in your workshop.)"
