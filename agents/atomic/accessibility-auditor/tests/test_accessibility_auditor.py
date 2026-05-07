"""Comprehensive tests for accessibility-auditor agent.

Covers:
- Models: construction, validation, serialization, immutability
- audit_content: HTML auditing, text auditing, compliance scoring
- check_html: images, forms, headings, links, language, ARIA, tables
- generate_remediation: priority ordering, effort estimation
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest

from agent_accessibility_auditor.agent import AccessibilityAuditorAgent
from agent_accessibility_auditor.local_adapter import handle_message
from agent_accessibility_auditor.models import (
    AccessibilityIssue,
    AuditResult,
    RemediationPlan,
)
from agent_accessibility_auditor.tools.audit_content import (
    _check_aria,
    _check_forms,
    _check_headings,
    _check_images,
    _check_language,
    _check_links,
    _check_tables,
    _compute_compliance,
    audit_content,
)
from agent_accessibility_auditor.tools.check_html import check_html
from agent_accessibility_auditor.tools.generate_remediation import (
    _categorize_issue,
    generate_remediation,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> AccessibilityAuditorAgent:
    """Provide an AccessibilityAuditorAgent instance."""
    return AccessibilityAuditorAgent()


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestAccessibilityIssue:
    """Tests for AccessibilityIssue model."""

    def test_basic_construction(self) -> None:
        i = AccessibilityIssue(criterion="1.1.1", level="A")
        assert i.criterion == "1.1.1"
        assert i.level == "A"
        assert i.element == ""
        assert i.description == ""
        assert i.fix_suggestion == ""

    def test_full_construction(self) -> None:
        i = AccessibilityIssue(
            criterion="2.4.3",
            level="A",
            element="<a>",
            description="Focus order issue",
            fix_suggestion="Add tabindex attribute",
        )
        assert i.element == "<a>"
        assert i.fix_suggestion == "Add tabindex attribute"

    def test_frozen(self) -> None:
        i = AccessibilityIssue(criterion="1.1.1")
        with pytest.raises(Exception):
            i.criterion = "2.0"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        i = AccessibilityIssue(criterion="1.3.1", level="A", element="form")
        data = i.model_dump()
        i2 = AccessibilityIssue.model_validate(data)
        assert i == i2

    def test_json_serialization(self) -> None:
        i = AccessibilityIssue(criterion="4.1.2", level="AA")
        json_str = i.model_dump_json()
        data = json.loads(json_str)
        assert data["criterion"] == "4.1.2"


class TestAuditResult:
    """Tests for AuditResult model."""

    def test_empty(self) -> None:
        r = AuditResult()
        assert r.issues == []
        assert r.compliance_score == 100.0
        assert r.wcag_level == "AA"

    def test_with_issues(self) -> None:
        i = AccessibilityIssue(criterion="1.1.1", level="A")
        r = AuditResult(issues=[i], compliance_score=80.0, wcag_level="None")
        assert len(r.issues) == 1
        assert r.compliance_score == 80.0

    def test_frozen(self) -> None:
        r = AuditResult()
        with pytest.raises(Exception):
            r.compliance_score = 50.0  # type: ignore[misc]


class TestRemediationPlan:
    """Tests for RemediationPlan model."""

    def test_empty(self) -> None:
        p = RemediationPlan()
        assert p.issues == []
        assert p.priority_order == []
        assert p.estimated_effort == "TBD"

    def test_with_data(self) -> None:
        i = AccessibilityIssue(criterion="1.1.1", level="A")
        p = RemediationPlan(issues=[i], priority_order=["1.1.1"], estimated_effort="1 hour")
        assert p.priority_order == ["1.1.1"]

    def test_frozen(self) -> None:
        p = RemediationPlan()
        with pytest.raises(Exception):
            p.estimated_effort = "2 hours"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# audit_content — HTML auditing
# ---------------------------------------------------------------------------


class TestAuditContent:
    """Tests for audit_content tool."""

    def test_empty_content(self) -> None:
        result = audit_content("", "html")
        assert result.compliance_score == 100.0
        assert result.issues == []

    def test_perfect_html(self) -> None:
        html = (
            '<html lang="en">'
            "<head><title>Test</title></head>"
            "<body>"
            "<h1>Main Heading</h1>"
            '<p>Paragraph with <img src="photo.jpg" alt="A photo"> content.</p>'
            '<label for="name">Name</label><input id="name" type="text">'
            '<a href="/about">About our company</a>'
            "</body></html>"
        )
        result = audit_content(html, "html")
        assert result.compliance_score > 0

    def test_text_content(self) -> None:
        result = audit_content("Hello world paragraph.", "text")
        assert isinstance(result, AuditResult)

    def test_returns_audit_result(self) -> None:
        result = audit_content("<html><body></body></html>", "html")
        assert isinstance(result, AuditResult)


class TestCheckImages:
    """Tests for _check_images helper."""

    def test_img_without_alt(self) -> None:
        issues = _check_images('<img src="photo.jpg">')
        assert len(issues) >= 1
        assert any(i.criterion == "1.1.1" for i in issues)

    def test_img_with_alt(self) -> None:
        issues = _check_images('<img src="photo.jpg" alt="A beautiful photo">')
        assert not any("missing alt" in i.description.lower() for i in issues)

    def test_img_empty_alt_no_role(self) -> None:
        issues = _check_images('<img src="spacer.gif" alt="">')
        assert any("empty alt" in i.description.lower() for i in issues)

    def test_img_empty_alt_with_role(self) -> None:
        issues = _check_images('<img src="spacer.gif" alt="" role="presentation">')
        assert not any("empty alt" in i.description.lower() for i in issues)

    def test_no_images(self) -> None:
        issues = _check_images("<p>No images here</p>")
        assert issues == []


class TestCheckForms:
    """Tests for _check_forms helper."""

    def test_input_without_label(self) -> None:
        issues = _check_forms('<input type="text" id="email">')
        assert len(issues) >= 1
        assert any(i.criterion == "1.3.1" for i in issues)

    def test_input_with_label(self) -> None:
        html = '<label for="email">Email</label><input type="text" id="email">'
        issues = _check_forms(html)
        assert not any(i.criterion == "1.3.1" for i in issues)

    def test_input_with_aria_label(self) -> None:
        issues = _check_forms('<input type="text" aria-label="Search">')
        assert not any(i.criterion == "1.3.1" for i in issues)

    def test_hidden_input_skipped(self) -> None:
        issues = _check_forms('<input type="hidden" name="token">')
        assert not any(i.criterion == "1.3.1" for i in issues)

    def test_submit_input_skipped(self) -> None:
        issues = _check_forms('<input type="submit" value="Go">')
        assert not any(i.criterion == "1.3.1" for i in issues)


class TestCheckHeadings:
    """Tests for _check_headings helper."""

    def test_heading_skip(self) -> None:
        issues = _check_headings("<h1>Title</h1><h3>Subtitle</h3>")
        assert any("skipped" in i.description.lower() for i in issues)

    def test_sequential_headings(self) -> None:
        issues = _check_headings("<h1>Title</h1><h2>Section</h2><h3>Sub</h3>")
        assert not any("skipped" in i.description.lower() for i in issues)

    def test_missing_h1(self) -> None:
        issues = _check_headings("<h2>No h1</h2>")
        assert any("h1" in i.description.lower() for i in issues)

    def test_has_h1(self) -> None:
        issues = _check_headings("<h1>Title</h1><h2>Sub</h2>")
        assert not any("missing h1" in i.description.lower() for i in issues)


class TestCheckLinks:
    """Tests for _check_links helper."""

    def test_link_no_text(self) -> None:
        issues = _check_links('<a href="/page"></a>')
        assert len(issues) >= 1

    def test_link_with_text(self) -> None:
        issues = _check_links('<a href="/about">About us</a>')
        assert not any("no accessible text" in i.description.lower() for i in issues)

    def test_ambiguous_link_text(self) -> None:
        issues = _check_links('<a href="/page">click here</a>')
        assert any("ambiguous" in i.description.lower() for i in issues)

    def test_good_link_text(self) -> None:
        issues = _check_links('<a href="/docs">Read the documentation</a>')
        assert not any("ambiguous" in i.description.lower() for i in issues)

    def test_link_with_aria_label(self) -> None:
        issues = _check_links('<a href="/page" aria-label="Navigate to settings"></a>')
        assert not any("no accessible text" in i.description.lower() for i in issues)


class TestCheckLanguage:
    """Tests for _check_language helper."""

    def test_missing_lang(self) -> None:
        issues = _check_language("<html><body></body></html>")
        assert any("lang" in i.description.lower() for i in issues)

    def test_has_lang(self) -> None:
        issues = _check_language('<html lang="en"><body></body></html>')
        assert not any("lang" in i.description.lower() for i in issues)

    def test_no_html_tag(self) -> None:
        issues = _check_language("<body>Fragment</body>")
        assert issues == []


class TestCheckAria:
    """Tests for _check_aria helper."""

    def test_button_role_no_tabindex(self) -> None:
        issues = _check_aria('<div role="button" onclick="do()">Click</div>')
        assert any("tabindex" in i.description.lower() for i in issues)

    def test_button_role_with_tabindex(self) -> None:
        issues = _check_aria('<div role="button" tabindex="0" onclick="do()">Click</div>')
        assert not any("tabindex" in i.description.lower() for i in issues)


class TestCheckTables:
    """Tests for _check_tables helper."""

    def test_table_no_headers(self) -> None:
        issues = _check_tables("<table><tr><td>Data</td></tr></table>")
        assert any("header" in i.description.lower() for i in issues)

    def test_table_with_headers(self) -> None:
        issues = _check_tables("<table><tr><th scope='col'>Name</th></tr><tr><td>Data</td></tr></table>")
        assert not any("header" in i.description.lower() for i in issues)


class TestComputeCompliance:
    """Tests for _compute_compliance helper."""

    def test_no_issues(self) -> None:
        score, level = _compute_compliance([])
        assert score == 100.0
        assert level == "AA"

    def test_level_a_issues(self) -> None:
        issues = [AccessibilityIssue(criterion="1.1.1", level="A")]
        score, level = _compute_compliance(issues)
        assert score < 100.0
        assert level == "None"

    def test_level_aa_only(self) -> None:
        issues = [AccessibilityIssue(criterion="1.4.3", level="AA")]
        score, level = _compute_compliance(issues)
        assert level == "A"


# ---------------------------------------------------------------------------
# check_html — HTML-specific checks
# ---------------------------------------------------------------------------


class TestCheckHtml:
    """Tests for check_html tool."""

    def test_empty_html(self) -> None:
        issues = check_html("")
        assert issues == []

    def test_problematic_html(self) -> None:
        html = (
            "<html>"
            "<body>"
            '<img src="photo.jpg">'
            "<h2>Skipped h1</h2>"
            '<input type="text">'
            '<a href="/page">click here</a>'
            "</body></html>"
        )
        issues = check_html(html)
        assert len(issues) >= 3

    def test_clean_html(self) -> None:
        html = (
            '<html lang="en">'
            "<body>"
            "<h1>Title</h1>"
            '<p>Text with <img src="x.png" alt="desc"></p>'
            '<label for="q">Search</label><input id="q" type="text">'
            '<a href="/page">Visit page</a>'
            "</body></html>"
        )
        issues = check_html(html)
        assert len(issues) == 0 or all(i.level == "AA" for i in issues)


# ---------------------------------------------------------------------------
# generate_remediation — priority ordering
# ---------------------------------------------------------------------------


class TestGenerateRemediation:
    """Tests for generate_remediation tool."""

    def test_empty_issues(self) -> None:
        plan = generate_remediation([])
        assert plan.issues == []
        assert plan.estimated_effort == "No issues to remediate"

    def test_priority_ordering(self) -> None:
        issues = [
            AccessibilityIssue(criterion="1.4.3", level="AA"),
            AccessibilityIssue(criterion="1.1.1", level="A"),
            AccessibilityIssue(criterion="2.4.6", level="AA"),
        ]
        plan = generate_remediation(issues)
        assert plan.issues[0].level == "A"
        assert plan.priority_order[0] == "1.1.1"

    def test_dict_issues(self) -> None:
        issues = [
            {"criterion": "1.1.1", "level": "A", "element": "img", "description": "Missing alt"},
        ]
        plan = generate_remediation(issues)
        assert len(plan.issues) == 1

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            generate_remediation(["not an issue"])

    def test_effort_estimation(self) -> None:
        issues = [
            AccessibilityIssue(criterion="1.1.1", level="A", element="img", description="Image alt"),
        ]
        plan = generate_remediation(issues)
        assert plan.estimated_effort != "TBD"

    def test_dedup_priority_order(self) -> None:
        issues = [
            AccessibilityIssue(criterion="1.1.1", level="A"),
            AccessibilityIssue(criterion="1.1.1", level="A"),
            AccessibilityIssue(criterion="1.3.1", level="A"),
        ]
        plan = generate_remediation(issues)
        assert plan.priority_order == ["1.1.1", "1.3.1"]


class TestCategorizeIssue:
    """Tests for _categorize_issue helper."""

    def test_image_category(self) -> None:
        i = AccessibilityIssue(criterion="1.1.1", level="A", element="img", description="alt text")
        assert _categorize_issue(i) == "images"

    def test_form_category(self) -> None:
        i = AccessibilityIssue(criterion="1.3.1", level="A", element="input", description="label")
        assert _categorize_issue(i) == "forms"

    def test_heading_category(self) -> None:
        i = AccessibilityIssue(criterion="1.3.1", level="A", element="h2", description="heading skip")
        assert _categorize_issue(i) == "headings"


# ---------------------------------------------------------------------------
# Agent — full pipeline
# ---------------------------------------------------------------------------


class TestAccessibilityAuditorAgent:
    """Tests for AccessibilityAuditorAgent class."""

    def test_audit_content(self, agent: AccessibilityAuditorAgent) -> None:
        result = agent.audit_content("<html><body></body></html>", "html")
        assert isinstance(result, AuditResult)

    def test_check_html(self, agent: AccessibilityAuditorAgent) -> None:
        issues = agent.check_html('<img src="x.jpg">')
        assert isinstance(issues, list)
        assert len(issues) >= 1

    def test_generate_remediation(self, agent: AccessibilityAuditorAgent) -> None:
        issues = [AccessibilityIssue(criterion="1.1.1", level="A")]
        result = agent.generate_remediation(issues)
        assert isinstance(result, RemediationPlan)

    def test_full_pipeline(self, agent: AccessibilityAuditorAgent) -> None:
        html = (
            "<html>"
            "<body>"
            "<h2>No h1</h2>"
            '<img src="photo.jpg">'
            '<input type="text">'
            "</body></html>"
        )
        audit = agent.audit_content(html, "html")
        assert audit.compliance_score < 100.0

        issues = agent.check_html(html)
        assert len(issues) >= 1

        plan = agent.generate_remediation(audit.issues)
        assert len(plan.priority_order) >= 1


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_accessibility_auditor.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_accessibility_auditor.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_audit(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "audit",
                "params": {
                    "content": "<html><body><img src='x.jpg'></body></html>",
                    "content_type": "html",
                },
            },
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_audit_missing_content(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(
            agent, {"method": "audit", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_check_html(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(
            agent,
            {"method": "check_html", "params": {"html": "<img src='x.jpg'>"}},
        )
        assert response["status"] == "ok"
        assert isinstance(response["result"], list)

    def test_handle_check_html_missing(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(
            agent, {"method": "check_html", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_remediation(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "remediation",
                "params": {
                    "issues": [
                        {"criterion": "1.1.1", "level": "A", "element": "img"}
                    ]
                },
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["priority_order"] == ["1.1.1"]

    def test_handle_unknown_method(self, agent: AccessibilityAuditorAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
