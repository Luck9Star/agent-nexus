"""Comprehensive tests for market-intelligence-analyst agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_market: framework validation, factor extraction, scoring, insights
- identify_trends: trend detection, direction, impact, confidence
- generate_briefing: title, executive summary, sections, recommendations
- Agent: three-phase pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_market_intelligence_analyst.agent import MarketIntelligenceAgent
from agent_market_intelligence_analyst.local_adapter import handle_message
from agent_market_intelligence_analyst.models import (
    BriefingReport,
    MarketAnalysis,
    TrendItem,
    TrendReport,
)
from agent_market_intelligence_analyst.tools.analyze_market import (
    _compute_score,
    _count_keyword_hits,
    _generate_insights,
    analyze_market,
)
from agent_market_intelligence_analyst.tools.generate_briefing import (
    generate_briefing,
)
from agent_market_intelligence_analyst.tools.identify_trends import (
    _compute_confidence,
    _generate_summary,
    identify_trends,
)

# ---------------------------------------------------------------------------
# Sample market data
# ---------------------------------------------------------------------------

MARKET_DATA_CN = """\
AI人工智能市场研究报告

当前市场格局：
1. 供应商议价能力较强，核心技术供应商掌握关键AI芯片资源
2. 买方对价格越来越敏感，但同时也需要高质量的AI解决方案
3. 新进入者增多，进入壁垒相对较低，市场集中度下降
4. 替代品威胁显著，传统技术方案仍然有市场
5. 行业竞争激烈，各大厂商展开价格战争夺市场份额

市场趋势：
- AI技术和大模型快速发展，市场规模持续扩大
- 数字化转型加速，企业对云服务需求增长
- ESG可持续发展理念日益受到重视
- 监管趋严，数据保护和合规要求提高
- 消费升级趋势明显，用户对品质要求更高
"""

MARKET_DATA_EN = """\
Market Analysis Report: Technology Sector

The competitive landscape shows intense rivalry among existing players.
Market share battles are common as competitors increase their spending.

Buyer power is growing as customers have more alternatives and are
becoming more price sensitive.

New entrants continue to enter the market with innovative solutions,
though startup costs remain a barrier to entry.

The threat of substitutes is increasing with digital transformation
and alternative technologies emerging.

