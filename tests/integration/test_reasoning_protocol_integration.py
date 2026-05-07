"""Integration tests for the Structured Reasoning Protocol.

Verifies end-to-end behavior: LLMExecutor with reasoning_protocol=True
extracts tags correctly and metadata is populated without polluting sections.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.executor import LLMExecutor
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.registry import ExpertRegistry


def _make_registry() -> ExpertRegistry:
    """Minimal registry with two expert profiles."""
    registry = ExpertRegistry()
    registry.add(
        "agency.code-reviewer",
        {
            "id": "agency.code-reviewer",
            "name": "Code Reviewer",
            "capabilities": ["code_review", "security_review"],
            "profile": {"body": "You are an expert code reviewer."},
            "output_contract": {
                "artifact_type": "report",
                "required_sections": ["summary", "findings", "recommendations"],
            },
        },
        ["code_review", "security_review"],
    )
    registry.add(
        "agency.architect",
        {
            "id": "agency.architect",
            "name": "System Architect",
            "capabilities": ["system_design"],
            "profile": {"body": "You are a system architect."},
            "output_contract": {
                "artifact_type": "report",
                "required_sections": ["summary", "proposed_design", "tradeoffs"],
            },
            "model": "anthropic:claude-sonnet-4-20250514",
        },
        ["system_design"],
    )
    return registry


# Simulated LLM response with reasoning protocol tags
_MOCK_RESPONSE_WITH_TAGS = """<thinking>
Let me analyze the codebase structure carefully.
The main concern is the tight coupling between modules.
Edge case: what if the database is unavailable?
</thinking>

<summary>
Found 3 coupling issues, confidence: high (85%)
</summary>

## summary
The codebase has moderate coupling between the authentication and user modules.
Recommend extracting a shared interface.

## findings
- Authentication module directly imports User model
- No abstraction layer between auth and user storage
- Session management mixed with user lifecycle

## recommendations
1. Extract a UserRepository interface
2. Use dependency injection for auth module
3. Add integration tests for the new boundaries
"""

_MOCK_RESPONSE_WITHOUT_TAGS = """## summary
Simple analysis without reasoning tags.

## findings
- Finding 1
- Finding 2

## recommendations
- Recommendation 1
"""


class TestReasoningProtocolIntegration:
    """End-to-end tests for reasoning protocol with LLMExecutor."""

    @patch("agent_nexus.platform.agency.executor.LLMClient")
    def test_reasoning_protocol_extracts_tags_to_metadata(self, mock_llm_client):
        """LLMExecutor with reasoning_protocol=True extracts tags to metadata."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=_MOCK_RESPONSE_WITH_TAGS,
            model="test-model",
            provider="test-provider",
        )
        mock_llm_client.return_value = mock_client

        registry = _make_registry()
        executor = LLMExecutor(
            registry=registry,
            reasoning_protocol=True,
            client=mock_client,
        )

        artifact = executor("agency.code-reviewer", "Review the authentication module")

        # Tags should NOT appear in sections
        assert "<thinking>" not in str(artifact.sections)
        assert "<summary>" not in str(artifact.sections)
        assert "Let me analyze" not in str(artifact.sections)

        # Tags SHOULD appear in metadata
        assert "reasoning" in artifact.metadata
        assert "expert_summary" in artifact.metadata
        assert "coupling" in artifact.metadata["reasoning"]
        assert "coupling issues" in artifact.metadata["expert_summary"]

        # Sections should parse correctly from clean text
        assert "coupling" in artifact.sections["summary"]
        assert "Authentication module" in artifact.sections["findings"]

    @patch("agent_nexus.platform.agency.executor.LLMClient")
    def test_no_reasoning_protocol_default_behavior(self, mock_llm_client):
        """LLMExecutor without reasoning_protocol behaves identically to before."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=_MOCK_RESPONSE_WITHOUT_TAGS,
            model="test-model",
            provider="test-provider",
        )
        mock_llm_client.return_value = mock_client

        registry = _make_registry()
        executor = LLMExecutor(
            registry=registry,
            reasoning_protocol=False,
            client=mock_client,
        )

        artifact = executor("agency.code-reviewer", "Review the authentication module")

        # No reasoning/summary in metadata
        assert "reasoning" not in artifact.metadata
        assert "expert_summary" not in artifact.metadata

        # Standard metadata still present
        assert artifact.metadata["llm"] is True
        assert artifact.metadata["model"] == "test-model"

        # Sections parse normally
        assert "Simple analysis" in artifact.sections["summary"]

    @patch("agent_nexus.platform.agency.executor.LLMClient")
    def test_per_expert_model_override_with_protocol(self, mock_llm_client):
        """Per-expert model override works correctly with reasoning protocol."""
        mock_default = MagicMock()
        mock_default.call.return_value = LLMResponse(
            text=(
                "<thinking>Default thinking</thinking>\n"
                "<summary>Default summary</summary>\n"
                "## summary\nDefault analysis\n"
                "## proposed_design\nDefault design\n"
                "## tradeoffs\n- Default tradeoff"
            ),
            model="default-model",
            provider="test",
        )
        mock_expert = MagicMock()
        mock_expert.call.return_value = LLMResponse(
            text=(
                "<thinking>Expert thinking</thinking>\n"
                "<summary>Expert summary</summary>\n"
                "## summary\nExpert analysis\n"
                "## proposed_design\nExpert design\n"
                "## tradeoffs\n- Expert tradeoff"
            ),
            model="claude-sonnet-4-20250514",
            provider="anthropic",
        )
        # First call: default client for executor init
        # Second call: per-expert client for architect profile
        mock_llm_client.side_effect = [mock_default, mock_expert]

        registry = _make_registry()
        executor = LLMExecutor(
            registry=registry,
            reasoning_protocol=True,
        )

        # Default model expert (code-reviewer has no model override)
        artifact_a = executor("agency.code-reviewer", "Review code")
        assert artifact_a.metadata["reasoning"] == "Default thinking"

        # Per-expert model override (architect has model override)
        artifact_b = executor("agency.architect", "Design system")
        assert artifact_b.metadata["reasoning"] == "Expert thinking"
        assert artifact_b.metadata["model"] == "claude-sonnet-4-20250514"

        executor.close()

    @patch("agent_nexus.platform.agency.executor.LLMClient")
    def test_protocol_on_but_no_tags_in_response(self, mock_llm_client):
        """When protocol is ON but LLM doesn't output tags, sections still parse."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=_MOCK_RESPONSE_WITHOUT_TAGS,
            model="test-model",
            provider="test-provider",
        )
        mock_llm_client.return_value = mock_client

        registry = _make_registry()
        executor = LLMExecutor(
            registry=registry,
            reasoning_protocol=True,
            client=mock_client,
        )

        artifact = executor("agency.code-reviewer", "Review code")

        # No tags → no reasoning/summary in metadata
        assert "reasoning" not in artifact.metadata
        assert "expert_summary" not in artifact.metadata

        # Sections still parse correctly
        assert "Simple analysis" in artifact.sections["summary"]
