"""Hybrid token counter + structured prompt with priority-based section trimming.

Provides:
- ``TokenCounter`` — tiktoken (exact) or len/4 (estimate) fallback.
- ``TokenCountResult`` — audit-friendly token budget breakdown.
- ``PromptSection`` — titled content block with a trim priority.
- ``StructuredPrompt`` — ordered collection of sections that can be
  trimmed to fit a token budget while protecting priority-1 sections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TokenCounter
# ---------------------------------------------------------------------------


class TokenCounter:
    """Hybrid token counter: tiktoken (exact) or len/4 (estimate)."""

    def __init__(self) -> None:
        self._tiktoken_available: bool = False
        try:
            import tiktoken as _tiktoken  # noqa: F401

            self._tiktoken_available = True
            self._tiktoken_mod = _tiktoken
        except ImportError:
            pass

    # -- public API ----------------------------------------------------------

    def count(self, text: str, model: str = "") -> int:
        """Count tokens. Uses tiktoken if available, else len/4."""
        if not text:
            return 0
        if self._tiktoken_available:
            import tiktoken

            try:
                enc = (
                    tiktoken.encoding_for_model(model)
                    if model
                    else tiktoken.get_encoding("cl100k_base")
                )
            except (KeyError, ValueError):
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# TokenCountResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenCountResult:
    """Audit-friendly breakdown of token usage for a single LLM call."""

    total: int
    system_prompt: int
    user_message: int
    model: str
    max_tokens: int
    utilization: float  # 0.0 ~ 1.0 (total / max_tokens)


# ---------------------------------------------------------------------------
# PromptSection
# ---------------------------------------------------------------------------


@dataclass
class PromptSection:
    """A titled content block with a trim priority.

    Priority levels:
        1 = Core (never trim) — role definition, task description
        2 = High — output format requirements
        3 = Medium-high — expert info
        5 = Medium — intermediate results
        7 = Low — dynamic context (P3 Providers)
        9 = Lowest / trimmable — examples, extra docs
    """

    title: str
    content: str
    priority: int  # 1=highest (never trim) ~ 9=lowest (trim first)

    @property
    def token_count(self) -> int:
        """Estimate token count for this section (len/4 heuristic)."""
        text = f"{self.title}\n{self.content}"
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# StructuredPrompt
# ---------------------------------------------------------------------------


class StructuredPrompt:
    """Ordered collection of PromptSections with priority-based trimming."""

    def __init__(self) -> None:
        self.sections: list[PromptSection] = []

    # -- builders -----------------------------------------------------------

    def add(self, title: str, content: str, priority: int = 5) -> None:
        """Add a section. Skips empty content."""
        if not content or not content.strip():
            return
        self.sections.append(PromptSection(title=title, content=content, priority=priority))

    def add_from_providers(self, providers: dict[str, Any], priority: int = 7) -> None:
        """Add sections from Context Providers (P3 integration point).

        Each provider is expected to have ``title`` (str) and
        ``get_context()`` -> str attributes.
        """
        for _name, provider in providers.items():
            content = provider.get_context()
            if content:
                self.add(provider.title, content, priority=priority)

    # -- rendering ----------------------------------------------------------

    def render(self) -> str:
        """Render all sections into a single string."""
        parts = [f"## {s.title}\n{s.content}" for s in self.sections]
        return "\n\n".join(parts)

    # -- token budget -------------------------------------------------------

    def total_tokens(self, counter: TokenCounter) -> int:
        """Calculate total token count across all sections."""
        return sum(counter.count(f"{s.title}\n{s.content}") for s in self.sections)

    def trim_to(self, max_tokens: int, counter: TokenCounter) -> None:
        """Remove lowest-priority sections until total tokens <= *max_tokens*.

        Priority-1 sections are **never** removed.
        This is destructive — it mutates ``self.sections``.
        """
        indexed: list[tuple[int, PromptSection]] = list(enumerate(self.sections))
        total = sum(counter.count(f"{s.title}\n{s.content}") for _, s in indexed)

        while total > max_tokens:
            # Find sections eligible for removal (priority > 1).
            removable = [(i, s) for i, s in indexed if s.priority > 1]
            if not removable:
                break  # Everything left is priority 1 — stop.

            # Pick the one with the highest priority number (lowest importance).
            target = max(removable, key=lambda x: x[1].priority)
            total -= counter.count(f"{target[1].title}\n{target[1].content}")
            indexed.remove(target)

        self.sections = [s for _, s in sorted(indexed, key=lambda x: x[0])]
