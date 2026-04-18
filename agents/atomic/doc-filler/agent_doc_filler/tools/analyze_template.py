"""Template analysis tool — identify placeholders and extract formatting context.

Supports two backends:
- python-docx: Full structured parsing of paragraphs, tables, headers/footers.
- XML fallback: Regex-based scanning of raw document XML when python-docx is unavailable.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from agent_doc_filler.models import PlaceholderInfo, TemplateAnalysis

# Placeholder pattern: {{name}}
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _extract_formatting_from_run(run: object) -> dict:
    """Extract formatting properties from a python-docx Run object.

    Returns a dict of non-None formatting attributes, or empty dict if
    python-docx is not available.
    """
    fmt: dict = {}
    try:
        if run.font.name:
            fmt["font_name"] = run.font.name
        if run.font.size:
            fmt["font_size"] = run.font.size.pt
        if run.font.bold is not None:
            fmt["bold"] = run.font.bold
        if run.font.italic is not None:
            fmt["italic"] = run.font.italic
        if run.font.underline is not None:
            fmt["underline"] = run.font.underline
        if run.font.color and run.font.color.rgb:
            fmt["font_color"] = str(run.font.color.rgb)
    except Exception:
        pass
    return fmt


def _guess_field_type(name: str) -> str:
    """Infer a field type hint from the placeholder name."""
    name_lower = name.lower()
    if "date" in name_lower or "time" in name_lower:
        return "date"
    if any(kw in name_lower for kw in ("amount", "price", "total", "count", "number", "num", "qty")):
        return "number"
    if "image" in name_lower or "photo" in name_lower or "logo" in name_lower:
        return "image_ref"
    return "text"


def _analyze_with_docx(template_path: str) -> TemplateAnalysis:
    """Full analysis using python-docx library."""
    from docx import Document

    doc = Document(template_path)
    placeholders: list[PlaceholderInfo] = []
    seen_names: set[str] = set()

    def _scan_text(text: str, formatting: dict | None = None) -> None:
        """Scan a text string for placeholders and add them."""
        for match in PLACEHOLDER_RE.finditer(text):
            name = match.group(1)
            if name in seen_names:
                continue
            seen_names.add(name)
            placeholders.append(
                PlaceholderInfo(
                    name=name,
                    field_type=_guess_field_type(name),
                    description="",
                    required=True,
                    default=None,
                    formatting=formatting,
                )
            )

    # Scan paragraphs
    for para in doc.paragraphs:
        for run in para.runs:
            if PLACEHOLDER_RE.search(run.text):
                _scan_text(run.text, _extract_formatting_from_run(run))

    # Scan tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        if PLACEHOLDER_RE.search(run.text):
                            _scan_text(run.text, _extract_formatting_from_run(run))

    # Scan headers and footers
    for section in doc.sections:
        for para in section.header.paragraphs:
            _scan_text(para.text)
        for para in section.footer.paragraphs:
            _scan_text(para.text)

    # Document-level style info
    style_info: dict = {}
    if doc.styles:
        try:
            normal = doc.styles["Normal"]
            if normal.font.name:
                style_info["default_font"] = normal.font.name
            if normal.font.size:
                style_info["default_size"] = normal.font.size.pt
        except (KeyError, AttributeError):
            pass

    metadata: dict = {}
    metadata["section_count"] = len(doc.sections)

    return TemplateAnalysis(
        template_path=template_path,
        placeholders=placeholders,
        style_info=style_info,
        metadata=metadata,
    )


def _analyze_xml_fallback(template_path: str) -> TemplateAnalysis:
    """Fallback analysis using XML regex when python-docx is unavailable.

    Reads the raw XML inside the .docx zip and scans for {{placeholder}} patterns.
    """
    placeholders: list[PlaceholderInfo] = []
    seen_names: set[str] = set()

    with zipfile.ZipFile(template_path, "r") as zf:
        # Scan main document
        xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
        for xml_name in xml_files:
            content = zf.read(xml_name).decode("utf-8")
            # Remove XML tags to get raw text for placeholder scanning
            text_only = re.sub(r"<[^>]+>", " ", content)
            for match in PLACEHOLDER_RE.finditer(text_only):
                name = match.group(1)
                if name in seen_names:
                    continue
                seen_names.add(name)
                placeholders.append(
                    PlaceholderInfo(
                        name=name,
                        field_type=_guess_field_type(name),
                        description="",
                        required=True,
                        default=None,
                        formatting=None,
                    )
                )

    return TemplateAnalysis(
        template_path=template_path,
        placeholders=placeholders,
        style_info={},
        metadata={"backend": "xml_fallback"},
    )


def analyze_template(template_path: str) -> TemplateAnalysis:
    """Analyze a .docx template file to identify all placeholders.

    Uses python-docx if available for rich structural analysis (paragraphs,
    tables, headers/footers with formatting context). Falls back to XML regex
    scanning when python-docx is not installed.

    Args:
        template_path: Path to the .docx template file.

    Returns:
        TemplateAnalysis with all discovered placeholders and style info.

    Raises:
        FileNotFoundError: If template_path does not exist.
        ValueError: If the file is not a .docx file.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")
    if path.suffix.lower() not in (".docx", ".doc"):
        raise ValueError(f"Expected .docx file, got: {path.suffix}")

    try:
        return _analyze_with_docx(template_path)
    except ImportError:
        return _analyze_xml_fallback(template_path)
