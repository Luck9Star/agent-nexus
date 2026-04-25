"""Load and parse Expert Profile YAML files, assemble system prompts."""

from pathlib import Path
from typing import Any

import yaml


def load_expert_profile(path: str) -> dict[str, Any]:
    """Load an Expert Profile YAML file and return its contents as a dict.

    Args:
        path: Filesystem path to the YAML profile.

    Returns:
        Parsed profile dictionary with all fields from the YAML file.

    Raises:
        FileNotFoundError: If the profile file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    profile_path = Path(path)
    if not profile_path.is_file():
        raise FileNotFoundError(f"Expert profile not found: {path}")

    with open(profile_path, encoding="utf-8") as f:
        profile: dict[str, Any] = yaml.safe_load(f)

    return profile


def assemble_prompt(profile: dict[str, Any]) -> str:
    """Build a system prompt string from an Expert Profile.

    The prompt includes the expert's name as a role header, followed by
    the vibe descriptor (if present) and the body content.

    Args:
        profile: Parsed Expert Profile dictionary.

    Returns:
        Assembled system prompt string.
    """
    parts: list[str] = []

    name = profile.get("name", "Expert")
    parts.append(f"You are {name}.")

    profile_section = profile.get("profile", {})
    vibe = profile_section.get("vibe", "")
    if vibe:
        parts.append(f"Tone and style: {vibe}.")

    body = profile_section.get("body", "")
    if body:
        parts.append(body)

    description = profile_section.get("description", "")
    if description and description not in body:
        parts.append(description)

    return "\n\n".join(parts)
