"""Comprehensive tests for doc-filler agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_template: placeholder detection, type inference, formatting extraction
- fill_template: basic filling, style preservation, unfilled tracking, error handling
- Agent: analyze->fill pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from agent_doc_filler.agent import DocFillerAgent
from agent_doc_filler.local_adapter import handle_message
from agent_doc_filler.models import (
    FillRequest,
    FillResult,
    PlaceholderInfo,
    TemplateAnalysis,
)
from agent_doc_filler.tools.analyze_template import (
    PLACEHOLDER_RE,
    _guess_field_type,
    analyze_template,
)
from agent_doc_filler.tools.fill_template import (
    _default_output_path,
    fill_template,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_docx_zip(
    dir_path: str,
    filename: str,
    content_xml: str | None = None,
) -> str:
    """Create a minimal .docx file (zip with word/document.xml) for testing.

    Args:
        dir_path: Directory to create the file in.
        filename: Name of the .docx file.
        content_xml: Custom document.xml content. If None, a default template
            with {{name}} and {{date}} placeholders is used.

    Returns:
        Absolute path to the created .docx file.
    """
    if content_xml is None:
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Hello {{name}}, today is {{date}}.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Your order #{{order_number}} is ready.</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>"
        )

    filepath = os.path.join(dir_path, filename)
    with zipfile.ZipFile(filepath, "w") as zf:
        zf.writestr("word/document.xml", content_xml)
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
    return filepath


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_docx(tmp_dir: str) -> str:
    """Create a sample .docx with {{name}}, {{date}}, {{order_number}} placeholders."""
    return _create_docx_zip(tmp_dir, "sample.docx")


@pytest.fixture
def agent() -> DocFillerAgent:
    """Provide a DocFillerAgent instance."""
    return DocFillerAgent()


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestPlaceholderInfo:
    """Tests for PlaceholderInfo model."""

    def test_basic_construction(self) -> None:
        p = PlaceholderInfo(name="title")
        assert p.name == "title"
        assert p.field_type == "text"
        assert p.required is True
        assert p.default is None
        assert p.formatting is None

    def test_full_construction(self) -> None:
        p = PlaceholderInfo(
            name="amount",
            field_type="number",
            description="Total amount",
            required=False,
            default="0.00",
            formatting={"bold": True, "font_size": 14},
        )
        assert p.name == "amount"
        assert p.field_type == "number"
        assert p.description == "Total amount"
        assert p.required is False
        assert p.default == "0.00"
        assert p.formatting == {"bold": True, "font_size": 14}

    def test_frozen(self) -> None:
        p = PlaceholderInfo(name="title")
        with pytest.raises(Exception):
            p.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        p = PlaceholderInfo(name="x", field_type="date", required=True)
        data = p.model_dump()
        p2 = PlaceholderInfo.model_validate(data)
        assert p == p2

    def test_json_serialization(self) -> None:
        p = PlaceholderInfo(name="logo", field_type="image_ref")
        json_str = p.model_dump_json()
        data = json.loads(json_str)
        assert data["name"] == "logo"
        assert data["field_type"] == "image_ref"


class TestTemplateAnalysis:
    """Tests for TemplateAnalysis model."""

    def test_empty_analysis(self) -> None:
        a = TemplateAnalysis(template_path="/tmp/test.docx")
        assert a.template_path == "/tmp/test.docx"
        assert a.placeholders == []
        assert a.style_info == {}
        assert a.metadata == {}

    def test_with_placeholders(self) -> None:
        p1 = PlaceholderInfo(name="a")
        p2 = PlaceholderInfo(name="b", field_type="number")
        a = TemplateAnalysis(
            template_path="t.docx",
            placeholders=[p1, p2],
            style_info={"default_font": "Arial"},
            metadata={"section_count": 3},
        )
        assert len(a.placeholders) == 2
        assert a.style_info["default_font"] == "Arial"

    def test_frozen(self) -> None:
        a = TemplateAnalysis(template_path="t.docx")
        with pytest.raises(Exception):
            a.template_path = "other.docx"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        a = TemplateAnalysis(
            template_path="t.docx",
            placeholders=[PlaceholderInfo(name="x")],
            metadata={"pages": 5},
        )
        data = a.model_dump()
        a2 = TemplateAnalysis.model_validate(data)
        assert a == a2


class TestFillRequest:
    """Tests for FillRequest model."""

    def test_minimal(self) -> None:
        r = FillRequest(template_path="t.docx", values={"a": "1"})
        assert r.output_path is None
        assert r.preserve_styles is True

    def test_full(self) -> None:
        r = FillRequest(
            template_path="t.docx",
            values={"a": "1"},
            output_path="out.docx",
            preserve_styles=False,
        )
        assert r.output_path == "out.docx"
        assert r.preserve_styles is False

    def test_frozen(self) -> None:
        r = FillRequest(template_path="t.docx", values={})
        with pytest.raises(Exception):
            r.template_path = "other.docx"  # type: ignore[misc]

    def test_empty_values(self) -> None:
        r = FillRequest(template_path="t.docx", values={})
        assert r.values == {}


class TestFillResult:
    """Tests for FillResult model."""

    def test_success_result(self) -> None:
        r = FillResult(success=True, output_path="out.docx", filled_count=3)
        assert r.success is True
        assert r.filled_count == 3
        assert r.unfilled == []
        assert r.warnings == []

    def test_failure_result(self) -> None:
        r = FillResult(
            success=False,
            output_path="",
            warnings=["File not found"],
        )
        assert r.success is False

    def test_with_unfilled(self) -> None:
        r = FillResult(
            success=True,
            output_path="out.docx",
            filled_count=2,
            unfilled=["missing_field"],
            warnings=["Placeholder 'missing_field' was not filled"],
        )
        assert len(r.unfilled) == 1
        assert len(r.warnings) == 1

    def test_frozen(self) -> None:
        r = FillResult(success=True, output_path="out.docx")
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# analyze_template — placeholder detection
# ---------------------------------------------------------------------------


class TestAnalyzeTemplate:
    """Tests for analyze_template tool."""

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            analyze_template("/nonexistent/file.docx")

    def test_wrong_extension(self, tmp_dir: str) -> None:
        txt_file = os.path.join(tmp_dir, "test.txt")
        Path(txt_file).write_text("hello {{name}}")
        with pytest.raises(ValueError, match="Expected .docx"):
            analyze_template(txt_file)

    def test_detects_placeholders_xml_fallback(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        names = {p.name for p in result.placeholders}
        assert "name" in names
        assert "date" in names
        assert "order_number" in names

    def test_placeholder_count(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        assert len(result.placeholders) == 3

    def test_type_inference_date(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        date_p = next(p for p in result.placeholders if p.name == "date")
        assert date_p.field_type == "date"

    def test_type_inference_number(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        num_p = next(p for p in result.placeholders if p.name == "order_number")
        assert num_p.field_type == "number"

    def test_type_inference_text(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        name_p = next(p for p in result.placeholders if p.name == "name")
        assert name_p.field_type == "text"

    def test_all_required_by_default(self, sample_docx: str) -> None:
        result = analyze_template(sample_docx)
        for p in result.placeholders:
            assert p.required is True

    def test_custom_content(self, tmp_dir: str) -> None:
        xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>{{company_name}} — {{logo}}</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>"
        )
        path = _create_docx_zip(tmp_dir, "custom.docx", content_xml=xml)
        result = analyze_template(path)
        names = {p.name for p in result.placeholders}
        assert names == {"company_name", "logo"}

    def test_logo_inferred_as_image_ref(self, tmp_dir: str) -> None:
        xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>{{logo}}</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>"
        )
        path = _create_docx_zip(tmp_dir, "logo.docx", content_xml=xml)
        result = analyze_template(path)
        logo_p = next(p for p in result.placeholders if p.name == "logo")
        assert logo_p.field_type == "image_ref"

    def test_no_placeholders(self, tmp_dir: str) -> None:
        xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>No placeholders here.</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>"
        )
        path = _create_docx_zip(tmp_dir, "empty.docx", content_xml=xml)
        result = analyze_template(path)
        assert result.placeholders == []


class TestGuessFieldType:
    """Tests for _guess_field_type helper."""

    def test_date_keywords(self) -> None:
        assert _guess_field_type("start_date") == "date"
        assert _guess_field_type("end_time") == "date"
        assert _guess_field_type("due_date") == "date"

    def test_number_keywords(self) -> None:
        assert _guess_field_type("total_amount") == "number"
        assert _guess_field_type("unit_price") == "number"
        assert _guess_field_type("item_count") == "number"
        assert _guess_field_type("order_number") == "number"
        assert _guess_field_type("qty") == "number"

    def test_image_keywords(self) -> None:
        assert _guess_field_type("company_logo") == "image_ref"
        assert _guess_field_type("profile_photo") == "image_ref"
        assert _guess_field_type("product_image") == "image_ref"

    def test_default_text(self) -> None:
        assert _guess_field_type("title") == "text"
        assert _guess_field_type("description") == "text"
        assert _guess_field_type("address") == "text"


class TestPlaceholderRegex:
    """Tests for PLACEHOLDER_RE pattern."""

    def test_simple_match(self) -> None:
        m = PLACEHOLDER_RE.search("{{name}}")
        assert m is not None
        assert m.group(1) == "name"

    def test_in_text(self) -> None:
        m = PLACEHOLDER_RE.search("Hello {{name}}, welcome!")
        assert m is not None
        assert m.group(1) == "name"

    def test_multiple_matches(self) -> None:
        matches = PLACEHOLDER_RE.findall("{{a}} and {{b}} and {{c}}")
        assert matches == ["a", "b", "c"]

    def test_no_match(self) -> None:
        m = PLACEHOLDER_RE.search("no placeholders")
        assert m is None

    def test_no_curly_brace_variants(self) -> None:
        # Single braces should not match
        m = PLACEHOLDER_RE.search("{name}")
        assert m is None

    def test_underscore_in_name(self) -> None:
        m = PLACEHOLDER_RE.search("{{my_field_name}}")
        assert m is not None
        assert m.group(1) == "my_field_name"


# ---------------------------------------------------------------------------
# fill_template — basic filling, style preservation, unfilled tracking
# ---------------------------------------------------------------------------


class TestFillTemplate:
    """Tests for fill_template tool."""

    def test_file_not_found(self) -> None:
        result = fill_template("/nonexistent.docx", {"name": "Alice"})
        assert result.success is False

    def test_wrong_extension(self, tmp_dir: str) -> None:
        txt_file = os.path.join(tmp_dir, "test.txt")
        Path(txt_file).write_text("hello")
        result = fill_template(txt_file, {})
        assert result.success is False

    def test_basic_fill_xml_fallback(self, sample_docx: str, tmp_dir: str) -> None:
        output = os.path.join(tmp_dir, "filled.docx")
        result = fill_template(
            sample_docx,
            {"name": "Alice", "date": "2025-01-15", "order_number": "12345"},
            output_path=output,
        )
        assert result.success is True
        assert result.filled_count == 3
        assert result.unfilled == []
        assert os.path.exists(output)

    def test_partial_fill(self, sample_docx: str, tmp_dir: str) -> None:
        output = os.path.join(tmp_dir, "partial.docx")
        result = fill_template(
            sample_docx,
            {"name": "Bob"},
            output_path=output,
        )
        assert result.success is True
        assert result.filled_count == 1
        assert "date" in result.unfilled
        assert "order_number" in result.unfilled

    def test_empty_values(self, sample_docx: str, tmp_dir: str) -> None:
        output = os.path.join(tmp_dir, "empty_fill.docx")
        result = fill_template(sample_docx, {}, output_path=output)
        assert result.success is True
        assert result.filled_count == 0
        assert len(result.unfilled) == 3

    def test_default_output_path(self, sample_docx: str) -> None:
        result = fill_template(sample_docx, {"name": "Test"})
        assert result.success is True
        assert "_filled" in result.output_path

    def test_default_output_path_generation(self) -> None:
        assert _default_output_path("/tmp/report.docx") == "/tmp/report_filled.docx"
        assert _default_output_path("contract.docx") == "contract_filled.docx"

    def test_extra_values_ignored(self, sample_docx: str, tmp_dir: str) -> None:
        output = os.path.join(tmp_dir, "extra.docx")
        result = fill_template(
            sample_docx,
            {"name": "Alice", "nonexistent": "value"},
            output_path=output,
        )
        assert result.success is True
        # Only placeholders that exist in template are counted
        assert result.filled_count == 1


# ---------------------------------------------------------------------------
# Agent — analyze->fill pipeline
# ---------------------------------------------------------------------------


class TestDocFillerAgent:
    """Tests for DocFillerAgent class."""

    def test_analyze(self, agent: DocFillerAgent, sample_docx: str) -> None:
        result = agent.analyze(sample_docx)
        assert isinstance(result, TemplateAnalysis)
        assert len(result.placeholders) == 3

    def test_fill(self, agent: DocFillerAgent, sample_docx: str, tmp_dir: str) -> None:
        request = FillRequest(
            template_path=sample_docx,
            values={"name": "Charlie", "date": "2025-06-01", "order_number": "99"},
            output_path=os.path.join(tmp_dir, "agent_out.docx"),
        )
        result = agent.fill(request)
        assert result.success is True
        assert result.filled_count == 3

    def test_analyze_file_not_found(self, agent: DocFillerAgent) -> None:
        with pytest.raises(FileNotFoundError):
            agent.analyze("/nonexistent.docx")

    def test_pipeline_analyze_then_fill(
        self, agent: DocFillerAgent, sample_docx: str, tmp_dir: str
    ) -> None:
        # Phase 1: analyze
        analysis = agent.analyze(sample_docx)
        assert len(analysis.placeholders) > 0

        # Phase 2: fill using analysis results
        values = {p.name: f"value_{p.name}" for p in analysis.placeholders}
        request = FillRequest(
            template_path=sample_docx,
            values=values,
            output_path=os.path.join(tmp_dir, "pipeline_out.docx"),
        )
        result = agent.fill(request)
        assert result.success is True
        assert result.filled_count == len(analysis.placeholders)
        assert result.unfilled == []


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        """When fastmcp is not installed, create_mcp_server raises ImportError."""
        try:
            from agent_doc_filler.mcp_adapter import create_mcp_server

            # If fastmcp IS installed, the server should be created successfully
            server = create_mcp_server()
            assert server is not None
        except ImportError:
            # If fastmcp is NOT installed, we expect ImportError with helpful message
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        """The mcp_adapter module should always be importable."""
        import agent_doc_filler.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze(self, agent: DocFillerAgent, sample_docx: str) -> None:
        response = handle_message(
            agent,
            {"method": "analyze", "params": {"template_path": sample_docx}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert len(response["result"]["placeholders"]) == 3

    def test_handle_fill(self, agent: DocFillerAgent, sample_docx: str, tmp_dir: str) -> None:
        output = os.path.join(tmp_dir, "local_out.docx")
        response = handle_message(
            agent,
            {
                "method": "fill",
                "params": {
                    "template_path": sample_docx,
                    "values": {"name": "Dave"},
                    "output_path": output,
                },
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["success"] is True
        assert response["result"]["filled_count"] == 1

    def test_handle_unknown_method(self, agent: DocFillerAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_template_path(self, agent: DocFillerAgent) -> None:
        response = handle_message(
            agent, {"method": "analyze", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_analyze_file_not_found(self, agent: DocFillerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "analyze", "params": {"template_path": "/nonexistent.docx"}},
        )
        assert response["status"] == "error"
        assert "FileNotFoundError" in response.get("error_type", "")

    def test_handle_fill_missing_template_path(self, agent: DocFillerAgent) -> None:
        response = handle_message(
            agent, {"method": "fill", "params": {"values": {"a": "1"}}}
        )
        assert response["status"] == "error"
