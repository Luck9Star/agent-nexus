"""Template filling tool — replace placeholders with values, preserving styles.

Supports two backends:
- python-docx: Full structured filling with run-level format preservation.
- XML fallback: Simple regex replacement in raw document XML.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from agent_doc_filler.models import FillResult

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _default_output_path(template_path: str) -> str:
    """Generate default output path with _filled suffix."""
    path = Path(template_path)
    return str(path.with_stem(path.stem + "_filled"))


def _fill_with_docx(
    template_path: str,
    values: dict[str, str],
    output_path: str,
) -> FillResult:
    """Fill template using python-docx with full style preservation."""
    from docx import Document

    doc = Document(template_path)
    filled_count = 0
    unfilled: list[str] = []
    warnings: list[str] = []
    seen_placeholders: set[str] = set()

    def _replace_in_paragraph(paragraph: object) -> None:
        """Replace placeholders in a single paragraph, preserving run formatting."""
        nonlocal filled_count
        for run in paragraph.runs:
            text = run.text
            if not PLACEHOLDER_RE.search(text):
                continue

            def _replacer(match: re.Match) -> str:
                nonlocal filled_count
                name = match.group(1)
                seen_placeholders.add(name)
                if name in values:
                    filled_count += 1
                    return values[name]
                return match.group(0)  # Keep original placeholder

            new_text = PLACEHOLDER_RE.sub(_replacer, text)
            run.text = new_text

    # Process paragraphs
    for para in doc.paragraphs:
        _replace_in_paragraph(para)

    # Process tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_in_paragraph(para)

    # Process headers and footers
    for section in doc.sections:
        for para in section.header.paragraphs:
            _replace_in_paragraph(para)
        for para in section.footer.paragraphs:
            _replace_in_paragraph(para)

    # Determine unfilled placeholders
    for name in seen_placeholders:
        if name not in values:
            unfilled.append(name)

    # Check for placeholders that exist in template but weren't seen via runs
    all_text_parts: list[str] = []
    for para in doc.paragraphs:
        all_text_parts.append(para.text)
    remaining = PLACEHOLDER_RE.search(" ".join(all_text_parts))
    if remaining:
        name = remaining.group(1)
        if name not in seen_placeholders and name not in values:
            unfilled.append(name)
            warnings.append(
                f"Placeholder '{name}' spans multiple runs and was not filled. "
                "Consider using a single run for this placeholder."
            )

    if unfilled:
        warnings.append(f"Unfilled placeholders: {', '.join(unfilled)}")

    doc.save(output_path)

    return FillResult(
        success=True,
        output_path=output_path,
        filled_count=filled_count,
        unfilled=unfilled,
        warnings=warnings,
    )


def _fill_xml_fallback(
    template_path: str,
    values: dict[str, str],
    output_path: str,
) -> FillResult:
    """Fill template using XML regex replacement (fallback)."""
    filled_count = 0
    unfilled: list[str] = []
    warnings: list[str] = ["Using XML fallback — style preservation may be limited"]

    # Copy the original .docx to output path
    shutil.copy2(template_path, output_path)

    # Read and replace in all XML files within the zip
    with zipfile.ZipFile(output_path, "r") as zin:
        with zipfile.ZipFile(output_path + ".tmp", "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".xml"):
                    text = data.decode("utf-8")

                    # Find all placeholder names in this XML
                    found = PLACEHOLDER_RE.findall(text)
                    for name in found:
                        if name in values:
                            text = text.replace("{{" + name + "}}", values[name])
                            filled_count += 1
                        else:
                            if name not in unfilled:
                                unfilled.append(name)

                    data = text.encode("utf-8")
                zout.writestr(item, data)

    # Replace original with the new file
    Path(output_path + ".tmp").rename(output_path)

    if unfilled:
        warnings.append(f"Unfilled placeholders: {', '.join(unfilled)}")

    return FillResult(
        success=True,
        output_path=output_path,
        filled_count=filled_count,
        unfilled=unfilled,
        warnings=warnings,
    )


def fill_template(
    template_path: str,
    values: dict[str, str],
    output_path: str | None = None,
) -> FillResult:
    """Fill a .docx template with provided values, preserving styles.

    Args:
        template_path: Path to the .docx template file.
        values: Mapping of placeholder names to replacement values.
        output_path: Where to save the filled document. If None, uses
            template_path with a "_filled" suffix.

    Returns:
        FillResult with fill statistics and any warnings.

    Raises:
        FileNotFoundError: If template_path does not exist.
        ValueError: If the file is not a .docx file.
    """
    path = Path(template_path)
    if not path.exists():
        return FillResult(
            success=False,
            output_path=output_path or template_path,
            warnings=[f"Template file not found: {template_path}"],
        )

    if path.suffix.lower() not in (".docx", ".doc"):
        return FillResult(
            success=False,
            output_path=output_path or template_path,
            warnings=[f"Expected .docx file, got: {path.suffix}"],
        )

    if output_path is None:
        output_path = _default_output_path(template_path)

    try:
        return _fill_with_docx(template_path, values, output_path)
    except ImportError:
        return _fill_xml_fallback(template_path, values, output_path)
    except Exception:
        # python-docx may fail on minimal/malformed docx files
        import logging
        logging.getLogger(__name__).debug(
            "python-docx fill failed, falling back to XML", exc_info=True
        )
        return _fill_xml_fallback(template_path, values, output_path)
