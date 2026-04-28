"""Comprehensive tests for localization-specialist agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_text: register detection, domain detection, key term extraction, complexity
- manage_glossary: add, list, search, delete, clear, conflict handling
- localize: glossary matching, case preservation, untranslated detection
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_localization_specialist.agent import LocalizationSpecialistAgent
from agent_localization_specialist.local_adapter import handle_message
from agent_localization_specialist.models import (
    Glossary,
    LocalizationResult,
    TermEntry,
    TextAnalysis,
)
from agent_localization_specialist.tools.analyze_text import (
    _detect_domain,
    _detect_register,
    _extract_key_terms,
    _assess_complexity,
    analyze_text,
)
from agent_localization_specialist.tools.localize import localize
from agent_localization_specialist.tools.manage_glossary import manage_glossary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> LocalizationSpecialistAgent:
    """Provide a LocalizationSpecialistAgent instance."""
    return LocalizationSpecialistAgent()


@pytest.fixture
def sample_glossary() -> Glossary:
    """Provide a sample glossary with tech terms."""
    return Glossary(
        source_lang="en",
        target_lang="zh",
        entries=[
            TermEntry(source="API", target="API", domain="tech"),
            TermEntry(source="endpoint", target="端点", domain="tech"),
            TermEntry(source="authentication", target="认证", domain="tech"),
        ],
    )


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestTermEntry:
    """Tests for TermEntry model."""

    def test_basic_construction(self) -> None:
        t = TermEntry(source="API")
        assert t.source == "API"
        assert t.target == ""
        assert t.domain == "general"

    def test_full_construction(self) -> None:
        t = TermEntry(source="endpoint", target="端点", context="技术接口", domain="tech")
        assert t.target == "端点"
        assert t.domain == "tech"

    def test_frozen(self) -> None:
        t = TermEntry(source="test")
        with pytest.raises(Exception):
            t.source = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        t = TermEntry(source="hello", target="你好", domain="general")
        data = t.model_dump()
        t2 = TermEntry.model_validate(data)
        assert t == t2

    def test_json_serialization(self) -> None:
        t = TermEntry(source="world", target="世界")
        json_str = t.model_dump_json()
        data = json.loads(json_str)
        assert data["source"] == "world"


class TestTextAnalysis:
    """Tests for TextAnalysis model."""

    def test_defaults(self) -> None:
        a = TextAnalysis()
        assert a.formality == "neutral"
        assert a.domain == "general"
        assert a.key_terms == []
        assert a.complexity == "medium"

    def test_with_values(self) -> None:
        a = TextAnalysis(formality="formal", domain="legal", key_terms=["plaintiff"], complexity="high")
        assert a.formality == "formal"

    def test_frozen(self) -> None:
        a = TextAnalysis()
        with pytest.raises(Exception):
            a.formality = "formal"  # type: ignore[misc]


class TestGlossary:
    """Tests for Glossary model."""

    def test_empty(self) -> None:
        g = Glossary()
        assert g.source_lang == "en"
        assert g.entries == []

    def test_with_entries(self) -> None:
        g = Glossary(
            source_lang="en",
            target_lang="ja",
            entries=[TermEntry(source="hello", target="こんにちは")],
        )
        assert len(g.entries) == 1
        assert g.target_lang == "ja"

    def test_frozen(self) -> None:
        g = Glossary()
        with pytest.raises(Exception):
            g.source_lang = "fr"  # type: ignore[misc]


class TestLocalizationResult:
    """Tests for LocalizationResult model."""

    def test_empty(self) -> None:
        r = LocalizationResult()
        assert r.translated_text == ""
        assert r.glossary_matches == []
        assert r.warnings == []

    def test_with_values(self) -> None:
        r = LocalizationResult(
            translated_text="认证端点",
            glossary_matches=["authentication", "endpoint"],
            warnings=[" untranslated: server"],
        )
        assert r.translated_text == "认证端点"

    def test_frozen(self) -> None:
        r = LocalizationResult(translated_text="test")
        with pytest.raises(Exception):
            r.translated_text = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# analyze_text — register, domain, key terms
# ---------------------------------------------------------------------------


class TestDetectRegister:
    """Tests for _detect_register helper."""

    def test_formal(self) -> None:
        text = "hereby pursuant to the aforementioned agreement shall be binding".lower()
        words = set(text.split())
        assert _detect_register(text, words) == "formal"

    def test_informal(self) -> None:
        text = "hey this is cool gonna be awesome lol".lower()
        words = set(text.split())
        assert _detect_register(text, words) == "informal"

    def test_neutral(self) -> None:
        text = "the application uses a database for storage".lower()
        words = set(text.split())
        assert _detect_register(text, words) == "neutral"


class TestDetectDomain:
    """Tests for _detect_domain helper."""

    def test_tech(self) -> None:
        words = {"api", "framework", "deployment", "server"}
        assert _detect_domain(words) == "tech"

    def test_legal(self) -> None:
        words = {"plaintiff", "jurisdiction", "liability", "statute"}
        assert _detect_domain(words) == "legal"

    def test_medical(self) -> None:
        words = {"diagnosis", "symptom", "treatment", "patient"}
        assert _detect_domain(words) == "medical"

    def test_business(self) -> None:
        words = {"revenue", "stakeholder", "roi", "quarterly"}
        assert _detect_domain(words) == "business"

    def test_general(self) -> None:
        words = {"hello", "world", "today", "weather"}
        assert _detect_domain(words) == "general"


class TestExtractKeyTerms:
    """Tests for _extract_key_terms helper."""

    def test_tech_terms(self) -> None:
        text = "The OAuth endpoint handles authentication for the API."
        words = set(text.lower().split())
        terms = _extract_key_terms(text, words, "tech")
        assert "api" in terms or "API" in terms

    def test_no_terms(self) -> None:
        text = "Hello world this is a test"
        words = set(text.lower().split())
        terms = _extract_key_terms(text, words, "general")
        assert terms == [] or len(terms) == 0


class TestAssessComplexity:
    """Tests for _assess_complexity helper."""

    def test_low_complexity(self) -> None:
        assert _assess_complexity("Hello world", [], "neutral") == "low"

    def test_high_complexity(self) -> None:
        long_text = " ".join(f"word{i}" for i in range(50))
        terms = [f"term{i}" for i in range(12)]
        assert _assess_complexity(long_text, terms, "formal") in ("medium", "high")


class TestAnalyzeText:
    """Tests for analyze_text tool."""

    def test_empty_text(self) -> None:
        result = analyze_text("")
        assert result.formality == "neutral"
        assert result.complexity == "low"

    def test_tech_text(self) -> None:
        result = analyze_text("The API endpoint requires authentication via OAuth.")
        assert result.domain == "tech"
        assert len(result.key_terms) >= 1

    def test_legal_text(self) -> None:
        result = analyze_text(
            "Pursuant to the aforementioned statute, the plaintiff shall file a complaint."
        )
        assert result.formality == "formal"
        assert result.domain == "legal"

    def test_informal_text(self) -> None:
        result = analyze_text("Hey, this is gonna be awesome! LOL")
        assert result.formality == "informal"

    def test_returns_text_analysis(self) -> None:
        result = analyze_text("Some text here")
        assert isinstance(result, TextAnalysis)


# ---------------------------------------------------------------------------
# manage_glossary — CRUD operations
# ---------------------------------------------------------------------------


class TestManageGlossary:
    """Tests for manage_glossary tool."""

    def test_add_entries(self) -> None:
        g = manage_glossary(
            "add",
            entries=[{"source": "API", "target": "API", "domain": "tech"}],
        )
        assert len(g.entries) == 1
        assert g.entries[0].source == "API"

    def test_add_multiple(self) -> None:
        g = manage_glossary(
            "add",
            entries=[
                {"source": "hello", "target": "你好"},
                {"source": "world", "target": "世界"},
            ],
        )
        assert len(g.entries) == 2

    def test_add_updates_existing(self) -> None:
        g = manage_glossary(
            "add",
            entries=[{"source": "test", "target": "测试1"}],
        )
        g = manage_glossary(
            "add",
            entries=[{"source": "test", "target": "测试2"}],
            glossary=g,
        )
        assert len(g.entries) == 1
        assert g.entries[0].target == "测试2"

    def test_list_returns_glossary(self) -> None:
        g = manage_glossary(
            "add",
            entries=[{"source": "a", "target": "b"}],
        )
        result = manage_glossary("list", glossary=g)
        assert len(result.entries) == 1

    def test_search(self) -> None:
        g = manage_glossary(
            "add",
            entries=[
                {"source": "API", "target": "API"},
                {"source": "endpoint", "target": "端点"},
            ],
        )
        result = manage_glossary(
            "search", entries=[{"source": "API"}], glossary=g
        )
        assert len(result.entries) == 1
        assert result.entries[0].source == "API"

    def test_delete(self) -> None:
        g = manage_glossary(
            "add",
            entries=[
                {"source": "API", "target": "API"},
                {"source": "endpoint", "target": "端点"},
            ],
        )
        result = manage_glossary(
            "delete", entries=[{"source": "API"}], glossary=g
        )
        assert len(result.entries) == 1
        assert result.entries[0].source == "endpoint"

    def test_clear(self) -> None:
        g = manage_glossary(
            "add",
            entries=[{"source": "a", "target": "b"}],
        )
        result = manage_glossary("clear", glossary=g)
        assert len(result.entries) == 0
        assert result.source_lang == g.source_lang

    def test_unknown_action_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown glossary action"):
            manage_glossary("invalid")

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            manage_glossary("add", entries=["not an entry"])

    def test_new_glossary_defaults(self) -> None:
        g = manage_glossary("list")
        assert g.source_lang == "en"
        assert g.target_lang == "zh"

    def test_custom_langs(self) -> None:
        g = manage_glossary("add", source_lang="ja", target_lang="en")
        assert g.source_lang == "ja"
        assert g.target_lang == "en"


# ---------------------------------------------------------------------------
# localize — glossary-aware translation
# ---------------------------------------------------------------------------


class TestLocalize:
    """Tests for localize tool."""

    def test_empty_text(self) -> None:
        result = localize("", "zh", {"hello": "你好"})
        assert result.translated_text == ""

    def test_no_glossary(self) -> None:
        result = localize("Hello world", "zh")
        assert result.translated_text == "Hello world"
        assert len(result.warnings) >= 1

    def test_basic_substitution(self) -> None:
        result = localize(
            "The API endpoint",
            "zh",
            {"API": "API", "endpoint": "端点"},
        )
        assert "端点" in result.translated_text
        assert "API" in result.glossary_matches
        assert "endpoint" in result.glossary_matches

    def test_case_insensitive(self) -> None:
        result = localize(
            "the api and API are different things",
            "zh",
            {"api": "接口"},
        )
        assert "接口" in result.translated_text

    def test_no_match(self) -> None:
        result = localize(
            "Hello world",
            "zh",
            {"server": "服务器"},
        )
        assert result.translated_text == "Hello world"
        assert "server" not in result.glossary_matches

    def test_longest_match_first(self) -> None:
        result = localize(
            "Use the API server endpoint",
            "zh",
            {"API": "接口", "API server": "接口服务器"},
        )
        # "API server" should be matched before "API"
        assert "接口服务器" in result.translated_text

    def test_empty_glossary_key_skipped(self) -> None:
        result = localize(
            "Hello world",
            "zh",
            {"": "空", "hello": "你好"},
        )
        # Empty key should not cause error
        assert isinstance(result, LocalizationResult)

    def test_cjk_untranslated_detection(self) -> None:
        result = localize(
            "The deployment configuration needs updating",
            "zh",
            {"deployment": "部署"},
        )
        # "configuration" is long English word in CJK target
        assert any("untranslated" in w.lower() for w in result.warnings)

    def test_non_cjk_no_warning(self) -> None:
        result = localize(
            "Hello world",
            "fr",
            {"hello": "bonjour"},
        )
        assert not any("untranslated" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Agent — full pipeline
# ---------------------------------------------------------------------------


class TestLocalizationSpecialistAgent:
    """Tests for LocalizationSpecialistAgent class."""

    def test_analyze_text(self, agent: LocalizationSpecialistAgent) -> None:
        result = agent.analyze_text("The API endpoint requires authentication.", "en")
        assert isinstance(result, TextAnalysis)
        assert result.domain == "tech"

    def test_manage_glossary(self, agent: LocalizationSpecialistAgent) -> None:
        result = agent.manage_glossary(
            "add",
            entries=[{"source": "test", "target": "测试"}],
        )
        assert isinstance(result, Glossary)
        assert len(result.entries) == 1

    def test_localize(self, agent: LocalizationSpecialistAgent) -> None:
        result = agent.localize("Hello API", "zh", {"API": "接口"})
        assert isinstance(result, LocalizationResult)

    def test_full_pipeline(self, agent: LocalizationSpecialistAgent) -> None:
        # Phase 1: analyze
        text = "The API endpoint requires authentication via OAuth 2.0 protocol."
        analysis = agent.analyze_text(text, "en")
        assert analysis.domain == "tech"
        assert len(analysis.key_terms) >= 1

        # Phase 2: build glossary
        g = agent.manage_glossary("add", entries=[
            {"source": "API", "target": "API", "domain": "tech"},
            {"source": "endpoint", "target": "端点", "domain": "tech"},
            {"source": "authentication", "target": "认证", "domain": "tech"},
        ])
        assert len(g.entries) >= 1

        # Phase 3: localize
        glossary_dict = {e.source: e.target for e in g.entries}
        result = agent.localize(text, "zh", glossary_dict)
        assert "端点" in result.translated_text
        assert "认证" in result.translated_text
        assert len(result.glossary_matches) >= 2


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_localization_specialist.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_localization_specialist.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "analyze",
                "params": {"text": "The API endpoint", "source_lang": "en"},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["domain"] == "tech"

    def test_handle_analyze_missing_text(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent, {"method": "analyze", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_glossary_add(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "glossary",
                "params": {
                    "action": "add",
                    "entries": [{"source": "test", "target": "测试"}],
                },
            },
        )
        assert response["status"] == "ok"
        assert len(response["result"]["entries"]) == 1

    def test_handle_glossary_missing_action(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent, {"method": "glossary", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_localize(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "localize",
                "params": {
                    "text": "API endpoint",
                    "target_lang": "zh",
                    "glossary": {"API": "接口", "endpoint": "端点"},
                },
            },
        )
        assert response["status"] == "ok"
        assert "端点" in response["result"]["translated_text"]

    def test_handle_localize_missing_target(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(
            agent,
            {"method": "localize", "params": {"text": "Hello"}},
        )
        assert response["status"] == "error"

    def test_handle_unknown_method(self, agent: LocalizationSpecialistAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
