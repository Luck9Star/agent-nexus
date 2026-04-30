"""Shared JSON parsing utilities for LLM output handling.

LLM responses may be wrapped in markdown fences, contain leading/trailing
text, or have other formatting artifacts.  This module provides robust
extraction logic used by LLMPlanner, LLMIntegrator, and LLMQualityGate.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def strip_markdown_fence(text: str) -> str:
    """Strip `````json ... ````` wrapper from LLM output, if present."""
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def robust_json_parse(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from LLM output with multiple fallback strategies.

    Strategy order:
      1. Direct ``json.loads`` after stripping markdown fences.
      2. Find first ``{`` and use ``raw_decode`` to locate the matching
         ``}`` — handles nested objects and strings correctly (unlike regex).
      3. Return ``None`` if no valid JSON object is found.
    """
    if not text or not text.strip():
        return None

    cleaned = strip_markdown_fence(text)

    # Strategy 1: direct parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: find first '{' and raw_decode from that position
    idx = cleaned.find("{")
    if idx != -1:
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(cleaned, idx)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None
