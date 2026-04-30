"""CLI output parser — JSON path extraction and text regex parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_nexus.platform.agency.cli_backend.types import BackendConfig, CLIResult


def extract_json_value(data: dict[str, Any], path: str) -> Any:
    """Extract a value from a nested dict using dot-separated path."""
    if not path:
        return None
    keys = path.split(".")
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def parse_json_output(stdout: str, config: BackendConfig) -> CLIResult:
    """Parse JSON-formatted CLI output using json_paths config."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return CLIResult(text=stdout, model="", raw_stdout=stdout, parse_error=True)

    paths = config.json_paths

    def _extract(path: str | None) -> Any:
        if path is None:
            return None
        return extract_json_value(data, path)

    text = _extract(paths.text)
    if text is None:
        text = stdout
    if not isinstance(text, str):
        text = str(text)

    session_id = _extract(paths.session_id)
    model = _extract(paths.model)
    input_tokens = _extract(paths.input_tokens)
    output_tokens = _extract(paths.output_tokens)

    return CLIResult(
        text=text,
        model=model if isinstance(model, str) else "",
        session_id=session_id if isinstance(session_id, str) else None,
        input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
        raw_stdout=stdout,
        parse_error=False,
    )


def parse_text_output(stdout: str, stderr: str, config: BackendConfig) -> CLIResult:
    """Parse text-mode CLI output, optionally extracting metadata via regex."""
    session_id = None
    model = None
    patterns = config.text_patterns
    if patterns.session_id:
        combined = f"{stdout}\n{stderr}"
        match = re.search(patterns.session_id, combined)
        if match:
            session_id = match.group(1)
    if patterns.model:
        match = re.search(patterns.model, stdout)
        if match:
            model = match.group(1)
    return CLIResult(
        text=stdout,
        model=model or "",
        session_id=session_id,
        raw_stdout=stdout,
        raw_stderr=stderr,
        parse_error=False,
    )
