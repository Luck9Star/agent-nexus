"""Provider base types for capability testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract


@dataclass
class ProviderResult:
    success: bool
    raw_output: Any
    exit_code: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


_REPO_ROOT = Path(__file__).resolve().parents[3]


def build_test_inputs(contract: CapabilityContract) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for name, spec in contract.required_inputs.items():
        if spec.examples:
            value = spec.examples[0]
            # Resolve file-path inputs to absolute paths so they work
            # regardless of the agent subprocess CWD.
            if isinstance(value, str) and "file" in name and not Path(value).is_absolute():
                abs_path = _REPO_ROOT / value
                if abs_path.exists():
                    value = str(abs_path)
            inputs[name] = value
    return inputs
