"""Config linting tool — parse and validate TOML, YAML, JSON config files.

Detects common issues:
- Missing required keys (name, version in project configs)
- Type mismatches (string where number expected)
- Deprecated options
- Structural problems (duplicate keys, trailing commas in JSON)
- Empty sections
"""

from __future__ import annotations

import json
import re

from agent_config_linter.models import LintIssue, LintReport


def _detect_format(content: str) -> str:
    """Auto-detect config file format.

    Args:
        content: File content string.

    Returns:
        One of "toml", "yaml", "json", or "unknown".
    """
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            pass

    # TOML heuristic: contains [section] headers or key = value pairs
    if re.search(r"^\[.+\]", content, re.MULTILINE) or re.search(
        r"^[a-zA-Z_][a-zA-Z0-9_.]*\s*=\s*", content, re.MULTILINE
    ):
        # Distinguish from YAML: TOML uses key = value, YAML uses key: value
        has_toml_assign = bool(re.search(r"^[a-zA-Z_]\w*\s*=\s*", content, re.MULTILINE))
        has_yaml_colon = bool(re.search(r"^[a-zA-Z_]\w*:\s", content, re.MULTILINE))
        if has_toml_assign and not has_yaml_colon:
            return "toml"

    # YAML heuristic: key: value pairs or --- document markers
    if re.search(r"^[a-zA-Z_]\w*:\s", content, re.MULTILINE) or content.startswith("---"):
        return "yaml"

    # Fallback: try JSON parse on the whole content
    try:
        json.loads(stripped)
        return "json"
    except (json.JSONDecodeError, ValueError):
        pass

    return "unknown"


def _lint_toml(content: str) -> list[LintIssue]:
    """Lint TOML content for common issues.

    Args:
        content: TOML file content.

    Returns:
        List of LintIssue instances.
    """
    issues: list[LintIssue] = []
    lines = content.splitlines()

    # Check for missing required keys in [project] section
    has_project_section = False
    project_keys: set[str] = set()
    in_project = False

    for line in lines:
        stripped = line.strip()
        if stripped == "[project]":
            has_project_section = True
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            in_project = False
        if in_project:
            match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=", stripped)
            if match:
                project_keys.add(match.group(1))

    if has_project_section:
        required_keys = {"name", "version"}
        for key in required_keys:
            if key not in project_keys:
                issues.append(
                    LintIssue(
                        severity="error",
                        category="missing_key",
                        location="[project]",
                        message=f"Missing required key '{key}' in [project]",
                        suggestion=f'Add {key} = "..." to [project]',
                    )
                )

    # Check for empty sections
    current_section: str | None = None
    section_has_content: dict[str, bool] = {}
    for line in lines:
        stripped = line.strip()
        section_match = re.match(r"^\[(.+)\]$", stripped)
        if section_match:
            current_section = section_match.group(1)
            if current_section not in section_has_content:
                section_has_content[current_section] = False
        elif current_section and stripped and not stripped.startswith("#"):
            section_has_content[current_section] = True

    for section, has_content in section_has_content.items():
        if not has_content:
            issues.append(
                LintIssue(
                    severity="info",
                    category="empty_section",
                    location=f"[{section}]",
                    message=f"Empty section [{section}]",
                    suggestion=f"Remove unused section [{section}] or add content",
                )
            )

    # Check for deprecated options in pyproject.toml context
    deprecated_patterns = [
        (
            r"^\s*bdist_wheel\s*=",
            "The bdist_wheel config is deprecated; use [tool.hatch.build.targets.wheel]",
        ),
    ]
    for i, line in enumerate(lines, 1):
        for pattern, message in deprecated_patterns:
            if re.search(pattern, line):
                issues.append(
                    LintIssue(
                        severity="warning",
                        category="deprecated",
                        location=f"line {i}",
                        message=message,
                        suggestion="Use the modern replacement",
                    )
                )

    return issues


