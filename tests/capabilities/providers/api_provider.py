"""APIProvider — invoke agents via real LLM API calls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tests.capabilities.contracts.schema import CapabilityContract
from tests.capabilities.providers.base import ProviderResult, build_test_inputs


class APIProvider:
    """Invoke agent capabilities through real LLM API via LLMClient."""

    def __init__(self, model: str, config_dir: str | None = None) -> None:
        self.model = model
        self.config_dir = config_dir

    async def invoke(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any] | None = None,
    ) -> ProviderResult:
        if inputs is None:
            inputs = build_test_inputs(contract)

        prompt = self._build_prompt(contract, inputs)

        start = time.monotonic()
        try:
            from agent_nexus.models.capability import ModelCapabilityRegistry
            from agent_nexus.platform.agency.llm_client import LLMClient

            config_path = Path(self.config_dir) if self.config_dir else None
            registry = ModelCapabilityRegistry()

            with LLMClient(
                model_string=self.model,
                config_dir=config_path,
                capability_registry=registry,
            ) as client:
                response = client.call(prompt)
                duration_ms = (time.monotonic() - start) * 1000

                return ProviderResult(
                    success=True,
                    raw_output=response,
                    duration_ms=duration_ms,
                )
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                success=False,
                raw_output=None,
                duration_ms=duration_ms,
                error=str(exc),
            )

    def _build_prompt(
        self,
        contract: CapabilityContract,
        inputs: dict[str, Any],
    ) -> str:
        parts = [
            f"You are an expert {contract.agent_name} agent.",
            f"Task: {contract.description}",
            "",
            "Inputs:",
        ]
        for key, value in inputs.items():
            parts.append(f"  {key}: {value}")

        parts.append("")
        parts.append("Output as JSON with these fields:")
        for field_name, spec in contract.output_schema.items():
            req = " (required)" if spec.required else " (optional)"
            parts.append(f"  {field_name}: {spec.type}{req}")

        return "\n".join(parts)
