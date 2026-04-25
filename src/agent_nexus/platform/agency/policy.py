"""Content policy validator for agency-agents Markdown bodies.

NOTE: This is a first-pass heuristic. Patterns are English-only and case-insensitive
via ``.lower()``. Obfuscated prompts using Unicode, non-English text, or encoding tricks
will bypass detection. Production use needs LLM-based content scanning or Unicode
normalization (NFKC) as a pre-processing step. See review finding F2.
"""

import re
from typing import Any


# Pattern categories and their severity levels
_HIGH_SEVERITY_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+previous\s+instructions", "prompt injection: ignore previous instructions"),
    (r"bypass\s+security", "prompt injection: bypass security"),
    (r"execute\s+shell", "prompt injection: execute shell command"),
    (r"system\s+prompt", "prompt injection: system prompt reference"),
    (r"reveal\s+your\s+instructions", "prompt injection: reveal instructions"),
    (r"forget\s+your\s+role", "prompt injection: forget role"),
]

_MEDIUM_SEVERITY_PATTERNS: list[tuple[str, str]] = [
    (r"\bwrite\s+to\s+file\b", "tool access: write file request"),
    (r"\bexecute\s+command\b", "tool access: execute command request"),
    (r"\baccess\s+environment\b", "tool access: environment variable access"),
    (r"\brun\s+command\b", "tool access: run command request"),
]

# Chinese-language prompt injection patterns
_CN_HIGH_SEVERITY_PATTERNS: list[tuple[str, str]] = [
    (r"忽略.{0,6}之前", "prompt injection (CN): ignore previous instructions"),
    (r"绕过.{0,4}安全", "prompt injection (CN): bypass security"),
    (r"(?:告诉|说出|泄露).{0,4}(?:系统)?提示词", "prompt injection (CN): reveal system prompt"),
    (r"忘记.{0,4}(?:角色|身份)", "prompt injection (CN): forget role"),
]

_CN_MEDIUM_SEVERITY_PATTERNS: list[tuple[str, str]] = [
    (r"执行.{0,4}shell", "tool access (CN): execute shell command"),
]


def check_content_policy(md_body: str) -> dict[str, Any]:
    """Check a Markdown body for content policy violations.

    Returns a dict with:
      - ``passed`` (bool): True if no high or medium severity risks found
      - ``risks`` (list): list of risk dicts, each with keys:
        - ``pattern`` (str): the matched pattern description
        - ``severity`` (str): "high", "medium", or "low"
        - ``line`` (int): line number where the risk was found
    """
    risks: list[dict[str, Any]] = []
    lines = md_body.split("\n")

    for line_num, line in enumerate(lines, start=1):
        line_lower = line.lower()

        # Check high severity patterns
        for pattern, description in _HIGH_SEVERITY_PATTERNS:
            if re.search(pattern, line_lower):
                risks.append({
                    "pattern": description,
                    "severity": "high",
                    "line": line_num,
                })

        # Check medium severity patterns (English)
        for pattern, description in _MEDIUM_SEVERITY_PATTERNS:
            if re.search(pattern, line_lower):
                risks.append({
                    "pattern": description,
                    "severity": "medium",
                    "line": line_num,
                })

        # Check Chinese high severity patterns
        for pattern, description in _CN_HIGH_SEVERITY_PATTERNS:
            if re.search(pattern, line):
                risks.append({
                    "pattern": description,
                    "severity": "high",
                    "line": line_num,
                })

        # Check Chinese medium severity patterns
        for pattern, description in _CN_MEDIUM_SEVERITY_PATTERNS:
            if re.search(pattern, line):
                risks.append({
                    "pattern": description,
                    "severity": "medium",
                    "line": line_num,
                })

    passed = not any(r["severity"] in ("high", "medium") for r in risks)

    return {
        "passed": passed,
        "risks": risks,
    }