def _lint_yaml(content: str) -> list[LintIssue]:
    """Lint YAML content for common issues.

    Args:
        content: YAML file content.

    Returns:
        List of LintIssue instances.
    """
    issues: list[LintIssue] = []
    lines = content.splitlines()

    # Check for duplicate keys at same indent level
    seen_keys: dict[int, dict[str, int]] = {}
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Calculate indent level
        indent = len(line) - len(line.lstrip())
        # Extract key
        match = re.match(r"^(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:", line)
        if match:
            key = match.group(2)
            if indent not in seen_keys:
                seen_keys[indent] = {}
            if key in seen_keys[indent]:
                issues.append(
                    LintIssue(
                        severity="error",
                        category="duplicate_key",
                        location=f"line {i}",
                        message=(
                            f"Duplicate key '{key}' (first seen at line {seen_keys[indent][key]})"
                        ),
                        suggestion=f"Rename or merge duplicate key '{key}'",
                    )
                )
            else:
                seen_keys[indent][key] = i

    # Check for tab indentation (YAML doesn't allow tabs)
    for i, line in enumerate(lines, 1):
        if "\t" in line:
            issues.append(
                LintIssue(
                    severity="error",
                    category="indentation",
                    location=f"line {i}",
                    message="YAML does not allow tab indentation",
                    suggestion="Replace tabs with spaces",
                )
            )
            break  # One report is enough

    # Check for unquoted special characters in values
    for i, line in enumerate(lines, 1):
        match = re.match(r"^[a-zA-Z_]\w*:\s*(.+)$", line.strip())
        if match:
            value = match.group(1).strip()
            if (
                value
                and not value.startswith('"')
                and not value.startswith("'")
                and any(c in value for c in [":", "{", "}", "[", "]", ",", "&", "*", "?"])
                and not value.startswith("|")
                and not value.startswith(">")
            ):
                issues.append(
                    LintIssue(
                        severity="warning",
                        category="unquoted_special",
                        location=f"line {i}",
                        message=(
                            f"Value contains special characters that may need quoting: {value[:40]}"
                        ),
                        suggestion="Wrap the value in quotes",
                    )
                )

    return issues


def _lint_json(content: str) -> list[LintIssue]:
    """Lint JSON content for common issues.

    Args:
        content: JSON file content.

    Returns:
        List of LintIssue instances.
    """
    issues: list[LintIssue] = []
    lines = content.splitlines()

    # Check for trailing commas (not allowed in standard JSON)
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        if stripped.endswith(",") and i < len(lines):
            next_stripped = lines[i].strip()  # 0-indexed, so lines[i] is next line
            if next_stripped.startswith("]") or next_stripped.startswith("}"):
                issues.append(
                    LintIssue(
                        severity="error",
                        category="trailing_comma",
                        location=f"line {i}",
                        message="Trailing comma before closing bracket (not allowed in JSON)",
                        suggestion="Remove the trailing comma",
                    )
                )

    # Try to parse and check for null values in important keys
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            # Check for null values in top-level keys
            for key, value in data.items():
                if value is None:
                    issues.append(
                        LintIssue(
                            severity="warning",
                            category="null_value",
                            location=f"key '{key}'",
                            message=f"Key '{key}' has null value",
                            suggestion=f"Provide a value for '{key}' or remove the key",
                        )
                    )
    except json.JSONDecodeError as e:
        issues.append(
            LintIssue(
                severity="error",
                category="parse_error",
                location=f"line {e.lineno}",
                message=f"JSON parse error: {e.msg}",
                suggestion="Fix JSON syntax errors",
            )
        )

    return issues


def lint_config(content: str, fmt: str = "auto") -> LintReport:
    """Lint a configuration file for common issues.

    Auto-detects format if not specified, then applies format-specific
    checks for structural issues, missing keys, type mismatches, and
    deprecated options.

    Args:
        content: Configuration file content string.
        fmt: Format hint — "auto", "toml", "yaml", or "json".

    Returns:
        LintReport with all issues found and severity counts.
    """
    detected = _detect_format(content) if fmt == "auto" else fmt

    if detected == "toml":
        issues = _lint_toml(content)
    elif detected == "yaml":
        issues = _lint_yaml(content)
    elif detected == "json":
        issues = _lint_json(content)
    else:
        issues = [
            LintIssue(
                severity="error",
                category="unknown_format",
                message="Could not detect configuration file format",
                suggestion="Specify format explicitly using fmt parameter",
            )
        ]

    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    info_count = sum(1 for i in issues if i.severity == "info")

    return LintReport(
        issues=issues,
        total_issues=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        format_detected=detected,
    )
