"""Unified agent manifest loader: TOML primary, YAML backward-compatible.

Reads ``agent.toml`` as the primary manifest format, falling back to
``agent-manifest.yaml`` for backward compatibility.  Also provides a
migration helper to convert existing YAML manifests to TOML.

Design spec: docs/roadmap/p1-3-marketplace.md Phase 1.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from agent_nexus.models.agent import AgentManifest

logger = logging.getLogger(__name__)

# File names to probe, in priority order.
# ``agent.toml`` is the preferred format; ``agent-manifest.yaml`` is legacy.
TOML_MANIFEST = "agent.toml"
YAML_MANIFEST = "agent-manifest.yaml"

_MANIFEST_FILES = (TOML_MANIFEST, YAML_MANIFEST)


class ManifestError(Exception):
    """Raised when a manifest file cannot be found or parsed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_manifest(agent_dir: Path) -> Path | None:
    """Return the path to the first manifest file found in *agent_dir*.

    Probes ``agent.toml`` first, then ``agent-manifest.yaml``.
    Returns ``None`` if neither exists.
    """
    for name in _MANIFEST_FILES:
        candidate = agent_dir / name
        if candidate.is_file():
            return candidate
    return None


def load_manifest(agent_dir: Path) -> AgentManifest:
    """Load and validate an agent manifest from *agent_dir*.

    Probes for ``agent.toml`` first (preferred), then falls back to
    ``agent-manifest.yaml`` (legacy).  Emits a :class:`DeprecationWarning`
    when reading YAML.

    Raises
    ------
    ManifestError
        No manifest file found, or the file could not be parsed.
    """
    manifest_path = find_manifest(agent_dir)
    if manifest_path is None:
        raise ManifestError(
            f"No manifest found in {agent_dir}. "
            f"Expected one of: {', '.join(_MANIFEST_FILES)}"
        )
    return load_manifest_from_file(manifest_path)


def load_manifest_from_file(path: Path) -> AgentManifest:
    """Load and validate an agent manifest from an explicit file path.

    Auto-detects format from the file extension:
    - ``.toml`` → TOML parser
    - ``.yaml`` / ``.yml`` → YAML parser (with deprecation warning)
    """
    raw = _read_raw_manifest(path)
    if raw is None:
        raise ManifestError(f"Empty or unparseable manifest: {path}")
    return _parse_manifest_dict(raw, path)


