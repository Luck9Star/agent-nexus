"""Expert executor implementations for the agency pipeline."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .integrator import Artifact
from .llm_client import LLMClient

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
            raise ValueError(
                f"Profile '{profile_id}' not found in registry "
                "— cannot produce artifact"
            )

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
            metadata={"synthetic": True},
        )

    def _generate_sections(
        self,
        name: str,
        body: str,
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
                sections["findings"] = [
                    f"{cap} perspective on: {task}" for cap in capabilities[:3]
                ]
            elif section == "proposed_design":
                sections["proposed_design"] = f"[{name}] Design for: {task}"
            elif section == "tradeoffs":
                sections["tradeoffs"] = [
                    f"Trade-off from {cap} perspective"
                    for cap in capabilities[:2]
                ]
            elif section == "risks":
                sections["risks"] = [
                    f"Risk identified via {cap}" for cap in capabilities[:2]
                ]
            elif section == "next_steps":
                sections["next_steps"] = [
                    f"Follow up with {cap} analysis" for cap in capabilities[:2]
                ]
            elif section == "assumptions":
                sections["assumptions"] = [
                    f"Assumed: {task} relates to {cap}" for cap in capabilities[:2]
                ]
            elif section == "objective":
                sections["objective"] = f"[{name}] Orchestration plan for: {task}"
            elif section == "task_decomposition":
                sections["task_decomposition"] = [f"Subtask: apply {cap}" for cap in capabilities]
            elif section == "agent_assignments":
                sections["agent_assignments"] = {
                    cap: f"Assigned to {name}" for cap in capabilities[:2]
                }
            elif section == "execution_order":
                sections["execution_order"] = [
                    f"Step {i+1}: {cap}" for i, cap in enumerate(capabilities)
                ]
            else:
                logger.warning("Unmapped section '%s' in output contract for '%s'", section, name)
                sections[section] = f"[{name}] {section} for: {task}"
        return sections


class LLMExecutor:
    """Executor that calls a real LLM API using expert profiles as system prompts.

    Delegates API calls to :class:`LLMClient`.  Supports per-expert model
    overrides via the ``model`` field in expert profiles.
    """

    def __init__(
        self,
        registry: ExpertRegistry,
        model_string: str | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._config_dir = config_dir
        self._default_model_string = model_string

        # Create default client (used when expert has no model override)
        self._default_client = LLMClient(
            model_string=model_string,
            config_dir=config_dir,
        )

        # Cache per-expert clients (keyed by model string)
        self._expert_clients: dict[str, LLMClient] = {}

    @property
    def _model_name(self) -> str:
        """Default model name (backward compat)."""
        return self._default_client.model_name

    def _get_client(self, profile: dict[str, Any]) -> LLMClient:
        """Get LLMClient for an expert, respecting per-expert model override."""
        expert_model = profile.get("model")
        if not expert_model:
            return self._default_client

        if expert_model not in self._expert_clients:
            self._expert_clients[expert_model] = LLMClient(
                model_string=expert_model,
                config_dir=self._config_dir,
            )
            logger.info(
                "Per-expert model override: %s → %s",
                profile.get("id", "unknown"),
                expert_model,
            )
        return self._expert_clients[expert_model]

    def __call__(self, profile_id: str, task: str) -> Artifact:
        profile = self._registry.get(profile_id)
        if profile is None:
            raise ValueError(
                f"Profile '{profile_id}' not found in registry "
                "— cannot produce artifact"
            )

        name: str = profile.get("name", profile_id)
        body: str = profile.get("profile", {}).get("body", "")
        capabilities: list[str] = profile.get("capabilities", [])
        output_contract: dict[str, Any] = profile.get("output_contract", {})
        artifact_type: str = output_contract.get("artifact_type", "report")
        required_sections: list[str] = output_contract.get("required_sections", ["summary"])

        system_prompt = self._build_system_prompt(
            name=name,
            body=body,
            capabilities=capabilities,
            required_sections=required_sections,
        )

        # Use per-expert client if available
        client = self._get_client(profile)
        response = client.call(system_prompt=system_prompt, user_message=task)
        sections = self._parse_sections(response.text, required_sections)

        return Artifact(
            source_agent=profile_id,
            artifact_type=artifact_type,
            sections=sections,
            metadata={"llm": True, "model": response.model, "provider": response.provider},
        )

    # ------------------------------------------------------------------
    # Prompt building & parsing
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        name: str,
        body: str,
        capabilities: list[str],
        required_sections: list[str],
    ) -> str:
        """Build the full system prompt with section output instructions."""
        parts: list[str] = []

        if body:
            parts.append(body)
        else:
            parts.append(f"You are {name}, an expert assistant.")

        if capabilities:
            parts.append(
                "Your areas of expertise: " + ", ".join(capabilities) + "."
            )

        section_list = ", ".join(required_sections)
        parts.append(
            "Your response must include these sections as ## markdown headings: "
            + section_list + "."
        )
        parts.append(
            "Use exactly these heading names so they can be parsed. "
            "Provide substantive content under each heading."
        )

        return "\n\n".join(parts)

    def _parse_sections(
        self, response_text: str, required_sections: list[str],
    ) -> dict[str, object]:
        """Parse LLM response into sections using ``##`` markdown headings."""
        # Build a case-insensitive lookup: normalized_key -> original_key
        required_normalized: dict[str, str] = {
            _normalize_heading(s): s for s in required_sections
        }

        # Split by ## headings (level-2 only)
        pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
        splits = pattern.split(response_text)

        # splits: [preamble, heading1, content1, heading2, content2, ...]
        # First element is text before any ## heading — skip it.
        sections: dict[str, object] = {}

        for i in range(1, len(splits) - 1, 2):
            heading_raw = splits[i].strip()
            content = splits[i + 1].strip()
            heading_norm = _normalize_heading(heading_raw)

            if heading_norm in required_normalized:
                original_key = required_normalized[heading_norm]
                sections[original_key] = content
            else:
                # Keep unmatched headings under their original name
                sections[heading_raw] = content

        # Fill missing required sections with empty string
        for key in required_sections:
            if key not in sections:
                sections[key] = ""

        return sections


def _normalize_heading(heading: str) -> str:
    """Normalize a heading for case-insensitive, whitespace-insensitive comparison."""
    return re.sub(r"\s+", "_", heading.strip().lower())
