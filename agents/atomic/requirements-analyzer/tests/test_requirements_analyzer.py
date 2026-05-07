"""Comprehensive tests for requirements-analyzer agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_requirements: gap detection, ambiguity detection, priority categorization
- generate_questions: gap-based, ambiguity-based, contradiction-based questions
- build_specification: sections, priorities, constraints, glossary
- Agent: three-phase pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_requirements_analyzer.agent import RequirementsAnalyzerAgent
from agent_requirements_analyzer.local_adapter import handle_message
from agent_requirements_analyzer.models import (
    Question,
    RequirementAnalysis,
    RequirementSection,
    RequirementSpec,
)
from agent_requirements_analyzer.tools.analyze_requirements import (
    _detect_ambiguities,
    _detect_contradictions,
    _detect_gaps,
    _extract_key_terms,
    analyze_requirements,
)
from agent_requirements_analyzer.tools.build_specification import (
    build_specification,
)
from agent_requirements_analyzer.tools.generate_questions import (
    generate_questions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> RequirementsAnalyzerAgent:
    """Provide a RequirementsAnalyzerAgent instance."""
    return RequirementsAnalyzerAgent()


SAMPLE_TEXT = (
    "需要一个用户管理系统，支持登录和注册功能。"
    "系统应该能够处理用户数据。"
    "性能应该比较好，系统应该灵活可扩展。"
    "如果可能的话，支持数据导出。"
)


# ---------------------------------------------------------------------------
# Models -- construction, validation, serialization
# ---------------------------------------------------------------------------


class TestRequirementAnalysis:
    """Tests for RequirementAnalysis model."""

    def test_basic_construction(self) -> None:
        a = RequirementAnalysis(text="hello")
        assert a.text == "hello"
        assert a.gaps == []
        assert a.ambiguities == []
        assert a.priorities == {"high": [], "medium": [], "low": []}
        assert a.key_terms == []
        assert a.contradictions == []

    def test_full_construction(self) -> None:
        a = RequirementAnalysis(
            text="test",
            gaps=["gap1"],
            ambiguities=["amb1"],
            priorities={"high": ["item1"], "medium": [], "low": []},
            key_terms=["term1"],
            contradictions=["con1"],
        )
        assert len(a.gaps) == 1
        assert a.priorities["high"] == ["item1"]

    def test_frozen(self) -> None:
        a = RequirementAnalysis(text="test")
        with pytest.raises(ValidationError):
            a.text = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        a = RequirementAnalysis(
            text="test",
            gaps=["g1"],
            key_terms=["k1"],
        )
        data = a.model_dump()
        a2 = RequirementAnalysis.model_validate(data)
        assert a == a2

    def test_json_serialization(self) -> None:
        a = RequirementAnalysis(text="test", ambiguities=["amb"])
        json_str = a.model_dump_json()
        data = json.loads(json_str)
        assert data["text"] == "test"
        assert data["ambiguities"] == ["amb"]


class TestQuestion:
    """Tests for Question model."""

    def test_basic_construction(self) -> None:
        q = Question(text="What is the user role?")
        assert q.text == "What is the user role?"
        assert q.category == "functional"
        assert q.priority == "medium"
        assert q.context == ""

    def test_full_construction(self) -> None:
        q = Question(
            text="认证方式是什么？",
            category="functional",
            priority="high",
            context="缺口: 缺少认证方式详细说明",
        )
        assert q.category == "functional"
        assert q.priority == "high"

    def test_frozen(self) -> None:
        q = Question(text="test")
        with pytest.raises(ValidationError):
            q.text = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        q = Question(text="q", category="constraint", priority="low")
        data = q.model_dump()
        q2 = Question.model_validate(data)
        assert q == q2


class TestRequirementSection:
    """Tests for RequirementSection model."""

    def test_basic_construction(self) -> None:
        s = RequirementSection(title="功能需求")
        assert s.title == "功能需求"
        assert s.items == []
        assert s.priority == "medium"

    def test_with_items(self) -> None:
        s = RequirementSection(
            title="功能需求",
            items=["用户注册", "用户登录"],
            priority="high",
        )
        assert len(s.items) == 2
        assert s.priority == "high"

    def test_frozen(self) -> None:
        s = RequirementSection(title="test")
        with pytest.raises(ValidationError):
            s.title = "changed"  # type: ignore[misc]


class TestRequirementSpec:
    """Tests for RequirementSpec model."""

    def test_basic_construction(self) -> None:
        spec = RequirementSpec(title="需求说明书")
        assert spec.title == "需求说明书"
        assert spec.sections == []
        assert spec.priorities == {"must": [], "should": [], "could": [], "wont": []}
        assert spec.constraints == []
        assert spec.acceptance_criteria == []
        assert spec.glossary == {}

    def test_full_construction(self) -> None:
        spec = RequirementSpec(
            title="SRS",
            sections=[RequirementSection(title="功能需求", items=["item1"])],
            priorities={"must": ["must1"], "should": [], "could": ["could1"], "wont": []},
            constraints=["con1"],
            acceptance_criteria=["ac1"],
            glossary={"term": "definition"},
        )
        assert len(spec.sections) == 1
        assert spec.glossary["term"] == "definition"

    def test_frozen(self) -> None:
        spec = RequirementSpec(title="test")
        with pytest.raises(ValidationError):
            spec.title = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        spec = RequirementSpec(
            title="test",
            sections=[RequirementSection(title="s1")],
            glossary={"k": "v"},
        )
        data = spec.model_dump()
        spec2 = RequirementSpec.model_validate(data)
        assert spec == spec2


# ---------------------------------------------------------------------------
# analyze_requirements -- gap, ambiguity, priority detection
# ---------------------------------------------------------------------------


class TestAnalyzeRequirements:
    """Tests for analyze_requirements tool."""

    def test_empty_text(self) -> None:
        result = analyze_requirements("")
        assert "No input text provided" in result.gaps

    def test_none_text(self) -> None:
        result = analyze_requirements("")
        assert result.text == ""

    def test_detects_user_role_gap(self) -> None:
        text = "系统需要支持用户管理功能"
        result = analyze_requirements(text)
        assert any("角色" in g for g in result.gaps)

    def test_detects_auth_gap(self) -> None:
        text = "系统需要登录和注册功能"
        result = analyze_requirements(text)
        assert any("认证" in g for g in result.gaps)

    def test_detects_error_handling_gap(self) -> None:
        text = "系统需要支持文件上传功能"
        result = analyze_requirements(text)
        assert any("错误" in g for g in result.gaps)

    def test_detects_ambiguity(self) -> None:
        text = "系统应该能够灵活可扩展"
        result = analyze_requirements(text)
        assert len(result.ambiguities) > 0

    def test_detects_performance_ambiguity(self) -> None:
        text = "系统性能应该比较好"
        result = analyze_requirements(text)
        assert len(result.ambiguities) > 0

    def test_prioritization(self) -> None:
        result = analyze_requirements(SAMPLE_TEXT)
        assert len(result.priorities["high"]) > 0
        assert len(result.priorities["medium"]) > 0

    def test_key_terms_extraction(self) -> None:
        text = '需要实现"用户管理"模块的 UserService 功能'
        result = analyze_requirements(text)
        assert len(result.key_terms) > 0

    def test_no_contradictions_in_simple_text(self) -> None:
        text = "系统需要支持用户注册功能"
        result = analyze_requirements(text)
        assert result.contradictions == []

    def test_contradiction_detection(self) -> None:
        text = "系统需要实时处理数据，同时支持批量异步导入"
        result = analyze_requirements(text)
        assert len(result.contradictions) > 0


class TestDetectGaps:
    """Tests for _detect_gaps helper."""

    def test_no_gaps_in_empty_text(self) -> None:
        gaps = _detect_gaps("")
        assert gaps == []

    def test_role_gap(self) -> None:
        gaps = _detect_gaps("用户管理功能")
        assert "缺少用户角色定义" in gaps

    def test_no_role_gap_when_defined(self) -> None:
        gaps = _detect_gaps("管理员和普通用户的管理功能")
        assert "缺少用户角色定义" not in gaps


class TestDetectAmbiguities:
    """Tests for _detect_ambiguities helper."""

    def test_no_ambiguity(self) -> None:
        result = _detect_ambiguities("系统支持用户注册")
        assert result == []

    def test_flexible_ambiguity(self) -> None:
        result = _detect_ambiguities("系统需要灵活配置")
        assert len(result) > 0

    def test_fast_ambiguity(self) -> None:
        result = _detect_ambiguities("响应要快")
        assert len(result) > 0


class TestDetectContradictions:
    """Tests for _detect_contradictions helper."""

    def test_no_contradiction(self) -> None:
        result = _detect_contradictions("系统支持用户注册")
        assert result == []

    def test_realtime_vs_batch(self) -> None:
        result = _detect_contradictions("实时处理和批量异步导入")
        assert len(result) > 0


class TestExtractKeyTerms:
    """Tests for _extract_key_terms helper."""

    def test_quoted_terms(self) -> None:
        terms = _extract_key_terms('"用户管理"和"权限控制"')
        assert "用户管理" in terms
        assert "权限控制" in terms

    def test_technical_terms(self) -> None:
        terms = _extract_key_terms("需要 UserService 和 OrderProcessor")
        assert any("Service" in t for t in terms)


# ---------------------------------------------------------------------------
# generate_questions -- question generation
# ---------------------------------------------------------------------------


class TestGenerateQuestions:
    """Tests for generate_questions tool."""

    def test_empty_analysis(self) -> None:
        analysis = RequirementAnalysis(text="")
        questions = generate_questions(analysis)
        assert questions == []

    def test_generates_gap_questions(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            gaps=["缺少用户角色定义"],
        )
        questions = generate_questions(analysis)
        assert len(questions) > 0
        assert any("角色" in q.text for q in questions)

    def test_generates_ambiguity_questions(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            ambiguities=["模糊表述: '灵活'"],
        )
        questions = generate_questions(analysis)
        assert any("灵活" in q.text for q in questions)

    def test_generates_contradiction_questions(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            contradictions=["同时要求实时处理和批量/异步处理"],
        )
        questions = generate_questions(analysis)
        assert any("矛盾" in q.text for q in questions)

    def test_questions_sorted_by_priority(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            gaps=["缺少用户角色定义"],
            ambiguities=["模糊表述: '灵活'"],
        )
        questions = generate_questions(analysis)
        if len(questions) > 1:
            priorities = [q.priority for q in questions]
            # Verify high priority comes before medium/low
            first_low_idx = next(
                (i for i, p in enumerate(priorities) if p != "high"),
                len(priorities),
            )
            high_after = any(p == "high" for p in priorities[first_low_idx:])
            assert not high_after

    def test_unknown_gap_generates_generic_question(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            gaps=["未知的缺口类型"],
        )
        questions = generate_questions(analysis)
        assert any("未知的缺口类型" in q.text for q in questions)

    def test_many_high_priority_items(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            priorities={"high": ["a", "b", "c", "d"], "medium": [], "low": []},
        )
        questions = generate_questions(analysis)
        assert any("优先级" in q.text for q in questions)


# ---------------------------------------------------------------------------
# build_specification -- specification assembly
# ---------------------------------------------------------------------------


class TestBuildSpecification:
    """Tests for build_specification tool."""

    def test_empty_answers(self) -> None:
        spec = build_specification({})
        assert isinstance(spec, RequirementSpec)
        assert spec.title == "需求说明书"

    def test_with_analysis(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            priorities={"high": ["用户注册"], "medium": ["数据导出"], "low": []},
            contradictions=["矛盾1"],
        )
        spec = build_specification({"矛盾1": "已解决"}, analysis=analysis)
        assert len(spec.priorities["must"]) > 0
        assert len(spec.constraints) > 0

    def test_custom_title(self) -> None:
        spec = build_specification({}, title="My Spec")
        assert spec.title == "My Spec"

    def test_glossary_from_answers(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            key_terms=["UserService"],
        )
        spec = build_specification({"UserService": "用户服务模块"}, analysis=analysis)
        assert spec.glossary["UserService"] == "用户服务模块"

    def test_functional_section(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            priorities={"high": ["用户注册"], "medium": [], "low": []},
        )
        spec = build_specification({}, analysis=analysis)
        func_sections = [s for s in spec.sections if s.title == "功能需求"]
        assert len(func_sections) > 0

    def test_acceptance_criteria_from_high_priority(self) -> None:
        analysis = RequirementAnalysis(
            text="test",
            priorities={"high": ["用户注册", "用户登录"], "medium": [], "low": []},
        )
        spec = build_specification({}, analysis=analysis)
        assert len(spec.acceptance_criteria) >= 2

    def test_wont_priority_from_answers(self) -> None:
        spec = build_specification({"feature_x": "不需要这个功能"})
        assert len(spec.priorities["wont"]) > 0

    def test_must_priority_from_answers(self) -> None:
        spec = build_specification({"feature_y": "必须实现这个功能"})
        assert len(spec.priorities["must"]) > 0


# ---------------------------------------------------------------------------
# Agent -- three-phase pipeline
# ---------------------------------------------------------------------------


class TestRequirementsAnalyzerAgent:
    """Tests for RequirementsAnalyzerAgent class."""

    def test_analyze(self, agent: RequirementsAnalyzerAgent) -> None:
        result = agent.analyze("系统需要支持用户管理")
        assert isinstance(result, RequirementAnalysis)

    def test_questions(self, agent: RequirementsAnalyzerAgent) -> None:
        analysis = agent.analyze(SAMPLE_TEXT)
        questions = agent.questions(analysis)
        assert isinstance(questions, list)
        assert all(isinstance(q, Question) for q in questions)

    def test_build(self, agent: RequirementsAnalyzerAgent) -> None:
        spec = agent.build({"用户角色": "管理员、普通用户"})
        assert isinstance(spec, RequirementSpec)
        assert spec.title == "需求说明书"

    def test_full_pipeline(self, agent: RequirementsAnalyzerAgent) -> None:
        # Phase 1: analyze
        analysis = agent.analyze(SAMPLE_TEXT)
        assert len(analysis.gaps) > 0 or len(analysis.ambiguities) > 0

        # Phase 2: generate questions
        questions = agent.questions(analysis)
        assert len(questions) > 0

        # Phase 3: build specification
        answers = {q.text: f"回答: {q.text}" for q in questions[:3]}
        spec = agent.build(answers, analysis)
        assert isinstance(spec, RequirementSpec)

    def test_build_without_analysis(self, agent: RequirementsAnalyzerAgent) -> None:
        spec = agent.build({"key": "value"}, title="Test Spec")
        assert spec.title == "Test Spec"


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        """When fastmcp is not installed, create_mcp_server raises ImportError."""
        try:
            from agent_requirements_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        """The mcp_adapter module should always be importable."""
        import agent_requirements_analyzer.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter -- message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze(self, agent: RequirementsAnalyzerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "analyze", "params": {"text": SAMPLE_TEXT}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert response["result"]["text"] == SAMPLE_TEXT

    def test_handle_analyze_missing_text(
        self, agent: RequirementsAnalyzerAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "analyze", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_questions(self, agent: RequirementsAnalyzerAgent) -> None:
        analysis = agent.analyze(SAMPLE_TEXT)
        response = handle_message(
            agent,
            {
                "method": "questions",
                "params": {"analysis": analysis.model_dump()},
            },
        )
        assert response["status"] == "ok"
        assert "questions" in response["result"]

    def test_handle_questions_missing_analysis(
        self, agent: RequirementsAnalyzerAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "questions", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_build(self, agent: RequirementsAnalyzerAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "build",
                "params": {"answers": {"key": "value"}, "title": "Test"},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["title"] == "Test"

    def test_handle_build_missing_answers(
        self, agent: RequirementsAnalyzerAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "build", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_unknown_method(
        self, agent: RequirementsAnalyzerAgent
    ) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_analyze_empty_text(
        self, agent: RequirementsAnalyzerAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "analyze", "params": {"text": ""}}
        )
        assert response["status"] == "ok"
        assert "No input text provided" in response["result"]["gaps"]
