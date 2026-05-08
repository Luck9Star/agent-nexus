"""Expert executor implementations for the agency pipeline."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .integrator import Artifact
from .llm_client import LLMClient

if TYPE_CHECKING:
    from agent_nexus.models.capability import ModelCapabilityRegistry

    from .registry import ExpertRegistry

logger = logging.getLogger(__name__)


_REASONING_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def _extract_reasoning_tags(text: str) -> tuple[str | None, str | None]:
    """Extract <thinking> and <summary> content from LLM response.

    Extracts the first occurrence of each tag (by design for v1).
    Strips ``<thinking>`` first so that ``<summary>`` mentions inside
    the thinking block are not falsely captured.
    """
    t_match = _REASONING_RE.search(text)
    thinking = t_match.group(1).strip() if t_match else None
    # Search for <summary> in text *after* removing thinking blocks
    # to avoid false matches when the LLM mentions the tag name inside
    # its own thinking process.
    text_without_thinking = _REASONING_RE.sub("", text)
    s_match = _SUMMARY_RE.search(text_without_thinking)
    summary = s_match.group(1).strip() if s_match else None
    return thinking, summary


def _strip_reasoning_tags(text: str) -> str:
    """Remove <thinking> and <summary> blocks from text before section parsing."""
    text = _REASONING_RE.sub("", text)
    text = _SUMMARY_RE.sub("", text)
    return text.strip()


class ProfileBasedExecutor:
    """Executor that constructs artifacts using expert profile data.

    Uses the profile's system prompt, capabilities, and output contract
    to produce structured artifacts. This is the foundation for real
    LLM execution -- subclass and override ``_generate_sections`` to
    integrate with an actual LLM provider.
    """

    def __init__(self, registry: ExpertRegistry) -> None:
        self._registry = registry

    def __call__(
        self,
        profile_id: str,
        task: str,
        *,
        upstream_artifacts: list[Any] | None = None,
    ) -> Artifact:
        profile = self._registry.get(profile_id)
        if profile is None:
            raise ValueError(
                f"Profile '{profile_id}' not found in registry — cannot produce artifact"
            )

        name = profile.get("name", profile_id)
        body = profile.get("profile", {}).get("body", "")
        output_contract = profile.get("output_contract", {})
        artifact_type = output_contract.get("artifact_type", "report")
        required_sections = output_contract.get("required_sections", ["summary"])
        capabilities = profile.get("capabilities", [])

        # Inject upstream artifact context into task description
        effective_task = _inject_upstream_context(task, upstream_artifacts)

        sections = self._generate_sections(
            name=name,
            body=body,
            capabilities=capabilities,
            task=effective_task,
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
            sections[section] = self._resolve_section(section, name, capabilities, task)
        return sections

    @staticmethod
    def _resolve_section(
        section: str,
        name: str,
        capabilities: list[str],
        task: str,
    ) -> object:
        """Map a section name to its generated value."""
        generators: dict[str, Callable[[], object]] = {
            "context": lambda: task,
            "summary": lambda: f"[{name}] Analysis of: {task}",
            "recommendations": lambda: [f"Apply {name} expertise to: {task}"],
            "findings": lambda: [f"{cap} perspective on: {task}" for cap in capabilities[:3]],
            "proposed_design": lambda: f"[{name}] Design for: {task}",
            "tradeoffs": lambda: [f"Trade-off from {cap} perspective" for cap in capabilities[:2]],
            "risks": lambda: [f"Risk identified via {cap}" for cap in capabilities[:2]],
            "next_steps": lambda: [f"Follow up with {cap} analysis" for cap in capabilities[:2]],
            "assumptions": lambda: [
                f"Assumed: {task} relates to {cap}" for cap in capabilities[:2]
            ],
            "objective": lambda: f"[{name}] Orchestration plan for: {task}",
            "task_decomposition": lambda: [f"Subtask: apply {cap}" for cap in capabilities],
            "agent_assignments": lambda: {cap: f"Assigned to {name}" for cap in capabilities[:2]},
            "execution_order": lambda: [
                f"Step {i + 1}: {cap}" for i, cap in enumerate(capabilities)
            ],
        }
        gen = generators.get(section)
        if gen is not None:
            return gen()
        logger.warning("Unmapped section '%s' in output contract for '%s'", section, name)
        return f"[{name}] {section} for: {task}"


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
        default_temperature: float | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
        timeout: float | None = None,
        client: LLMClient | None = None,
        reasoning_protocol: bool = False,
    ) -> None:
        self._registry = registry
        self._config_dir = config_dir
        self._default_model_string = model_string
        self._default_temperature = default_temperature
        self._timeout = timeout
        self._capability_registry = capability_registry
        self._reasoning_protocol = reasoning_protocol

        if client is not None:
            self._default_client = client
            self._owns_default_client = False
        else:
            self._default_client = LLMClient(
                model_string=model_string,
                config_dir=config_dir,
                capability_registry=capability_registry,
            )
            self._owns_default_client = True

        # Cache per-expert clients (keyed by model string)
        self._expert_clients: dict[str, LLMClient] = {}

    @property
    def model_name(self) -> str:
        """Default model name (public API for callers like CLI)."""
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
                capability_registry=self._capability_registry,
            )
            logger.info(
                "Per-expert model override: %s → %s",
                profile.get("id", "unknown"),
                expert_model,
            )
        return self._expert_clients[expert_model]

    def close(self) -> None:
        """Release resources held by per-expert client cache."""
        for client in self._expert_clients.values():
            client.close()
        self._expert_clients.clear()
        if self._owns_default_client:
            self._default_client.close()

    def __call__(
        self,
        profile_id: str,
        task: str,
        *,
        upstream_artifacts: list[Any] | None = None,
    ) -> Artifact:
        profile = self._registry.get(profile_id)
        if profile is None:
            raise ValueError(
                f"Profile '{profile_id}' not found in registry — cannot produce artifact"
            )

        logger.info("LLMExecutor: dispatching expert '%s'", profile_id)

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
            reasoning_protocol=self._reasoning_protocol,
        )

        # Inject upstream artifact context into user message
        effective_task = _inject_upstream_context(task, upstream_artifacts)

        # Use per-expert client if available
        client = self._get_client(profile)
        expert_temp = profile.get("temperature")
        expert_temperature = expert_temp if expert_temp is not None else self._default_temperature
        response = client.call(
            system_prompt=system_prompt,
            user_message=effective_task,
            temperature=expert_temperature,
            timeout=self._timeout,
        )

        if self._reasoning_protocol:
            thinking, summary = _extract_reasoning_tags(response.text)
            clean_text = _strip_reasoning_tags(response.text)
        else:
            thinking, summary = None, None
            clean_text = response.text

        sections = self._parse_sections(clean_text, required_sections)

        logger.info(
            "LLMExecutor: expert '%s' completed (model=%s, provider=%s)",
            profile_id,
            response.model,
            response.provider,
        )

        metadata = {
            "llm": True,
            "model": response.model,
            "provider": response.provider,
        }
        if thinking is not None:
            metadata["reasoning"] = thinking
        if summary is not None:
            metadata["expert_summary"] = summary

        return Artifact(
            source_agent=profile_id,
            artifact_type=artifact_type,
            sections=sections,
            metadata=metadata,
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
        *,
        reasoning_protocol: bool = False,
    ) -> str:
        """Build the full system prompt with section output instructions."""
        parts: list[str] = []

        if body:
            parts.append(body)
        else:
            parts.append(f"You are {name}, an expert assistant.")

        if capabilities:
            parts.append("Your areas of expertise: " + ", ".join(capabilities) + ".")

        section_list = ", ".join(required_sections)
        if reasoning_protocol:
            parts.append(
                "Follow this response protocol strictly:\n"
                "1. **Think**: Analyze the task inside <thinking> tags. Consider multiple\n"
                "   perspectives, identify edge cases, and evaluate trade-offs.\n"
                "2. **Summarize**: Output a one-line (<30 words) physical snapshot in <summary>\n"
                "   tags capturing your key finding and confidence level.\n"
                "3. **Structure**: Output your analysis as ## markdown headings using exactly\n"
                f"   these section names: {section_list}. Provide substantive content under each."
            )
        else:
            parts.append(
                "Your response must include these sections as ## markdown headings: "
                + section_list
                + "."
            )
            parts.append(
                "Use exactly these heading names so they can be parsed. "
                "Provide substantive content under each heading."
            )

        return "\n\n".join(parts)

    def _parse_sections(
        self,
        response_text: str,
        required_sections: list[str],
    ) -> dict[str, object]:
        """Parse LLM response into sections using ``##`` markdown headings.

        Fenced code blocks (```) are tracked so that ``##`` headings inside
        them are **not** treated as section delimiters.
        """
        # Build a case-insensitive lookup: normalized_key -> original_key
        required_normalized: dict[str, str] = {_normalize_heading(s): s for s in required_sections}

        # Split by ## headings (level-2 only)
        pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
        splits = pattern.split(response_text)

        # splits: [preamble, heading1, content1, heading2, content2, ...]
        # First element is text before any ## heading — skip it.
        sections: dict[str, object] = {}

        # Track fenced code block boundaries so we can skip ## headings
        # that appear inside code blocks.
        in_code_block: set[int] = _fenced_code_line_indices(response_text)

        # Precompute character offsets for each split element (avoids O(K×N)
        # repeated text.find calls).
        offsets = _compute_split_offsets(response_text, splits)

        for i in range(1, len(splits) - 1, 2):
            heading_raw = splits[i].strip()
            content = splits[i + 1].strip()

            # Skip headings that fall inside a fenced code block
            heading_offset = offsets[i]
            heading_line = response_text[:heading_offset].count("\n") + 1
            if heading_line in in_code_block:
                continue

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


def _fenced_code_line_indices(text: str) -> set[int]:
    """Return the set of 1-based line numbers that fall inside fenced code blocks."""
    code_lines: set[int] = set()
    depth = 0
    for lineno, line in enumerate(text.split("\n"), start=1):
        if line.strip().startswith("```"):
            depth += 1
            code_lines.add(lineno)  # the fence line itself
            continue
        if depth % 2 == 1:
            code_lines.add(lineno)
    return code_lines


def _compute_split_offsets(text: str, splits: list[str]) -> list[int]:
    """Precompute character offsets for each element in ``splits``.

    Replaces per-call ``_split_offset()`` with a single O(N) pass, reducing
    heading offset lookup from O(K×N) to O(N) total.
    """
    offsets: list[int] = []
    pos = 0
    for part in splits:
        offsets.append(pos)
        found = text.find(part, pos)
        if found != -1:
            pos = found + len(part)
        else:
            pos += len(part)
    return offsets


def _inject_upstream_context(task: str, upstream_artifacts: list[Any] | None) -> str:
    """Inject upstream artifact content into the task description.

    When ``upstream_artifacts`` is not empty, formats each artifact's content
    as a context block appended to the original task.  Returns the original
    task unchanged when ``upstream_artifacts`` is ``None`` or empty.
    """
    if not upstream_artifacts:
        return task

    # Import locally to avoid circular imports at module level
    from .integrator import Artifact

    context_parts: list[str] = [task, "", "## Upstream Artifacts", ""]
    for i, art in enumerate(upstream_artifacts, 1):
        if isinstance(art, Artifact):
            source = art.source_agent
            sections_text = "\n".join(f"  {k}: {v}" for k, v in art.sections.items())
            context_parts.append(f"### Artifact {i} (from {source})")
            context_parts.append(sections_text)
        else:
            context_parts.append(f"### Artifact {i}")
            context_parts.append(str(art))
        context_parts.append("")

    return "\n".join(context_parts)
