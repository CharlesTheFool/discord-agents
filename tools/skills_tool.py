"""
Skills Tool - Progressive Skill Disclosure

Allows Claude to:
- See available skills via system prompt catalog
- Request specific skills to be loaded
- Replace currently loaded skills

This implements the progressive disclosure pattern:
1. Claude sees a catalog of ALL available skills in system prompt
2. Claude decides which skill(s) are needed for the current task
3. Claude calls request_skill to load the needed skill
4. Framework loads the skill for the next API call
"""

import logging
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.skills_manager import SkillsManager
    from core.conversation_state import ConversationState

logger = logging.getLogger(__name__)


# Tool definition for Claude API
SKILL_REQUEST_TOOL = {
    "name": "request_skill",
    "description": (
        "Load a specific skill for use with code_execution. "
        "Check the <available_skills> section in your instructions to see what skills are available. "
        "Only 1-2 skills can be active at once. If you need a different skill than what's currently "
        "loaded, use this tool to request it. The skill will be available on your next response."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Name of the skill to load (e.g., 'docx', 'xlsx', 'pdf', or a custom skill name)"
            },
            "replace": {
                "type": "string",
                "description": "Name of currently loaded skill to replace (optional - if not provided, oldest skill may be replaced)"
            }
        },
        "required": ["skill_name"]
    }
}


def get_skill_request_tool() -> Dict[str, Any]:
    """Get the skill request tool definition for API registration."""
    return SKILL_REQUEST_TOOL


class SkillRequestExecutor:
    """
    Executes skill request commands from Claude.

    Updates conversation state with new skill selections,
    which takes effect on the next API call.
    """

    def __init__(
        self,
        skills_manager: "SkillsManager",
        max_skills: int = 2
    ):
        """
        Initialize skill request executor.

        Args:
            skills_manager: SkillsManager instance for skill catalog
            max_skills: Maximum skills that can be active at once
        """
        self.skills_manager = skills_manager
        self.max_skills = max_skills
        logger.info(f"SkillRequestExecutor initialized (max_skills={max_skills})")

    def execute(
        self,
        tool_input: Dict[str, Any],
        conversation_state: "ConversationState"
    ) -> str:
        """
        Execute a skill request.

        Args:
            tool_input: Tool parameters (skill_name, replace)
            conversation_state: Current conversation state to update

        Returns:
            Result message indicating success or failure
        """
        skill_name = tool_input.get("skill_name", "").strip()
        replace_skill = tool_input.get("replace", "").strip() or None

        if not skill_name:
            return "Error: skill_name is required"

        # Get skill catalog to validate request
        catalog = self.skills_manager.get_skill_catalog()

        if skill_name not in catalog:
            available = list(catalog.keys())
            return f"Error: Unknown skill '{skill_name}'. Available skills: {', '.join(available)}"

        # Get current active skills
        current_skills = conversation_state.get_active_skills()

        # Check if skill is already loaded
        if skill_name in current_skills:
            return f"Skill '{skill_name}' is already loaded and ready to use."

        # Handle skill replacement
        if replace_skill:
            # User specified which skill to replace
            if replace_skill not in current_skills:
                return f"Error: Cannot replace '{replace_skill}' - it's not currently loaded. Current skills: {', '.join(current_skills)}"

            success = conversation_state.replace_active_skill(replace_skill, skill_name)
            if success:
                new_skills = conversation_state.get_active_skills()
                logger.info(f"Skill request: replaced '{replace_skill}' with '{skill_name}'. Active: {new_skills}")
                return f"Loaded skill '{skill_name}' (replaced '{replace_skill}'). Active skills: {', '.join(new_skills)}. The skill is now available for use."
            else:
                return f"Error: Failed to replace skill '{replace_skill}'"

        # Try to add skill (if under capacity)
        if conversation_state.add_active_skill(skill_name, self.max_skills):
            new_skills = conversation_state.get_active_skills()
            logger.info(f"Skill request: added '{skill_name}'. Active: {new_skills}")
            return f"Loaded skill '{skill_name}'. Active skills: {', '.join(new_skills)}. The skill is now available for use."

        # At capacity - need to replace one
        # Default: replace the oldest (first) skill
        oldest_skill = current_skills[0] if current_skills else None

        if oldest_skill:
            conversation_state.replace_active_skill(oldest_skill, skill_name)
            new_skills = conversation_state.get_active_skills()
            logger.info(f"Skill request: replaced oldest '{oldest_skill}' with '{skill_name}'. Active: {new_skills}")
            return (
                f"Loaded skill '{skill_name}' (replaced '{oldest_skill}' to stay within {self.max_skills}-skill limit). "
                f"Active skills: {', '.join(new_skills)}. The skill is now available for use."
            )

        # Fallback: just set the skill
        conversation_state.set_active_skills([skill_name], self.max_skills)
        new_skills = conversation_state.get_active_skills()
        logger.info(f"Skill request: set '{skill_name}'. Active: {new_skills}")
        return f"Loaded skill '{skill_name}'. Active skills: {', '.join(new_skills)}. The skill is now available for use."


def build_skills_catalog_prompt(
    skills_manager: "SkillsManager",
    active_skills: List[str]
) -> str:
    """
    Build the skills catalog section for system prompt.

    This enables progressive disclosure - Claude can see ALL available
    skills and knows which ones are currently loaded.

    Args:
        skills_manager: SkillsManager instance
        active_skills: List of currently active skill names

    Returns:
        Formatted XML section for system prompt
    """
    catalog = skills_manager.get_skill_catalog()

    if not catalog:
        return ""

    lines = [
        "<available_skills>",
        "Skills you can use with the code_execution tool. Use the request_skill tool to load a different skill.",
        ""
    ]

    # Group by type
    anthropic_skills = []
    custom_skills = []

    for name, info in catalog.items():
        status = " [LOADED]" if name in active_skills else ""
        entry = f"  - {name}: {info['description']}{status}"

        if info["type"] == "anthropic":
            anthropic_skills.append(entry)
        else:
            custom_skills.append(entry)

    if anthropic_skills:
        lines.append("BUILT-IN SKILLS:")
        lines.extend(anthropic_skills)
        lines.append("")

    if custom_skills:
        lines.append("CUSTOM SKILLS:")
        lines.extend(custom_skills)
        lines.append("")

    # Show current state
    if active_skills:
        lines.append(f"CURRENTLY LOADED: {', '.join(active_skills)}")
    else:
        lines.append("CURRENTLY LOADED: None (use request_skill to load a skill)")

    lines.append("</available_skills>")

    return "\n".join(lines)
