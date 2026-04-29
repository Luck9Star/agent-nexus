"""Provider base types for capability testing."""

from __future__ import annotations

from dataclasses import dataclass, field
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


def build_test_inputs(contract: CapabilityContract) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for name, spec in contract.required_inputs.items():
        if spec.examples:
            inputs[name] = spec.examples[0]
    return inputs
