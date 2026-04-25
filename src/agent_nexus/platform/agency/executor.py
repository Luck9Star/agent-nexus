"""Expert executor implementations for the agency pipeline."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .integrator import Artifact

if TYPE_CHECKING:
    from .registry import ExpertRegistry

logger = logging.getLogger(__name__)


class ProfileBasedExecutor:
    """Executor that constructs artifacts using expert profile data.

    Uses the profile's system prompt, capabilities, and output contract
    to produce structured artifacts. This is the foundation for real
    LLM execution -- subclass and override ``_generate_sections`` to
    integrate with an actual LLM provider.
    """

    def __init__(self, registry: ExpertRegistry) -> None:
        self._registry = registry

    def __call__(self, profile_id: str, task: str) -> Artifact:
        profile = self._registry.get(profile_id)
        if profile is None:
            logger.warning("Profile '%s' not found in registry, using stub", profile_id)
            return Artifact(source_agent=profile_id, artifact_type="stub", sections={"context": task})

        name = profile.get("name", profile_id)
        body = profile.get("profile", {}).get("body", "")
        output_contract = profile.get("output_contract", {})
        artifact_type = output_contract.get("artifact_type", "report")
        required_sections = output_contract.get("required_sections", ["summary"])
        capabilities = profile.get("capabilities", [])

        sections = self._generate_sections(
            name=name,
            body=body,
            capabilities=capabilities,
            task=task,
            required_sections=required_sections,
        )

        return Artifact(
            source_agent=profile_id,
            artifact_type=artifact_type,
            sections=sections,
        )

    def _generate_sections(
        self,
        name: str,
        body: str,  # noqa: ARG002 — used by LLM subclasses
        capabilities: list[str],
        task: str,
        required_sections: list[str],
    ) -> dict[str, object]:
        """Generate artifact sections. Override in subclass for LLM integration."""
        sections: dict[str, object] = {}
        for section in required_sections:
            if section == "context":
                sections["context"] = task
            elif section == "summary":
                sections["summary"] = f"[{name}] Analysis of: {task}"
            elif section == "recommendations":
                sections["recommendations"] = [f"Apply {name} expertise to: {task}"]
            elif section == "findings":
                sections["findings"] = [f"{cap} perspective on: {task}" for cap in capabilities[:3]]
            elif section == "proposed_design":
                sections["proposed_design"] = f"[{name}] Design for: {task}"
            elif section == "tradeoffs":
                sections["tradeoffs"] = [f"Trade-off from {cap} perspective" for cap in capabilities[:2]]
            elif section == "risks":
                sections["risks"] = [f"Risk identified via {cap}" for cap in capabilities[:2]]
            elif section == "next_steps":
                sections["next_steps"] = [f"Follow up with {cap} analysis" for cap in capabilities[:2]]
            elif section == "assumptions":
                sections["assumptions"] = [f"Assumed: {task} relates to {cap}" for cap in capabilities[:2]]
            elif section == "objective":
                sections["objective"] = f"[{name}] Orchestration plan for: {task}"
            elif section == "task_decomposition":
                sections["task_decomposition"] = [f"Subtask: apply {cap}" for cap in capabilities]
            elif section == "agent_assignments":
                sections["agent_assignments"] = {cap: f"Assigned to {name}" for cap in capabilities[:2]}
            elif section == "execution_order":
                sections["execution_order"] = [f"Step {i+1}: {cap}" for i, cap in enumerate(capabilities)]
            else:
                sections[section] = f"[{name}] {section} for: {task}"
        return sections
