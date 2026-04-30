"""Prompt loader — loads external prompt templates from agents/agency-prompts/.

Uses :class:`string.Template` (``$variable`` syntax) so JSON ``{}`` braces
in prompt text don't need escaping.
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "agents" / "agency-prompts"
)

_cache: dict[str, Template] = {}


def _resolve_dir() -> Path:
    """Return the prompts directory, falling back gracefully."""
    if _PROMPTS_DIR.is_dir():
        return _PROMPTS_DIR
    # Try relative to CWD (for editable installs where package lives elsewhere)
    cwd_based = Path.cwd() / "agents" / "agency-prompts"
    if cwd_based.is_dir():
        return cwd_based
    logger.warning(
        "prompt_loader: prompts directory not found at %s or %s", _PROMPTS_DIR, cwd_based
    )
    return _PROMPTS_DIR


def load(name: str) -> Template:
    """Load a prompt template by name (e.g. ``"planner"`` → ``planner.md``).

    Results are cached in-process.
    """
    if name not in _cache:
        path = _resolve_dir() / f"{name}.md"
        _cache[name] = Template(path.read_text(encoding="utf-8"))
        logger.debug("prompt_loader: loaded %s from %s", name, path)
    return _cache[name]


def render(name: str, **kwargs: str) -> str:
    """Load a template and substitute variables in one call."""
    return load(name).substitute(kwargs)
