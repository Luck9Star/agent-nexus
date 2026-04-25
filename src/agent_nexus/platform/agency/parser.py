"""Markdown frontmatter parser for agency-agents vendor files."""

import yaml


def parse_frontmatter(md_content: str) -> dict:
    """Parse YAML frontmatter from a Markdown file.

    Expects content with ``---`` delimiters at the top:
        ---
        name: ...
        description: ...
        ---
        # Body content here

    Returns a dict with keys: name, description, color, emoji, vibe, body.
    Raises ValueError if no valid frontmatter is found.
    """
    stripped = md_content.strip()

    if not stripped.startswith("---"):
        raise ValueError("No frontmatter delimiters found: content must start with '---'")

    # Find the closing ---
    # Skip the opening --- (first line)
    first_newline = stripped.find("\n")
    if first_newline == -1:
        raise ValueError("No frontmatter delimiters found: single '---' without newline")

    rest = stripped[first_newline + 1:]

    # Find the closing ---
    closing_index = rest.find("\n---")
    if closing_index == -1:
        raise ValueError("No closing frontmatter delimiter found")

    yaml_block = rest[:closing_index]
    body = rest[closing_index + 4:].strip()  # skip past "\n---"

    if not yaml_block.strip():
        raise ValueError("Empty frontmatter block")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in frontmatter: {exc}") from exc

    if not isinstance(meta, dict):
        raise ValueError("Frontmatter must be a YAML mapping")

    return {
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "color": meta.get("color", ""),
        "emoji": meta.get("emoji", ""),
        "vibe": meta.get("vibe", ""),
        "body": body,
    }
