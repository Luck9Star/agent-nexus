"""Allowlist loader and validator for agency-agents imports."""

from pathlib import Path

import yaml


def load_allowlist(path: str) -> dict:
    """Load the agency-agents allowlist YAML file.

    Returns a dict with:
      - ``source``: dict with ``repo`` and ``ref`` keys
      - ``agents``: list of agent entry dicts
    """
    filepath = Path(path)
    with filepath.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Allowlist file must contain a YAML mapping")

    # Validate top-level structure
    if "source" not in data:
        raise ValueError("Allowlist must have a 'source' key")
    if "agents" not in data or not isinstance(data["agents"], list):
        raise ValueError("Allowlist must have an 'agents' list")

    source = data["source"]
    if not isinstance(source, dict):
        raise ValueError("'source' must be a mapping")
    if "repo" not in source or "ref" not in source:
        raise ValueError("'source' must contain 'repo' and 'ref' keys")

    return data


def validate_allowlist_entry(entry: dict) -> list[str]:
    """Validate a single allowlist entry.

    Returns a list of validation error strings. An empty list means the entry is valid.
    """
    errors: list[str] = []

    required_fields = ["source_path", "id", "capabilities", "output_contract"]
    for field in required_fields:
        if field not in entry:
            errors.append(f"missing required field '{field}'")

    if "capabilities" in entry:
        caps = entry["capabilities"]
        if not isinstance(caps, list):
            errors.append("'capabilities' must be a list")
        elif len(caps) == 0:
            errors.append("'capabilities' must be a non-empty list")

    if "id" in entry:
        id_val = entry["id"]
        if not isinstance(id_val, str) or not id_val.startswith("agency."):
            errors.append("'id' must be a string starting with 'agency.'")

    if "source_path" in entry:
        sp = entry["source_path"]
        if not isinstance(sp, str) or not sp.endswith(".md"):
            errors.append("'source_path' must be a string ending with '.md'")

    return errors
