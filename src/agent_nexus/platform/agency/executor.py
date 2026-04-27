"""Expert executor implementations for the agency pipeline."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.config.model_config import ModelConfigManager

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

    Supports both ``anthropic-messages`` and ``openai-compatible`` API formats.
    Reads model config from ``~/.agent-nexus/config.toml`` via
    :class:`ConfigLoader`.
    """

    _TIMEOUT = 120.0
    _MAX_TOKENS = 4096

    def __init__(
        self,
        registry: ExpertRegistry,
        model_string: str | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._registry = registry

        # 1. Load config
        loader = ConfigLoader(config_dir=config_dir)
        platform_config = loader.load_config()

        # 2. Create ModelConfigManager
        mgr = ModelConfigManager(platform_config)

        # 3. Resolve model string (use config default if not provided)
        resolved = model_string or mgr.resolve_model(__name__)
        if not resolved:
            raise ValueError(
                "No model string resolved — set [models].default in config.toml "
                "or pass model_string explicitly"
            )

        # 4. Parse into (provider_name, model_name)
        self._provider_name, self._model_name = mgr.parse_model_string(resolved)

        # 5. Get provider config (base_url, api_type)
        self._provider_config = mgr.get_provider_config(self._provider_name)

        # 6. Resolve API key
        self._api_key = mgr.resolve_api_key(self._provider_name)
        if not self._api_key:
            raise ValueError(
                f"API key for provider '{self._provider_name}' is empty. "
                f"Set the environment variable referenced in config.toml "
                f"(e.g. api_key_env = 'API_API_KEY') and ensure it is exported."
            )

        logger.info(
            "LLMExecutor initialized: provider=%s model=%s api=%s",
            self._provider_name,
            self._model_name,
            self._provider_config.api,
        )

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

        # Call LLM
        if self._provider_config.api == ProviderApiType.ANTHROPIC_MESSAGES:
            response_text = self._call_anthropic_api(system_prompt, task)
        else:
            response_text = self._call_openai_api(system_prompt, task)

        sections = self._parse_sections(response_text, required_sections)

        return Artifact(
            source_agent=profile_id,
            artifact_type=artifact_type,
            sections=sections,
            metadata={"llm": True, "model": self._model_name},
        )

    # ------------------------------------------------------------------
    # API callers
    # ------------------------------------------------------------------

    def _call_anthropic_api(self, system_prompt: str, user_message: str) -> str:
        """Call Anthropic Messages API format.

        ``POST {base_url}/v1/messages``
        """
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self._MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }

        with httpx.Client(timeout=self._TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Anthropic API call failed (status {resp.status_code}): "
                f"{resp.text[:500]}"
            )

        data = resp.json()
        # Anthropic Messages response: {"content": [{"type": "text", "text": "..."}]}
        content_blocks = data.get("content", [])
        return "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )

    def _call_openai_api(self, system_prompt: str, user_message: str) -> str:
        """Call OpenAI-compatible Chat Completions API.

        ``POST {base_url}/v1/chat/completions``
        """
        base_url = self._provider_config.base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        with httpx.Client(timeout=self._TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenAI-compatible API call failed (status {resp.status_code}): "
                f"{resp.text[:500]}"
            )

        data = resp.json()
        # OpenAI Chat Completions response: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""

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
