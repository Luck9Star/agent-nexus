"""Markdown frontmatter parser for agency-agents vendor files."""

import re

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

    # Find the closing --- by counting matches: only the first standalone
    # delimiter line after the opening one closes the frontmatter.  Using
    # re.search with MULTILINE could prematurely match a horizontal rule
    # in the body, so we split on delimiter lines and take the first split.
    delimiter_re = re.compile(r"^---[ \t]*$", re.MULTILINE)
    parts = re.split(delimiter_re, rest, maxsplit=1)
    if len(parts) < 2:
        raise ValueError("No closing frontmatter delimiter found")

    yaml_block = parts[0]
    body = parts[1].strip()

    if not yaml_block.strip():
        raise ValueError("Empty frontmatter block")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in frontmatter: {exc}") from exc

    if not isinstance(meta, dict):
        raise ValueError("Frontmatter must be a YAML mapping")

    name = meta.get("name", "")
    if not name or not name.strip():
        raise ValueError(
            "Frontmatter 'name' field is empty or missing — "
            "vendor file is malformed: every agent must have a non-empty name"
        )

    return {
        "name": name,
        "description": meta.get("description", ""),
        "color": meta.get("color", ""),
        "emoji": meta.get("emoji", ""),
        "vibe": meta.get("vibe", ""),
        "body": body,
    }