def load_manifest_dict(agent_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Load raw manifest dict + validation issues (no Pydantic parsing).

    Mirrors the ``(issues, manifest_data)`` contract used by
    :class:`GitInstaller._validate_agent_package`.

    Returns
    -------
    issues : list[str]
        Validation problems found (empty if valid).
    manifest_data : dict
        The raw parsed dict (may be empty on failure).
    """
    issues: list[str] = []
    manifest_path = find_manifest(agent_dir)

    if manifest_path is None:
        return (
            [f"No manifest found. Expected one of: {', '.join(_MANIFEST_FILES)}"],
            {},
        )

    try:
        raw = _read_raw_manifest(manifest_path)
    except Exception as exc:
        return ([f"{manifest_path.name} parse error: {exc}"], {})

    if raw is None:
        return ([f"Manifest file is empty or unparseable: {manifest_path.name}"], {})

    # Basic structural checks
    for field in ("name", "version", "type"):
        if field not in raw:
            issues.append(f"{manifest_path.name} missing required field: {field}")

    from agent_nexus.models.agent import AgentType

    if "type" in raw and raw["type"] not in {t.value for t in AgentType}:
        issues.append(f"Invalid agent type: {raw['type']}")

    return issues, raw


def migrate_yaml_to_toml(yaml_path: Path, toml_path: Path | None = None) -> Path:
    """Convert a YAML agent manifest to TOML format.

    Parameters
    ----------
    yaml_path : Path
        Path to the existing ``agent-manifest.yaml``.
    toml_path : Path | None
        Output path for ``agent.toml``.  Defaults to the same directory
        as *yaml_path*.

    Returns
    -------
    Path
        The path to the newly written TOML file.
    """
    if not yaml_path.is_file():
        raise ManifestError(f"YAML manifest not found: {yaml_path}")

    if toml_path is None:
        toml_path = yaml_path.parent / TOML_MANIFEST

    raw = _read_raw_manifest(yaml_path)
    if raw is None:
        raise ManifestError(f"Empty or unparseable YAML manifest: {yaml_path}")

    # Wrap top-level fields under [agent] section to match the unified format
    toml_data = {"agent": _flatten_manifest_for_toml(raw)}
    toml_content = _serialize_toml(toml_data)
    toml_path.write_text(toml_content, encoding="utf-8")
    logger.info("Migrated %s → %s", yaml_path, toml_path)
    return toml_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_raw_manifest(path: Path) -> dict[str, Any] | None:
    """Read a manifest file and return its raw dict content.

    Returns ``None`` if the file is empty or content is not a dict.
    """
    text = path.read_text(encoding="utf-8")

    if path.suffix == ".toml":
        try:
            data = tomllib.loads(text)
        except Exception as exc:
            raise ManifestError(f"TOML parse error in {path}: {exc}") from exc
    elif path.suffix in (".yaml", ".yml"):
        warnings.warn(
            "YAML agent manifests are deprecated. Please migrate to agent.toml.",
            DeprecationWarning,
            stacklevel=3,
        )
        try:
            data = yaml.safe_load(text)
        except Exception as exc:
            raise ManifestError(f"YAML parse error in {path}: {exc}") from exc
    else:
        raise ManifestError(f"Unsupported manifest format: {path.suffix}")

    if not isinstance(data, dict):
        return None
    normalized = _normalize_manifest_dict(data, path)
    # Treat empty dicts (e.g. from an empty TOML file) as unparseable
    if not normalized:
        return None
    return normalized


def _normalize_manifest_dict(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Normalize manifest dict depending on format.

    TOML manifests use a top-level ``[agent]`` section per the design spec.
    YAML manifests are flat.  This normalizes both to the flat dict expected
    by :class:`AgentManifest`.
    """
    if path.suffix == ".toml" and "agent" in data:
        # TOML format: unwrap [agent] section
        return data["agent"]
    return data


def _parse_manifest_dict(raw: dict[str, Any], path: Path) -> AgentManifest:
    """Parse a raw dict into an AgentManifest with a helpful error message."""
    try:
        return AgentManifest(**raw)
    except Exception as exc:
        raise ManifestError(f"Invalid manifest in {path}: {exc}") from exc


def _flatten_manifest_for_toml(data: dict[str, Any]) -> dict[str, Any]:
    """Prepare a flat YAML manifest dict for TOML serialization.

    Converts certain fields to TOML-friendly representations:
    - Lists become TOML arrays
    - Nested dicts are preserved as TOML tables
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        # Skip None values
        if value is None:
            continue
        result[key] = value
    return result


def _serialize_toml(data: dict[str, Any]) -> str:
    """Serialize a dict to TOML string.

    Uses ``tomli_w`` if available, otherwise falls back to a simple
    built-in serializer sufficient for the flat agent manifest structure.
    """
    try:
        import tomli_w

        return tomli_w.dumps(data)
    except ImportError:
        pass

    # Simple fallback serializer for the flat manifest structure
    return _simple_toml_serialize(data)


def _simple_toml_serialize(data: dict[str, Any], _prefix: str = "") -> str:
    """Minimal TOML serializer for agent manifest data.

    Handles: strings, ints, floats, bools, lists of scalars, and
    nested dicts (as TOML tables).  Sufficient for agent.toml output
    without requiring ``tomli_w`` as a dependency.
    """
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []

    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list):
            lines.append(f"{_toml_key(key)} = {_toml_array(value)}")
        elif isinstance(value, bool):
            lines.append(f"{_toml_key(key)} = {'true' if value else 'false'}")
        elif isinstance(value, int | float):
            lines.append(f"{_toml_key(key)} = {value}")
        elif isinstance(value, str):
            lines.append(f"{_toml_key(key)} = {_toml_string(value)}")
        # Skip None and unknown types

    result_parts: list[str] = []
    if lines:
        result_parts.append("\n".join(lines))

    for table_key, table_value in tables:
        if result_parts:
            result_parts.append("")
        result_parts.append(f"[{_prefix}{_toml_key(table_key)}]")
        result_parts.append(_simple_toml_serialize(table_value, f"{_prefix}{table_key}."))

    return "\n".join(result_parts)


def _toml_key(key: str) -> str:
    """Quote a TOML key if it contains special characters."""
    if key.isidentifier() and not key.startswith("_"):
        return key
    return f'"{key}"'


def _toml_string(value: str) -> str:
    """Serialize a string as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _toml_array(items: list[Any]) -> str:
    """Serialize a list as a TOML inline array."""
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(_toml_string(item))
        elif isinstance(item, bool):
            parts.append("true" if item else "false")
        elif isinstance(item, int | float):
            parts.append(str(item))
        else:
            parts.append(_toml_string(str(item)))
    return f"[{', '.join(parts)}]"
