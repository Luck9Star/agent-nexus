"""EvolutionConfig -- configurable thresholds for the evolution engine.

Reads config/evolution.toml (or uses defaults when not present).
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "fix_fallback_rate": 0.4,
    "fix_applied_rate": 0.4,
    "fix_completion_rate": 0.35,
    "derived_effective_rate": 0.55,
    "derived_applied_rate": 0.25,
    "promotion_effective_rate": 0.8,
    "promotion_min_selections": 50,
}

_DEFAULT_LLM: dict[str, Any] = {
    "model": "anthropic:claude-sonnet-4-20250514",
    "temperature": 0.3,
    "max_tokens": 4096,
}

_DEFAULT_EXPERIMENT: dict[str, Any] = {
    "min_samples": 30,
    "confidence_level": 0.95,
    "max_duration_days": 7,
}


@dataclass
class EvolutionConfig:
    """Evolution engine configuration (from evolution.toml or defaults)."""

    enabled: bool = True
    auto_promote: bool = False
    max_evolution_per_day: int = 10

    # Thresholds
    thresholds: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_THRESHOLDS))

    # LLM settings
    llm_model: str = _DEFAULT_LLM["model"]
    llm_temperature: float = _DEFAULT_LLM["temperature"]
    llm_max_tokens: int = _DEFAULT_LLM["max_tokens"]

    # Experiment settings
    experiment_min_samples: int = _DEFAULT_EXPERIMENT["min_samples"]
    experiment_confidence_level: float = _DEFAULT_EXPERIMENT["confidence_level"]
    experiment_max_duration_days: int = _DEFAULT_EXPERIMENT["max_duration_days"]

    @classmethod
    def load(cls, config_path: Path | None = None) -> EvolutionConfig:
        """Load config from TOML file, falling back to defaults."""
        if config_path is None or not config_path.exists():
            return cls()

        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            logger.warning(
                "Failed to load evolution config from %s",
                config_path,
                exc_info=True,
            )
            return cls()

        evo = data.get("evolution", {})
        thresholds = {**_DEFAULT_THRESHOLDS, **evo.get("thresholds", {})}
        llm = {**_DEFAULT_LLM, **evo.get("llm", {})}
        experiment = {**_DEFAULT_EXPERIMENT, **evo.get("experiment", {})}

        return cls(
            enabled=evo.get("enabled", True),
            auto_promote=evo.get("auto_promote", False),
            max_evolution_per_day=evo.get("max_evolution_per_day", 10),
            thresholds=thresholds,
            llm_model=llm.get("model", _DEFAULT_LLM["model"]),
            llm_temperature=float(llm.get("temperature", _DEFAULT_LLM["temperature"])),
            llm_max_tokens=int(llm.get("max_tokens", _DEFAULT_LLM["max_tokens"])),
            experiment_min_samples=int(
                experiment.get("min_samples", _DEFAULT_EXPERIMENT["min_samples"])
            ),
            experiment_confidence_level=float(
                experiment.get("confidence_level", _DEFAULT_EXPERIMENT["confidence_level"])
            ),
            experiment_max_duration_days=int(
                experiment.get("max_duration_days", _DEFAULT_EXPERIMENT["max_duration_days"])
            ),
        )
