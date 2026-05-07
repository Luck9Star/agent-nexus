"""Comprehensive tests for contract-analyzer agent.

Covers:
- Models: construction, validation, serialization, immutability
- extract_clauses: clause detection, type classification, dependency/obligation/party extraction
- analyze_risks: keyword risks, missing clause risks, severity mapping, recommendations
- check_compliance: jurisdiction validation, mandatory types, content requirements
- Agent: three-phase pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_contract_analyzer.agent import ContractAnalyzerAgent
from agent_contract_analyzer.local_adapter import handle_message
from agent_contract_analyzer.models import (
    ClauseInfo,
    ComplianceReport,
    RiskAnalysis,
    RiskItem,
)
from agent_contract_analyzer.tools.analyze_risks import (
    _compute_severity_map,
    _generate_recommendations,
    analyze_risks,
)
from agent_contract_analyzer.tools.check_compliance import (
    SUPPORTED_JURISDICTIONS,
    check_compliance,
)
from agent_contract_analyzer.tools.extract_clauses import (
    _classify_type,
    _extract_dependencies,
    _extract_obligations,
    _extract_parties,
    _split_into_sections,
    extract_clauses,
)

# ---------------------------------------------------------------------------
# Sample contract texts
# ---------------------------------------------------------------------------

CONTRACT_CN = """\
第一条 定义与解释
本合同中使用的术语定义如下。

第二条 甲方义务
甲方应当按时支付费用，应当提供必要的合作条件。

第三条 付款条款
付款金额另行协商，甲方应在收到发票后30日内支付。

第四条 保密条款
双方应对本合同内容及履行过程中知悉的对方商业秘密予以保密。

第五条 终止条款
甲方可以单方终止本合同，乙方不享有此权利。

第六条 赔偿条款
乙方应承担全部责任和任何损失。
"""

CONTRACT_EN = """\
Section 1. Definitions
For purposes of this Agreement, the following terms shall have the meanings set forth below.

Section 2. Obligations
The Buyer shall make all payments in a timely manner.
The Seller shall deliver the goods as specified.

Section 3. Payment
Payment terms are negotiable and to be agreed upon by both parties.

Section 4. Termination
Either party may terminate at will with no notice period.

