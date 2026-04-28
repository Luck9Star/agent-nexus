"""Content policy validator for agency-agents Markdown bodies.

NOTE: This is a first-pass heuristic. Patterns are English-only and case-insensitive
via ``.lower()``. Obfuscated prompts using Unicode, non-English text, or encoding tricks
will bypass detection. Production use needs LLM-based content scanning or Unicode
normalization (NFKC) as a pre-processing step. See review finding F2.
"""

import re
import unicodedata
from typing import Any

# Common Unicode confusables that NFKC does NOT normalize.
# Maps visually-similar characters from other scripts to their ASCII equivalent.
# This is NOT comprehensive — a full confusable database has 10k+ entries.
# It covers the most common attack vectors for prompt injection obfuscation.
_CONFUSABLE_MAP: dict[int, str] = str.maketrans({
    # Cyrillic → Latin
    0x0430: "a", 0x0435: "e", 0x043E: "o", 0x0440: "p", 0x0441: "c",
    0x0443: "y", 0x0445: "x", 0x0456: "i", 0x0458: "j",
    0x0410: "A", 0x0412: "B", 0x0415: "E", 0x041A: "K", 0x041C: "M",
    0x041D: "H", 0x041E: "O", 0x0420: "P", 0x0421: "C", 0x0422: "T",
    0x0425: "X",
    # Greek → Latin
    0x03B1: "a", 0x03B9: "i", 0x03BF: "o", 0x03C1: "p", 0x03C5: "y",
    # Fullwidth digits
    0xFF10: "0", 0xFF11: "1", 0xFF12: "2", 0xFF13: "3", 0xFF14: "4",
    0xFF15: "5", 0xFF16: "6", 0xFF17: "7", 0xFF18: "8", 0xFF19: "9",
})


def _normalize_confusables(text: str) -> str:
    """Apply NFKC normalization plus confusable character mapping."""
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.translate(_CONFUSABLE_MAP)


# Pattern categories and their severity levels
_HIGH_SEVERITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+previous\s+instructions"), "prompt injection: ignore previous instructions"),
    (re.compile(r"bypass\s+security"), "prompt injection: bypass security"),
    (re.compile(r"execute\s+shell"), "prompt injection: execute shell command"),
    (re.compile(r"system\s+prompt"), "prompt injection: system prompt reference"),
    (re.compile(r"reveal\s+your\s+instructions"), "prompt injection: reveal instructions"),
    (re.compile(r"forget\s+your\s+role"), "prompt injection: forget role"),
]

_MEDIUM_SEVERITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwrite\s+to\s+file\b"), "tool access: write file request"),
    (re.compile(r"\bexecute\s+command\b"), "tool access: execute command request"),
    (re.compile(r"\baccess\s+environment\b"), "tool access: environment variable access"),
    (re.compile(r"\brun\s+command\b"), "tool access: run command request"),
]

# Chinese-language prompt injection patterns
# Instruction-like prefixes that indicate a command/injection context.
# CN patterns are only flagged when at least one of these appears within
# 50 characters *before* the match.
_CN_INSTRUCTION_PREFIXES: list[str] = [
    "system",
    "你是一个",
    "ignore",
    "forget",
    "忽略",
    "绕过",
    "忘记",
    "执行",
    "假设你是",
    "现在你是",
]

_CN_HIGH_SEVERITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"忽略.{0,6}之前"), "prompt injection (CN): ignore previous instructions"),
    (re.compile(r"绕过.{0,4}安全"), "prompt injection (CN): bypass security"),
    (re.compile(r"(?:告诉|说出|泄露).{0,4}(?:系统)?提示词"), "prompt injection (CN): reveal system prompt"),
    (re.compile(r"忘记.{0,4}(?:角色|身份)"), "prompt injection (CN): forget role"),
]

_CN_MEDIUM_SEVERITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"执行.{0,4}shell"), "tool access (CN): execute shell command"),
]


