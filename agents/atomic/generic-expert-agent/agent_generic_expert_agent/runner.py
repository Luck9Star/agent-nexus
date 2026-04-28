"""Expert Agent Runner: PydanticAI-based execution layer for Expert Profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from .contract import validate_output_contract
from .profile_loader import assemble_prompt, load_expert_profile


@dataclass
class ExpertRunResult:
    """Structured result from an expert agent execution."""

    output: str
    contract_valid: bool = True
    missing_sections: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Backward-compatible string access."""
        if not self.contract_valid and self.missing_sections:
            missing = ", ".join(self.missing_sections)
            return f"{self.output}\n\n[WARNING] Missing required sections: {missing}"
        return self.output


class ExpertAgentRunner:
    """Load an Expert Profile and execute tasks via PydanticAI.

    The runner:
    1. Loads the profile YAML from *profile_path*.
    2. Assembles a system prompt from the profile fields.
    3. Creates a ``pydantic_ai.Agent`` with that prompt.
    4. Provides ``run()`` to execute a task and validate output.
    """

    def __init__(self, profile_path: str) -> None:
        self.profile: dict[str, Any] = load_expert_profile(profile_path)
        self._prompt: str = assemble_prompt(self.profile)

        # Determine model: env var > profile config > default
        model = os.environ.get("AGENT_MODEL") or self._resolve_model_tier()

        self._agent: Agent[None, str] = Agent(
            model=model,
            system_prompt=self._prompt,
            output_type=str,
            defer_model_check=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str, context: str = "") -> ExpertRunResult:
        """Execute *task* through the PydanticAI agent (synchronous).

        Returns an ExpertRunResult with the output and contract validation status.
        Use ``.text`` for backward-compatible string access.
        """
        user_message = task
        if context:
            user_message = f"{task}\n\nContext:\n{context}"

        result = self._agent.run_sync(user_message)
        output: str = result.output

        return self._validate_and_build(output)

    async def arun(self, task: str, context: str = "") -> ExpertRunResult:
        """Async variant of ``run()``."""
        user_message = task
        if context:
            user_message = f"{task}\n\nContext:\n{context}"

        result = await self._agent.run(user_message)
        output: str = result.output

        return self._validate_and_build(output)

    def _validate_and_build(self, output: str) -> ExpertRunResult:
        """Validate output against contract and build ExpertRunResult."""
        contract = self.profile.get("output_contract", {})
        if contract.get("required_sections"):
            validation = validate_output_contract(output, contract)
            if not validation["valid"]:
                return ExpertRunResult(
                    output=output,
                    contract_valid=False,
                    missing_sections=validation["missing_sections"],
                )

        return ExpertRunResult(output=output)

    def get_permissions(self) -> dict[str, Any]:
        """Return the plan-only permissions from the profile."""
        return dict(self.profile.get("permissions", {}))

    def get_model_tier(self) -> str:
        """Return the model tier from the profile's runtime config."""
        runtime = self.profile.get("runtime", {})
        return runtime.get("model_tier", "standard")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_model_tier(self) -> str:
        """Map the profile's model_tier to a KnownModelName string.

        Uses a simple heuristic: lightweight -> gpt-4o-mini,
        standard -> gpt-4o, heavyweight -> claude-sonnet.  The actual
        model can always be overridden via the AGENT_MODEL env var.
        """
        tier = self.get_model_tier()
        mapping = {
            "lightweight": "openai:gpt-4o-mini",
            "standard": "openai:gpt-4o",
            "heavyweight": "anthropic:claude-sonnet-4-20250514",
        }
        return mapping.get(tier, "openai:gpt-4o")
