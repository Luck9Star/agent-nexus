"""Allowlist loader and validator for agency-agents imports."""

import re
from pathlib import Path

import yaml


def load_allowlist(path: str) -> dict:
    """Load the agency-agents allowlist YAML file.

    Returns a dict with:
      - ``source``: dict with ``repo`` and ``ref`` keys
      - ``agents``: list of agent entry dicts
    """
    filepath = Path(path)
    with filepath.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Allowlist file must contain a YAML mapping")

    # Validate top-level structure
    if "source" not in data:
        raise ValueError("Allowlist must have a 'source' key")
    if "agents" not in data or not isinstance(data["agents"], list):
        raise ValueError("Allowlist must have an 'agents' list")

    # Validate each agent entry
    seen_ids: set[str] = set()
    for i, entry in enumerate(data["agents"]):
        if not isinstance(entry, dict):
            raise ValueError(f"Agent entry #{i} must be a mapping, got {type(entry).__name__}")
        entry_errors = validate_allowlist_entry(entry)
        if entry_errors:
            errs = "; ".join(entry_errors)
            raise ValueError(f"Agent entry #{i} ({entry.get('id', 'unknown')}): {errs}")
        entry_id = entry.get("id", "")
        if entry_id in seen_ids:
            raise ValueError(f"Duplicate agent id '{entry_id}' in allowlist")
        seen_ids.add(entry_id)

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

    for field in ["source_path", "id", "capabilities", "output_contract"]:
        if field not in entry:
            errors.append(f"missing required field '{field}'")

    _validate_capabilities(entry, errors)
    _validate_id(entry, errors)
    _validate_output_contract(entry, errors)
    _validate_source_path(entry, errors)
    _validate_tools(entry, errors)

    return errors


def _validate_capabilities(entry: dict, errors: list[str]) -> None:
    if "capabilities" not in entry:
        return
    caps = entry["capabilities"]
    if not isinstance(caps, list):
        errors.append("'capabilities' must be a list")
    elif len(caps) == 0:
        errors.append("'capabilities' must be a non-empty list")
    elif not all(isinstance(c, str) for c in caps):
        errors.append("'capabilities' entries must all be strings")


def _validate_id(entry: dict, errors: list[str]) -> None:
    if "id" not in entry:
        return
    id_val = entry["id"]
    if not isinstance(id_val, str) or not id_val.startswith("agency."):
        errors.append("'id' must be a string starting with 'agency.'")


def _validate_output_contract(entry: dict, errors: list[str]) -> None:
    if "output_contract" not in entry:
        return
    oc = entry["output_contract"]
    if not isinstance(oc, str) or not oc.strip():
        errors.append("'output_contract' must be a non-empty string")


_SAFE_SOURCE_PATH = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_/-]*\.md$")


def _validate_source_path(entry: dict, errors: list[str]) -> None:
    if "source_path" not in entry:
        return
    sp = entry["source_path"]
    if not isinstance(sp, str) or not sp.endswith(".md"):
        errors.append("'source_path' must be a string ending with '.md'")
    elif not _SAFE_SOURCE_PATH.match(sp):
        errors.append(
            "'source_path' must contain only alphanumeric, underscore, "
            "hyphen, forward-slash characters (no '..', '~', '\\', or absolute paths)"
        )


def _validate_string_list(value: object, field_name: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"'{field_name}' must be a list")
    elif not all(isinstance(t, str) for t in value):
        errors.append(f"'{field_name}' must be a list of strings")


def _validate_tools(entry: dict, errors: list[str]) -> None:
    if "tools" not in entry:
        return
    tools = entry["tools"]
    if not isinstance(tools, dict):
        errors.append("'tools' must be a mapping")
        return

    allowed = tools.get("allowed")
    denied = tools.get("denied")

    if allowed is not None:
        _validate_string_list(allowed, "tools.allowed", errors)
    if denied is not None:
        _validate_string_list(denied, "tools.denied", errors)
    if isinstance(allowed, list) and isinstance(denied, list):
        overlap = set(allowed) & set(denied)
        if overlap:
            errors.append(f"tools cannot be in both allowed and denied: {sorted(overlap)}")