def check_content_policy(md_body: str) -> dict[str, Any]:
    """Check a Markdown body for content policy violations.

    Returns a dict with:
      - ``passed`` (bool): True if no high or medium severity risks found
      - ``risks`` (list): list of risk dicts, each with keys:
        - ``pattern`` (str): the matched pattern description
        - ``severity`` (str): "high", "medium", or "low"
        - ``line`` (int): line number where the risk was found (in original text)
    """
    risks: list[dict[str, Any]] = []
    # NFKC normalization + confusable mapping collapses Unicode confusables
    # (e.g. fullwidth → ASCII, ligatures → component chars, Cyrillic homoglyphs
    # → Latin) to defeat obfuscation attempts.
    normalized = _normalize_confusables(md_body)

    # Build line-number mapping: normalized line → original line.
    # Normalization can change string length (e.g. ligatures → multiple chars)
    # but NFKC + translate preserves newline positions, so we can map by
    # splitting both texts and counting newlines.
    norm_lines = normalized.split("\n")
    orig_lines = md_body.split("\n")

    # If line counts match (common case), mapping is 1:1.
    # If they differ, build a char-offset-based mapping.
    if len(norm_lines) == len(orig_lines):
        line_map = dict(zip(range(1, len(norm_lines) + 1), range(1, len(orig_lines) + 1)))
    else:
        # Build mapping by tracking cumulative char offsets.
        # Each newline in both texts marks a line boundary.
        norm_offsets = [0]
        for i, ch in enumerate(normalized):
            if ch == "\n":
                norm_offsets.append(i + 1)
        orig_offsets = [0]
        for i, ch in enumerate(md_body):
            if ch == "\n":
                orig_offsets.append(i + 1)
        # Map by offset proximity: for each normalized line start,
        # find the original line whose start offset is closest.
        line_map: dict[int, int] = {}
        orig_idx = 0
        for norm_line_num, norm_off in enumerate(norm_offsets, start=1):
            while orig_idx + 1 < len(orig_offsets) and orig_offsets[orig_idx + 1] <= norm_off:
                orig_idx += 1
            line_map[norm_line_num] = orig_idx + 1  # 1-based

    for line_num, line in enumerate(norm_lines, start=1):
        line_lower = line.lower()

        # Check high severity patterns
        for pattern, description in _HIGH_SEVERITY_PATTERNS:
            if pattern.search(line_lower):
                risks.append({
                    "pattern": description,
                    "severity": "high",
                    "line": line_map.get(line_num, line_num),
                })

        # Check medium severity patterns (English)
        for pattern, description in _MEDIUM_SEVERITY_PATTERNS:
            if pattern.search(line_lower):
                risks.append({
                    "pattern": description,
                    "severity": "medium",
                    "line": line_map.get(line_num, line_num),
                })

        # Check Chinese high severity patterns.
        # These patterns are already specific (e.g., "忽略...之前" is not
        # normal phrasing), so the prefix check is a secondary confidence
        # boost rather than a hard gate.  A match is flagged when EITHER:
        #   a) the full line contains an instruction prefix, or
        #   b) the match starts at the very beginning of the line (no
        #      preceding context needed — a bare injection command).
        for pattern, description in _CN_HIGH_SEVERITY_PATTERNS:
            match = pattern.search(line)
            if match:
                has_prefix = any(
                    p in line for p in _CN_INSTRUCTION_PREFIXES
                )
                at_line_start = match.start() < 3
                if has_prefix or at_line_start:
                    risks.append({
                        "pattern": description,
                        "severity": "high",
                        "line": line_map.get(line_num, line_num),
                    })

        # Check Chinese medium severity patterns
        for pattern, description in _CN_MEDIUM_SEVERITY_PATTERNS:
            if pattern.search(line):
                risks.append({
                    "pattern": description,
                    "severity": "medium",
                    "line": line_map.get(line_num, line_num),
                })

    passed = not any(r["severity"] in ("high", "medium") for r in risks)

    return {
        "passed": passed,
        "risks": risks,
    }