Key trends include:
- AI and machine learning driving growth across sectors
- Cloud computing and SaaS adoption accelerating
- ESG and sustainability gaining importance
- Regulatory compliance requirements increasing
"""

MINIMAL_DATA = "Some generic text without specific market signals."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> MarketIntelligenceAgent:
    """Provide a MarketIntelligenceAgent instance."""
    return MarketIntelligenceAgent()


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestMarketAnalysis:
    """Tests for MarketAnalysis model."""

    def test_basic_construction(self) -> None:
        ma = MarketAnalysis(framework="porter")
        assert ma.framework == "porter"
        assert ma.factors == {}
        assert ma.scores == {}
        assert ma.insights == []

    def test_full_construction(self) -> None:
        ma = MarketAnalysis(
            framework="swot",
            factors={"strengths": "Strong brand"},
            scores={"strengths": 4},
            insights=["Competitive advantage"],
        )
        assert ma.framework == "swot"
        assert ma.factors["strengths"] == "Strong brand"
        assert ma.scores["strengths"] == 4

    def test_frozen(self) -> None:
        ma = MarketAnalysis(framework="porter")
        with pytest.raises(ValidationError):
            ma.framework = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        ma = MarketAnalysis(
            framework="pestel",
            factors={"economic": "Growing"},
            scores={"economic": 3},
            insights=["GDP rising"],
        )
        data = ma.model_dump()
        ma2 = MarketAnalysis.model_validate(data)
        assert ma == ma2

    def test_json_serialization(self) -> None:
        ma = MarketAnalysis(framework="porter", insights=["test"])
        json_str = ma.model_dump_json()
        data = json.loads(json_str)
        assert data["framework"] == "porter"


class TestTrendItem:
    """Tests for TrendItem model."""

    def test_basic_construction(self) -> None:
        t = TrendItem(name="AI growth")
        assert t.name == "AI growth"
        assert t.direction == "stable"
        assert t.impact == "medium"
        assert t.evidence == ""
        assert t.timeframe == ""

    def test_full_construction(self) -> None:
        t = TrendItem(
            name="AI growth",
            direction="up",
            impact="high",
            evidence="Market data shows increase",
            timeframe="1-3 years",
        )
        assert t.direction == "up"
        assert t.impact == "high"

    def test_frozen(self) -> None:
        t = TrendItem(name="test")
        with pytest.raises(ValidationError):
            t.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        t = TrendItem(name="test", direction="down", impact="low")
        data = t.model_dump()
        t2 = TrendItem.model_validate(data)
        assert t == t2


class TestTrendReport:
    """Tests for TrendReport model."""

    def test_empty(self) -> None:
        tr = TrendReport()
        assert tr.trends == []
        assert tr.summary == ""
        assert tr.confidence == 0.0

    def test_with_trends(self) -> None:
        tr = TrendReport(
            trends=[TrendItem(name="AI", direction="up")],
            summary="1 trend found",
            confidence=0.75,
        )
        assert len(tr.trends) == 1
        assert tr.confidence == 0.75

    def test_frozen(self) -> None:
        tr = TrendReport()
        with pytest.raises(ValidationError):
            tr.trends = []  # type: ignore[misc]


class TestBriefingReport:
    """Tests for BriefingReport model."""

    def test_empty(self) -> None:
        br = BriefingReport()
        assert br.title == ""
        assert br.executive_summary == ""
        assert br.sections == {}
        assert br.recommendations == []

    def test_full(self) -> None:
        br = BriefingReport(
            title="Market Report",
            executive_summary="Strong growth expected",
            sections={"overview": "Market is growing"},
            recommendations=["Invest in R&D"],
        )
        assert br.title == "Market Report"
        assert len(br.recommendations) == 1

    def test_frozen(self) -> None:
        br = BriefingReport()
        with pytest.raises(ValidationError):
            br.title = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        br = BriefingReport(
            title="Test",
            executive_summary="Summary",
            sections={"a": "b"},
            recommendations=["c"],
        )
        data = br.model_dump()
        br2 = BriefingReport.model_validate(data)
        assert br == br2


# ---------------------------------------------------------------------------
# analyze_market — framework analysis
# ---------------------------------------------------------------------------


class TestAnalyzeMarket:
    """Tests for analyze_market tool."""

    def test_porter_framework(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "porter")
        assert result.framework == "porter"
        assert len(result.factors) == 5
        assert len(result.scores) == 5
        assert "supplier_power" in result.factors
        assert "rivalry" in result.factors

    def test_swot_framework(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "swot")
        assert result.framework == "swot"
        assert len(result.factors) == 4
        assert "strengths" in result.factors
        assert "weaknesses" in result.factors

    def test_pestel_framework(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "pestel")
        assert result.framework == "pestel"
        assert len(result.factors) == 6
        assert "political" in result.factors
        assert "technological" in result.factors

    def test_unsupported_framework(self) -> None:
        with pytest.raises(ValueError, match="Unsupported framework"):
            analyze_market("data", "invalid")

    def test_empty_data(self) -> None:
        result = analyze_market("", "porter")
        assert result.factors == {}
        assert "无数据" in result.insights[0]

    def test_scores_range(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "porter")
        for score in result.scores.values():
            assert 1 <= score <= 5

    def test_insights_generated(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "porter")
        assert len(result.insights) > 0

    def test_porter_rivalry_high(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "porter")
        # Data mentions competition and price wars
        assert result.scores.get("rivalry", 0) >= 3

    def test_framework_case_insensitive(self) -> None:
        result = analyze_market(MARKET_DATA_CN, "PORTER")
        assert result.framework == "porter"

    def test_english_data(self) -> None:
        result = analyze_market(MARKET_DATA_EN, "porter")
        assert len(result.factors) == 5


class TestComputeScore:
    """Tests for _compute_score helper."""

    def test_high_hits(self) -> None:
        assert _compute_score(8, 10) == 5

    def test_medium_hits(self) -> None:
        assert _compute_score(4, 10) == 4  # ratio=0.4 >= 0.4 threshold

    def test_low_hits(self) -> None:
        assert _compute_score(1, 10) == 2

    def test_zero_hits(self) -> None:
        assert _compute_score(0, 10) == 1

    def test_zero_total(self) -> None:
        assert _compute_score(0, 0) == 3


class TestCountKeywordHits:
    """Tests for _count_keyword_hits helper."""

    def test_matches(self) -> None:
        assert _count_keyword_hits("AI is growing", ["AI", "ML"]) == 1

    def test_no_matches(self) -> None:
        assert _count_keyword_hits("Hello world", ["AI"]) == 0

    def test_case_insensitive(self) -> None:
        assert _count_keyword_hits("ai technology", ["AI"]) == 1

    def test_multiple(self) -> None:
        assert _count_keyword_hits("AI and ML grow", ["AI", "ML"]) == 2


class TestGenerateInsights:
    """Tests for _generate_insights helper."""

    def test_porter_high_rivalry(self) -> None:
        insights = _generate_insights("porter", {"rivalry": 5})
        assert any("竞争" in i for i in insights)

    def test_swot_high_strengths(self) -> None:
        insights = _generate_insights("swot", {"strengths": 5})
        assert any("优势" in i for i in insights)

    def test_pestel_high_tech(self) -> None:
        insights = _generate_insights("pestel", {"technological": 5})
        assert any("技术" in i for i in insights)

    def test_no_high_scores(self) -> None:
        insights = _generate_insights("porter", {"rivalry": 1})
        assert len(insights) > 0


# ---------------------------------------------------------------------------
# identify_trends — trend detection
# ---------------------------------------------------------------------------


class TestIdentifyTrends:
    """Tests for identify_trends tool."""

    def test_detects_ai_trend(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        names = {t.name for t in result.trends}
        assert any("AI" in n or "技术" in n for n in names)

    def test_detects_digital_trend(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        names = {t.name for t in result.trends}
        assert any("数字化" in n or "Digital" in n for n in names)

    def test_detects_esg_trend(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        names = {t.name for t in result.trends}
        assert any("ESG" in n or "可持续" in n for n in names)

    def test_detects_regulation_trend(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        names = {t.name for t in result.trends}
        assert any("监管" in n or "Regulation" in n for n in names)

    def test_empty_data(self) -> None:
        result = identify_trends("")
        assert result.trends == []
        assert result.confidence == 0.0

    def test_no_signals(self) -> None:
        result = identify_trends(MINIMAL_DATA)
        assert result.trends == []
        assert "未检测到" in result.summary or len(result.trends) == 0

    def test_trends_have_evidence(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        for trend in result.trends:
            assert trend.evidence != ""

    def test_trends_have_timeframe(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        for trend in result.trends:
            assert trend.timeframe != ""

    def test_confidence_non_zero(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        assert result.confidence > 0.0

    def test_summary_generated(self) -> None:
        result = identify_trends(MARKET_DATA_CN)
        assert result.summary != ""

    def test_english_data(self) -> None:
        result = identify_trends(MARKET_DATA_EN)
        assert len(result.trends) >= 2


class TestComputeConfidence:
    """Tests for _compute_confidence helper."""

    def test_no_trends(self) -> None:
        assert _compute_confidence([], 100) == 0.0

    def test_few_trends(self) -> None:
        trends = [TrendItem(name="a"), TrendItem(name="b")]
        conf = _compute_confidence(trends, 500)
        assert 0.0 < conf <= 1.0

    def test_many_trends(self) -> None:
        trends = [TrendItem(name=f"t{i}") for i in range(10)]
        conf = _compute_confidence(trends, 1000)
        assert conf >= 0.5


class TestGenerateSummary:
    """Tests for _generate_summary helper."""

    def test_no_trends(self) -> None:
        assert "未检测到" in _generate_summary([])

    def test_with_trends(self) -> None:
        trends = [
            TrendItem(name="a", direction="up"),
            TrendItem(name="b", direction="down"),
        ]
        summary = _generate_summary(trends)
        assert "2" in summary
        assert "上升" in summary
        assert "下降" in summary


# ---------------------------------------------------------------------------
# generate_briefing — report synthesis
# ---------------------------------------------------------------------------


class TestGenerateBriefing:
    """Tests for generate_briefing tool."""

    def test_porter_briefing(self) -> None:
        analysis = MarketAnalysis(
            framework="porter",
            factors={"rivalry": "High competition"},
            scores={"rivalry": 5},
            insights=["Intense competition"],
        )
        result = generate_briefing(analysis)
        assert "Porter" in result.title
        assert result.executive_summary != ""
        assert "rivalry" in result.sections
        assert len(result.recommendations) > 0

    def test_swot_briefing(self) -> None:
        analysis = MarketAnalysis(
            framework="swot",
            factors={"strengths": "Strong brand"},
            scores={"strengths": 5, "weaknesses": 2},
            insights=["Competitive advantage"],
        )
        result = generate_briefing(analysis)
        assert "SWOT" in result.title
        assert "strengths" in result.sections

    def test_pestel_briefing(self) -> None:
        analysis = MarketAnalysis(
            framework="pestel",
            factors={"technological": "AI growth"},
            scores={"technological": 5},
            insights=["Tech driven"],
        )
        result = generate_briefing(analysis)
        assert "PESTEL" in result.title

    def test_empty_insights(self) -> None:
        analysis = MarketAnalysis(framework="porter")
        result = generate_briefing(analysis)
        assert result.executive_summary != ""
        assert result.recommendations  # Should have default recommendation

    def test_recommendations_porter_high_rivalry(self) -> None:
        analysis = MarketAnalysis(
            framework="porter",
            factors={"rivalry": "High"},
            scores={"rivalry": 5},
            insights=["Competition"],
        )
        result = generate_briefing(analysis)
        assert any("差异化" in r for r in result.recommendations)

    def test_recommendations_swot_high_strengths(self) -> None:
        analysis = MarketAnalysis(
            framework="swot",
            factors={"strengths": "Strong"},
            scores={"strengths": 5},
            insights=["Advantage"],
        )
        result = generate_briefing(analysis)
        assert any("扩张" in r for r in result.recommendations)


# ---------------------------------------------------------------------------
# Agent — three-phase pipeline
# ---------------------------------------------------------------------------


class TestMarketIntelligenceAgent:
    """Tests for MarketIntelligenceAgent class."""

    def test_analyze_market(self, agent: MarketIntelligenceAgent) -> None:
        result = agent.analyze_market(MARKET_DATA_CN, "porter")
        assert isinstance(result, MarketAnalysis)
        assert result.framework == "porter"

    def test_identify_trends(self, agent: MarketIntelligenceAgent) -> None:
        result = agent.identify_trends(MARKET_DATA_CN)
        assert isinstance(result, TrendReport)

    def test_generate_briefing(self, agent: MarketIntelligenceAgent) -> None:
        analysis = agent.analyze_market(MARKET_DATA_CN, "swot")
        result = agent.generate_briefing(analysis)
        assert isinstance(result, BriefingReport)

    def test_full_pipeline(self, agent: MarketIntelligenceAgent) -> None:
        # Phase 1: analyze
        analysis = agent.analyze_market(MARKET_DATA_EN, "porter")
        assert len(analysis.factors) == 5

        # Phase 2: trends
        trends = agent.identify_trends(MARKET_DATA_EN)
        assert isinstance(trends, TrendReport)

        # Phase 3: briefing
        briefing = agent.generate_briefing(analysis)
        assert briefing.title != ""
        assert briefing.executive_summary != ""

    def test_all_frameworks(self, agent: MarketIntelligenceAgent) -> None:
        for fw in ["porter", "swot", "pestel"]:
            result = agent.analyze_market(MARKET_DATA_CN, fw)
            assert result.framework == fw
            assert len(result.factors) > 0


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_market_intelligence_analyst.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_market_intelligence_analyst.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze_market(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "analyze_market",
                "params": {"data": MARKET_DATA_CN, "framework": "porter"},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["framework"] == "porter"

    def test_handle_identify_trends(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(
            agent,
            {"method": "identify_trends", "params": {"data": MARKET_DATA_CN}},
        )
        assert response["status"] == "ok"
        assert "trends" in response["result"]

    def test_handle_generate_briefing(self, agent: MarketIntelligenceAgent) -> None:
        # First get analysis
        analysis_resp = handle_message(
            agent,
            {
                "method": "analyze_market",
                "params": {"data": MARKET_DATA_CN, "framework": "swot"},
            },
        )
        analysis_data = analysis_resp["result"]

        response = handle_message(
            agent,
            {"method": "generate_briefing", "params": {"analysis": analysis_data}},
        )
        assert response["status"] == "ok"
        assert response["result"]["title"] != ""

    def test_handle_unknown_method(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_data(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(agent, {"method": "analyze_market", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_missing_analysis(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(agent, {"method": "generate_briefing", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_invalid_framework(self, agent: MarketIntelligenceAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "analyze_market",
                "params": {"data": "some data", "framework": "invalid"},
            },
        )
        assert response["status"] == "error"
        assert "Unsupported" in response["error"]
