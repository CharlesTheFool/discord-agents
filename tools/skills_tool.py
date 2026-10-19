"""request_skill (0.14): a skill's instructions, as text, on demand.

Skills are local folders (core/skills.py); their files are already in the
workshop at /skills/<name>/, so loading one is just reading its guide.
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


def run_request_skill(catalog, tool_input: dict) -> str:
    name = (tool_input.get("skill_name") or "").strip()
    skill = catalog.skills().get(name) if catalog else None
    if skill is None:
        known = ", ".join(catalog.skills()) if catalog else ""
        return f"There's no skill named {name!r}." + (f" Skills here: {known}." if known else "")
    return f"{catalog.instructions(name)}\n\n(Files for this skill: /skills/{skill.rel}/ in your workshop.)"
