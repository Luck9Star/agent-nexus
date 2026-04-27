"""Per-model capability data for LLM client configuration.

Provides ModelCapability (frozen dataclass) and ModelCapabilityRegistry
(lookup with fuzzy matching and provider-level fallback) so that the
LLM client can set proper max_tokens, temperature, and know about
vision/tool-use support without hardcoding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ModelCapability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCapability:
    """Immutable capability record for a single model."""

    model_id: str
    provider: str
    max_output_tokens: int
    context_window: int
    supports_vision: bool
    supports_tool_use: bool
    supports_temperature: bool
    temperature_min: float
    temperature_max: float
    knowledge_cutoff: str  # e.g. "2025-04"


# ---------------------------------------------------------------------------
# Built-in data
# ---------------------------------------------------------------------------

# Each entry maps a concrete model-id string to its capabilities.
_BUILTIN: dict[str, ModelCapability] = {
    # ---- Anthropic ---------------------------------------------------------
    "claude-sonnet-4-20250514": ModelCapability(
        model_id="claude-sonnet-4-20250514",
        provider="anthropic",
        max_output_tokens=8192,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-04",
    ),
    "claude-opus-4-20250116": ModelCapability(
        model_id="claude-opus-4-20250116",
        provider="anthropic",
        max_output_tokens=8192,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-01",
    ),
    "claude-3-5-sonnet-20241022": ModelCapability(
        model_id="claude-3-5-sonnet-20241022",
        provider="anthropic",
        max_output_tokens=8192,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2024-10",
    ),
    "claude-3-5-haiku-20241022": ModelCapability(
        model_id="claude-3-5-haiku-20241022",
        provider="anthropic",
        max_output_tokens=8192,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2024-10",
    ),
    "claude-3-opus-20240229": ModelCapability(
        model_id="claude-3-opus-20240229",
        provider="anthropic",
        max_output_tokens=4096,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2024-02",
    ),
    # ---- OpenAI ------------------------------------------------------------
    "gpt-4o": ModelCapability(
        model_id="gpt-4o",
        provider="openai",
        max_output_tokens=16384,
        context_window=128000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2024-10",
    ),
    "gpt-4o-mini": ModelCapability(
        model_id="gpt-4o-mini",
        provider="openai",
        max_output_tokens=16384,
        context_window=128000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2024-10",
    ),
    "gpt-4-turbo": ModelCapability(
        model_id="gpt-4-turbo",
        provider="openai",
        max_output_tokens=8192,
        context_window=128000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2024-04",
    ),
    "gpt-4": ModelCapability(
        model_id="gpt-4",
        provider="openai",
        max_output_tokens=8192,
        context_window=8192,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2023-09",
    ),
    "gpt-3.5-turbo": ModelCapability(
        model_id="gpt-3.5-turbo",
        provider="openai",
        max_output_tokens=4096,
        context_window=16384,
        supports_vision=False,
        supports_tool_use=False,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2023-09",
    ),
    # ---- DeepSeek ----------------------------------------------------------
    "deepseek-chat": ModelCapability(
        model_id="deepseek-chat",
        provider="deepseek",
        max_output_tokens=8192,
        context_window=128000,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-03",
    ),
    "deepseek-reasoner": ModelCapability(
        model_id="deepseek-reasoner",
        provider="deepseek",
        max_output_tokens=8192,
        context_window=128000,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-03",
    ),
    # ---- Qwen (阿里通义千问) ------------------------------------------------
    "qwen2.5-72b-instruct": ModelCapability(
        model_id="qwen2.5-72b-instruct",
        provider="qwen",
        max_output_tokens=8192,
        context_window=131072,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-01",
    ),
    "qwen2.5-32b-instruct": ModelCapability(
        model_id="qwen2.5-32b-instruct",
        provider="qwen",
        max_output_tokens=8192,
        context_window=131072,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-01",
    ),
    "qwen2.5-7b-instruct": ModelCapability(
        model_id="qwen2.5-7b-instruct",
        provider="qwen",
        max_output_tokens=8192,
        context_window=32768,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-01",
    ),
    # ---- MiniMax -----------------------------------------------------------
    "minimax-m1-0519": ModelCapability(
        model_id="minimax-m1-0519",
        provider="minimax",
        max_output_tokens=16384,
        context_window=1048576,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-05",
    ),
    "minimax-t1-0519": ModelCapability(
        model_id="minimax-t1-0519",
        provider="minimax",
        max_output_tokens=16384,
        context_window=1048576,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-05",
    ),
}

# Provider-level safe defaults used when no model-specific match is found.
PROVIDER_DEFAULTS: dict[str, ModelCapability] = {
    "anthropic": ModelCapability(
        model_id="__anthropic_default__",
        provider="anthropic",
        max_output_tokens=8192,
        context_window=200000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-01",
    ),
    "openai": ModelCapability(
        model_id="__openai_default__",
        provider="openai",
        max_output_tokens=16384,
        context_window=128000,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2024-10",
    ),
    "deepseek": ModelCapability(
        model_id="__deepseek_default__",
        provider="deepseek",
        max_output_tokens=8192,
        context_window=128000,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-03",
    ),
    "qwen": ModelCapability(
        model_id="__qwen_default__",
        provider="qwen",
        max_output_tokens=8192,
        context_window=131072,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2025-01",
    ),
    "minimax": ModelCapability(
        model_id="__minimax_default__",
        provider="minimax",
        max_output_tokens=16384,
        context_window=1048576,
        supports_vision=True,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=1.0,
        knowledge_cutoff="2025-05",
    ),
    "ollama": ModelCapability(
        model_id="__ollama_default__",
        provider="ollama",
        max_output_tokens=4096,
        context_window=8192,
        supports_vision=False,
        supports_tool_use=True,
        supports_temperature=True,
        temperature_min=0.0,
        temperature_max=2.0,
        knowledge_cutoff="2024-01",
    ),
}

# ---------------------------------------------------------------------------
# Regex helpers for fuzzy matching
# ---------------------------------------------------------------------------

# Matches a trailing date-like suffix: -YYYYMMDD or -YYYYMM or -YYYY-MM-DD
_TRAILING_DATE_RE = re.compile(r"-\d{4}[_-]?\d{0,4}[_-]?\d{0,4}$")

# Matches any trailing numeric version segment
_TRAILING_NUMERIC_RE = re.compile(r"-\d+$")


def _strip_date_suffix(model_id: str) -> str:
    """Strip a trailing date pattern from a model id.

    Examples:
        "claude-sonnet-4-20250514" -> "claude-sonnet-4"
        "claude-3-opus-20240229"   -> "claude-3-opus"
        "gpt-4-turbo"              -> "gpt-4-turbo" (unchanged)
    """
    return _TRAILING_DATE_RE.sub("", model_id)


def _strip_trailing_numeric(model_id: str) -> str:
    """Strip any trailing ``-<digits>`` segment.

    Applied after date stripping as a second pass.
    """
    return _TRAILING_NUMERIC_RE.sub("", model_id)


def _extract_provider(model_id: str) -> str:
    """Heuristically determine the provider name from a model id string.

    This is intentionally simple -- if the id starts with a known prefix
    the provider is returned; otherwise we fall back to "openai" as a
    reasonable default for unrecognised ids.

    Returns one of: anthropic, openai, deepseek, qwen, minimax, ollama
    """
    lower = model_id.lower()
    if lower.startswith("claude"):
        return "anthropic"
    if lower.startswith("gpt") or lower.startswith("o1") or lower.startswith("o3"):
        return "openai"
    if lower.startswith("deepseek"):
        return "deepseek"
    if lower.startswith("qwen"):
        return "qwen"
    if lower.startswith("minimax"):
        return "minimax"
    _ollama_prefixes = ("ollama", "llama", "mistral", "mixtral", "gemma", "phi")
    if lower.startswith(_ollama_prefixes):
        return "ollama"
    # generic unknown -- default to openai
    return "openai"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ModelCapabilityRegistry:
    """Lookup model capabilities with exact-match, fuzzy, and provider fallback.

    Usage::

        reg = ModelCapabilityRegistry()
        cap = reg.get("claude-sonnet-4-20250514")
        cap = reg.get("some-unknown-model")       # -> provider default
        cap = reg.get_provider_default("anthropic")
    """

    def __init__(
        self, overrides: dict[str, ModelCapability] | None = None
    ) -> None:
        """Initialise registry.

        Parameters
        ----------
        overrides:
            An optional dict mapping model-id strings to custom
            ``ModelCapability`` instances.  Entries here take precedence
            over the built-in data.
        """
        self._data: dict[str, ModelCapability] = dict(_BUILTIN)
        if overrides:
            self._data.update(overrides)
        # Track which model ids have been enriched from models.dev so
        # that callers sharing a registry don't re-fetch the same model.
        self._enriched_models: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, model_id: str) -> ModelCapability:
        """Look up a model by its identifier.

        Resolution order:
            1. Exact string match against registered model ids.
            2. Strip trailing date suffix (e.g. ``-20250514``) and retry.
            3. Strip any trailing ``-<digits>`` and retry.
            4. Fall back to the provider-level default.
        """
        # 1. Exact match
        candidate = self._data.get(model_id)
        if candidate is not None:
            return candidate

        # 2. Strip date suffix
        stripped = _strip_date_suffix(model_id)
        if stripped != model_id:
            candidate = self._data.get(stripped)
            if candidate is not None:
                return candidate

        # 3. Strip any trailing numeric
        stripped2 = _strip_trailing_numeric(stripped)
        if stripped2 != stripped:
            candidate = self._data.get(stripped2)
            if candidate is not None:
                return candidate

        # 4. Provider fallback
        provider = _extract_provider(model_id)
        logger.warning(
            "Model '%s' not in capability database, using %s provider default",
            model_id, provider,
        )
        return self.get_provider_default(provider)

    def set_override(self, model_id: str, capability: ModelCapability) -> None:
        """Set or update a model capability record in-place on this registry.

        This mutates the shared registry so that all callers referencing
        the same instance see the updated data immediately.  Typical use
        is to store models.dev enrichment results without creating a new
        registry object.
        """
        self._data[model_id] = capability
        self._enriched_models.add(model_id)

    def is_enriched(self, model_id: str) -> bool:
        """Return True if *model_id* has been enriched from models.dev."""
        return model_id in self._enriched_models

    def get_provider_default(self, provider: str) -> ModelCapability:
        """Return the safe default capability for a given provider.

        If the provider is not recognised a conservative ``openai``-like
        default is returned.
        """
        default = PROVIDER_DEFAULTS.get(provider)
        if default is not None:
            return default
        # Ultimate fallback -- conservative generic default.
        return ModelCapability(
            model_id=f"__{provider}_default__",
            provider=provider,
            max_output_tokens=4096,
            context_window=8192,
            supports_vision=False,
            supports_tool_use=True,
            supports_temperature=True,
            temperature_min=0.0,
            temperature_max=2.0,
            knowledge_cutoff="2024-01",
        )