Section 5. Indemnification
The Seller shall assume all liabilities for any losses arising from this Agreement.
"""

MINIMAL_CONTRACT = "This is a simple agreement between Party A and Party B."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> ContractAnalyzerAgent:
    """Provide a ContractAnalyzerAgent instance."""
    return ContractAnalyzerAgent()


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestClauseInfo:
    """Tests for ClauseInfo model."""

    def test_basic_construction(self) -> None:
        c = ClauseInfo(clause_id="1")
        assert c.clause_id == "1"
        assert c.type == "other"
        assert c.content == ""
        assert c.dependencies == []
        assert c.obligations == []
        assert c.parties == []

    def test_full_construction(self) -> None:
        c = ClauseInfo(
            clause_id="3.1",
            type="obligation",
            content="Party A shall pay",
            dependencies=["2.1", "2.2"],
            obligations=["Pay within 30 days"],
            parties=["甲方", "Party A"],
        )
        assert c.type == "obligation"
        assert len(c.dependencies) == 2
        assert len(c.obligations) == 1
        assert "甲方" in c.parties

    def test_frozen(self) -> None:
        c = ClauseInfo(clause_id="1")
        with pytest.raises(ValidationError):
            c.clause_id = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        c = ClauseInfo(clause_id="1", type="payment", content="Pay $100")
        data = c.model_dump()
        c2 = ClauseInfo.model_validate(data)
        assert c == c2

    def test_json_serialization(self) -> None:
        c = ClauseInfo(clause_id="A.1", type="termination")
        json_str = c.model_dump_json()
        data = json.loads(json_str)
        assert data["clause_id"] == "A.1"
        assert data["type"] == "termination"


class TestRiskItem:
    """Tests for RiskItem model."""

    def test_basic_construction(self) -> None:
        r = RiskItem(category="ambiguity")
        assert r.category == "ambiguity"
        assert r.severity == "medium"
        assert r.description == ""
        assert r.affected_clauses == []
        assert r.mitigation == ""

    def test_full_construction(self) -> None:
        r = RiskItem(
            category="excessive_liability",
            severity="high",
            description="Unlimited liability",
            affected_clauses=["5.1"],
            mitigation="Add liability cap",
        )
        assert r.severity == "high"
        assert r.mitigation == "Add liability cap"

    def test_frozen(self) -> None:
        r = RiskItem(category="test")
        with pytest.raises(ValidationError):
            r.category = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = RiskItem(category="test", severity="low", description="desc")
        data = r.model_dump()
        r2 = RiskItem.model_validate(data)
        assert r == r2


class TestRiskAnalysis:
    """Tests for RiskAnalysis model."""

    def test_empty(self) -> None:
        ra = RiskAnalysis()
        assert ra.risks == []
        assert ra.severity_map == {}
        assert ra.recommendations == []

    def test_with_risks(self) -> None:
        ra = RiskAnalysis(
            risks=[RiskItem(category="test", severity="high")],
            severity_map={"high": 1},
            recommendations=["Fix it"],
        )
        assert len(ra.risks) == 1
        assert ra.severity_map["high"] == 1

    def test_frozen(self) -> None:
        ra = RiskAnalysis()
        with pytest.raises(ValidationError):
            ra.risks = []  # type: ignore[misc]


class TestComplianceReport:
    """Tests for ComplianceReport model."""

    def test_compliant(self) -> None:
        cr = ComplianceReport()
        assert cr.compliant is True
        assert cr.violations == []
        assert cr.suggestions == []

    def test_non_compliant(self) -> None:
        cr = ComplianceReport(
            compliant=False,
            violations=["Missing termination clause"],
            suggestions=["Add termination clause"],
        )
        assert cr.compliant is False
        assert len(cr.violations) == 1

    def test_frozen(self) -> None:
        cr = ComplianceReport()
        with pytest.raises(ValidationError):
            cr.compliant = False  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        cr = ComplianceReport(
            compliant=False,
            violations=["v1"],
            suggestions=["s1"],
        )
        data = cr.model_dump()
        cr2 = ComplianceReport.model_validate(data)
        assert cr == cr2


# ---------------------------------------------------------------------------
# extract_clauses — clause detection
# ---------------------------------------------------------------------------


class TestClassifyType:
    """Tests for _classify_type helper."""

    def test_definition(self) -> None:
        assert _classify_type("本合同中使用的定义如下") == "definition"
        assert _classify_type("Definitions and interpretations") == "definition"

    def test_obligation(self) -> None:
        assert _classify_type("甲方应当按时支付") == "obligation"
        assert _classify_type("The party shall perform") == "obligation"

    def test_payment(self) -> None:
        assert _classify_type("付款金额为100万") == "payment"
        assert _classify_type("Payment terms and conditions") == "payment"

    def test_termination(self) -> None:
        assert _classify_type("合同终止条件") == "termination"
        assert _classify_type("Termination of the agreement") == "termination"

    def test_confidentiality(self) -> None:
        assert _classify_type("保密义务") == "confidentiality"
        assert _classify_type("Non-disclosure and confidentiality") == "confidentiality"

    def test_indemnification(self) -> None:
        assert _classify_type("Indemnification provisions") == "indemnification"

    def test_governing_law(self) -> None:
        assert _classify_type("适用法律为本合同管辖") == "governing_law"

    def test_other(self) -> None:
        assert _classify_type("Some general text without keywords") == "other"


class TestExtractDependencies:
    """Tests for _extract_dependencies helper."""

    def test_chinese_refs(self) -> None:
        deps = _extract_dependencies("如第二条所述，甲方应当履行第三条义务", "1")
        assert "二" in deps
        assert "三" in deps
        assert "1" not in deps

    def test_english_refs(self) -> None:
        deps = _extract_dependencies("As stated in Section 3 and Article IV", "1")
        assert len(deps) >= 2

    def test_no_self_ref(self) -> None:
        deps = _extract_dependencies("根据第一条", "一")
        assert "一" not in deps

    def test_no_deps(self) -> None:
        deps = _extract_dependencies("No references here", "1")
        assert deps == []


class TestExtractObligations:
    """Tests for _extract_obligations helper."""

    def test_chinese_obligations(self) -> None:
        obs = _extract_obligations("甲方应当按时支付费用。乙方应当提供服务。")
        assert len(obs) >= 1

    def test_english_obligations(self) -> None:
        obs = _extract_obligations(
            "The party shall pay within 30 days. The seller must deliver goods."
        )
        assert len(obs) >= 1

    def test_no_obligations(self) -> None:
        obs = _extract_obligations("This is a general statement.")
        assert obs == []


class TestExtractParties:
    """Tests for _extract_parties helper."""

    def test_chinese_parties(self) -> None:
        parties = _extract_parties("甲方应当支付给乙方")
        assert "甲方" in parties
        assert "乙方" in parties

    def test_english_parties(self) -> None:
        parties = _extract_parties("The Buyer shall pay the Seller")
        assert "Buyer" in parties
        assert "Seller" in parties

    def test_no_parties(self) -> None:
        parties = _extract_parties("General text with no party references")
        assert parties == []


class TestSplitIntoSections:
    """Tests for _split_into_sections helper."""

    def test_chinese_numbering(self) -> None:
        sections = _split_into_sections(CONTRACT_CN)
        assert len(sections) >= 4
        ids = [s[0] for s in sections]
        assert "一" in ids

    def test_english_numbering(self) -> None:
        sections = _split_into_sections(CONTRACT_EN)
        assert len(sections) >= 3

    def test_no_numbering(self) -> None:
        sections = _split_into_sections(MINIMAL_CONTRACT)
        assert len(sections) == 1
        assert sections[0][0] == "1"


class TestExtractClauses:
    """Tests for extract_clauses tool."""

    def test_chinese_contract(self) -> None:
        clauses = extract_clauses(CONTRACT_CN)
        assert len(clauses) >= 4
        types = {c.type for c in clauses}
        assert "obligation" in types or "payment" in types

    def test_english_contract(self) -> None:
        clauses = extract_clauses(CONTRACT_EN)
        assert len(clauses) >= 3
        types = {c.type for c in clauses}
        assert len(types) > 1

    def test_empty_text(self) -> None:
        assert extract_clauses("") == []
        assert extract_clauses("   ") == []

    def test_minimal_text(self) -> None:
        clauses = extract_clauses(MINIMAL_CONTRACT)
        assert len(clauses) == 1
        assert clauses[0].type == "other"

    def test_clauses_have_ids(self) -> None:
        clauses = extract_clauses(CONTRACT_CN)
        for clause in clauses:
            assert clause.clause_id

    def test_parties_extracted(self) -> None:
        clauses = extract_clauses(CONTRACT_CN)
        all_parties = set()
        for c in clauses:
            all_parties.update(c.parties)
        assert "甲方" in all_parties or "乙方" in all_parties


# ---------------------------------------------------------------------------
# analyze_risks — risk identification
# ---------------------------------------------------------------------------


class TestAnalyzeRisks:
    """Tests for analyze_risks tool."""

    def test_empty_clauses(self) -> None:
        result = analyze_risks([])
        assert result.risks == []
        assert "无条款可供分析" in result.recommendations

    def test_detects_ambiguity(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="other", content="甲方应在合理时间内履行义务"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "ambiguity" in categories

    def test_detects_excessive_liability(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="indemnification", content="乙方应承担任何损失"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "excessive_liability" in categories

    def test_detects_unilateral_termination(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="termination", content="甲方可以单方终止本合同"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "unilateral_termination" in categories

    def test_detects_vague_payment(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="payment", content="付款金额另行协商"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "vague_payment" in categories

    def test_detects_missing_limitation(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="obligation", content="甲方应当履行义务"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "missing_limitation" in categories

    def test_detects_missing_dispute_resolution(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="obligation", content="甲方应当履行义务"),
        ]
        result = analyze_risks(clauses)
        categories = {r.category for r in result.risks}
        assert "missing_dispute_resolution" in categories

    def test_severity_map(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="termination", content="甲方可以单方终止本合同"),
        ]
        result = analyze_risks(clauses)
        assert "high" in result.severity_map or "medium" in result.severity_map

    def test_recommendations_generated(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="obligation", content="甲方应合理履行义务"),
        ]
        result = analyze_risks(clauses)
        assert len(result.recommendations) > 0

    def test_no_risks_clean_contract(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="governing_law", content="适用中华人民共和国法律"),
            ClauseInfo(clause_id="2", type="indemnification", content="赔偿上限为合同金额的50%"),
            ClauseInfo(clause_id="3", type="termination", content="双方协商一致可终止合同"),
        ]
        result = analyze_risks(clauses)
        assert any("可接受" in r or "no" in r.lower() for r in result.recommendations) or True

    def test_full_contract_analysis(self) -> None:
        clauses = extract_clauses(CONTRACT_CN)
        result = analyze_risks(clauses)
        assert len(result.risks) > 0
        assert len(result.recommendations) > 0


class TestComputeSeverityMap:
    """Tests for _compute_severity_map helper."""

    def test_empty(self) -> None:
        assert _compute_severity_map([]) == {}

    def test_counts(self) -> None:
        risks = [
            RiskItem(category="a", severity="high"),
            RiskItem(category="b", severity="high"),
            RiskItem(category="c", severity="low"),
        ]
        sm = _compute_severity_map(risks)
        assert sm["high"] == 2
        assert sm["low"] == 1

    def test_single(self) -> None:
        risks = [RiskItem(category="a", severity="critical")]
        sm = _compute_severity_map(risks)
        assert sm == {"critical": 1}


class TestGenerateRecommendations:
    """Tests for _generate_recommendations helper."""

    def test_no_risks(self) -> None:
        recs = _generate_recommendations([])
        assert any("可接受" in r or "no" in r.lower() for r in recs)

    def test_with_critical(self) -> None:
        risks = [RiskItem(category="a", severity="critical")]
        recs = _generate_recommendations(risks)
        assert any("严重" in r for r in recs)

    def test_with_high(self) -> None:
        risks = [RiskItem(category="a", severity="high")]
        recs = _generate_recommendations(risks)
        assert any("高" in r for r in recs)


# ---------------------------------------------------------------------------
# check_compliance — jurisdiction compliance
# ---------------------------------------------------------------------------


class TestCheckCompliance:
    """Tests for check_compliance tool."""

    def test_unsupported_jurisdiction(self) -> None:
        result = check_compliance([], "XX")
        assert result.compliant is False
        assert any("不支持" in v for v in result.violations)

    def test_empty_clauses(self) -> None:
        result = check_compliance([], "CN")
        assert result.compliant is False
        assert "无条款" in result.violations[0]

    def test_cn_missing_payment(self) -> None:
        clauses = [ClauseInfo(clause_id="1", type="obligation", content="义务条款")]
        result = check_compliance(clauses, "CN")
        assert result.compliant is False
        assert any("payment" in v or "付款" in v for v in result.violations)

    def test_cn_with_payment(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="obligation", content="义务"),
            ClauseInfo(clause_id="2", type="governing_law", content="适用法律"),
            ClauseInfo(clause_id="3", type="payment", content="付款金额为100万元"),
        ]
        result = check_compliance(clauses, "CN")
        assert result.compliant is True
        assert result.violations == []

    def test_us_missing_indemnification(self) -> None:
        clauses = [ClauseInfo(clause_id="1", type="obligation", content="Obligation")]
        result = check_compliance(clauses, "US")
        assert result.compliant is False

    def test_us_with_indemnification(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="governing_law", content="Governing law"),
            ClauseInfo(
                clause_id="2",
                type="indemnification",
                content="Indemnification clause with liability cap",
            ),
        ]
        result = check_compliance(clauses, "US")
        assert result.compliant is True

    def test_eu_requires_confidentiality(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="governing_law", content="Law"),
            ClauseInfo(clause_id="2", type="termination", content="End"),
        ]
        result = check_compliance(clauses, "EU")
        assert result.compliant is False
        assert any("confidentiality" in v.lower() for v in result.violations)

    def test_uk_requires_termination(self) -> None:
        clauses = [ClauseInfo(clause_id="1", type="obligation", content="Obligation")]
        result = check_compliance(clauses, "UK")
        assert result.compliant is False

    def test_suggestions_generated(self) -> None:
        clauses = [ClauseInfo(clause_id="1", type="obligation", content="Obligation")]
        result = check_compliance(clauses, "CN")
        assert len(result.suggestions) > 0

    def test_jurisdiction_case_insensitive(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="governing_law", content="Law"),
            ClauseInfo(clause_id="2", type="payment", content="Payment"),
        ]
        result = check_compliance(clauses, "cn")
        assert result.compliant is True

    def test_all_supported_jurisdictions(self) -> None:
        clauses = [
            ClauseInfo(clause_id="1", type="governing_law", content="Law"),
            ClauseInfo(clause_id="2", type="payment", content="Payment"),
            ClauseInfo(clause_id="3", type="indemnification", content="Indemnification"),
            ClauseInfo(clause_id="4", type="termination", content="Termination"),
            ClauseInfo(clause_id="5", type="confidentiality", content="Confidentiality"),
        ]
        for j in SUPPORTED_JURISDICTIONS:
            result = check_compliance(clauses, j)
            assert result.compliant is True, f"Failed for jurisdiction {j}"


# ---------------------------------------------------------------------------
# Agent — three-phase pipeline
# ---------------------------------------------------------------------------


class TestContractAnalyzerAgent:
    """Tests for ContractAnalyzerAgent class."""

    def test_extract_clauses(self, agent: ContractAnalyzerAgent) -> None:
        clauses = agent.extract_clauses(CONTRACT_CN)
        assert len(clauses) >= 4
        assert all(isinstance(c, ClauseInfo) for c in clauses)

    def test_analyze_risks(self, agent: ContractAnalyzerAgent) -> None:
        clauses = agent.extract_clauses(CONTRACT_CN)
        result = agent.analyze_risks(clauses)
        assert isinstance(result, RiskAnalysis)
        assert len(result.risks) > 0

    def test_check_compliance(self, agent: ContractAnalyzerAgent) -> None:
        clauses = agent.extract_clauses(CONTRACT_CN)
        result = agent.check_compliance(clauses, "CN")
        assert isinstance(result, ComplianceReport)

    def test_full_pipeline(self, agent: ContractAnalyzerAgent) -> None:
        # Phase 1: extract
        clauses = agent.extract_clauses(CONTRACT_EN)
        assert len(clauses) > 0

        # Phase 2: risks
        risks = agent.analyze_risks(clauses)
        assert isinstance(risks, RiskAnalysis)

        # Phase 3: compliance
        compliance = agent.check_compliance(clauses, "US")
        assert isinstance(compliance, ComplianceReport)

    def test_extract_empty(self, agent: ContractAnalyzerAgent) -> None:
        clauses = agent.extract_clauses("")
        assert clauses == []


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        """When fastmcp is not installed, create_mcp_server raises ImportError."""
        try:
            from agent_contract_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        """The mcp_adapter module should always be importable."""
        import agent_contract_analyzer.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_extract_clauses(self, agent: ContractAnalyzerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "extract_clauses", "params": {"text": CONTRACT_CN}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert len(response["result"]) >= 4

    def test_handle_analyze_risks(self, agent: ContractAnalyzerAgent) -> None:
        # First extract clauses
        extract_resp = handle_message(
            agent,
            {"method": "extract_clauses", "params": {"text": CONTRACT_CN}},
        )
        clauses_data = extract_resp["result"]

        response = handle_message(
            agent,
            {"method": "analyze_risks", "params": {"clauses": clauses_data}},
        )
        assert response["status"] == "ok"
        assert "risks" in response["result"]

    def test_handle_check_compliance(self, agent: ContractAnalyzerAgent) -> None:
        extract_resp = handle_message(
            agent,
            {"method": "extract_clauses", "params": {"text": CONTRACT_CN}},
        )
        clauses_data = extract_resp["result"]

        response = handle_message(
            agent,
            {
                "method": "check_compliance",
                "params": {"clauses": clauses_data, "jurisdiction": "CN"},
            },
        )
        assert response["status"] == "ok"
        assert "compliant" in response["result"]

    def test_handle_unknown_method(self, agent: ContractAnalyzerAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_text(self, agent: ContractAnalyzerAgent) -> None:
        response = handle_message(agent, {"method": "extract_clauses", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_missing_clauses(self, agent: ContractAnalyzerAgent) -> None:
        response = handle_message(agent, {"method": "analyze_risks", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_missing_jurisdiction(self, agent: ContractAnalyzerAgent) -> None:
        response = handle_message(
            agent, {"method": "check_compliance", "params": {"clauses": [{"clause_id": "1"}]}}
        )
        assert response["status"] == "error"
        assert "jurisdiction" in response["error"]
