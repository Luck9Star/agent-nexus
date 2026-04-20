"""Data models for doc-filler Agent.

Pydantic v2 frozen models for template analysis and filling operations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlaceholderInfo(BaseModel):
    """Information about a single placeholder found in a template.

    Attributes:
        name: The placeholder identifier (e.g. "title", "author").
        field_type: Semantic type hint (e.g. "text", "date", "number", "image_ref").
        description: Human-readable description of what this placeholder expects.
        required: Whether this placeholder must be filled before output is valid.
        default: Default value to use if none is provided, or None.
        formatting: Formatting context extracted from the placeholder's run
            (font, size, color, bold, italic, etc.).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    field_type: str = "text"
    description: str = ""
    required: bool = True
    default: str | None = None
    formatting: dict | None = None


class HeadingInfo(BaseModel):
    """A heading found in the document."""

    model_config = ConfigDict(frozen=True)

    level: int
    text: str


class TableInfo(BaseModel):
    """Summary of a table found in the document."""

    model_config = ConfigDict(frozen=True)

    index: int
    rows: int
    cols: int
    header_row: list[str] = Field(default_factory=list)
    preview: list[list[str]] = Field(default_factory=list)


class SectionContent(BaseModel):
    """Content summary for a document section (between headings)."""

    model_config = ConfigDict(frozen=True)

    heading: str = ""
    level: int = 0
    paragraph_count: int = 0
    preview: str = ""


class DocumentStats(BaseModel):
    """Aggregate statistics about the document."""

    model_config = ConfigDict(frozen=True)

    total_paragraphs: int = 0
    total_tables: int = 0
    total_images: int = 0
    total_characters: int = 0
    total_words: int = 0
    heading_count: int = 0


class TemplateAnalysis(BaseModel):
    """Result of analyzing a Word document template.

    Attributes:
        template_path: Path to the analyzed template file.
        placeholders: All placeholders found in the template.
        headings: Document heading hierarchy.
        sections: Content sections between headings.
        tables: Table summaries with header rows and preview data.
        stats: Aggregate document statistics.
        style_info: Document-level style information (default font, themes, etc.).
        metadata: Additional metadata (page count, section count, etc.).
    """

    model_config = ConfigDict(frozen=True)

    template_path: str
    placeholders: list[PlaceholderInfo] = Field(default_factory=list)
    headings: list[HeadingInfo] = Field(default_factory=list)
    sections: list[SectionContent] = Field(default_factory=list)
    tables: list[TableInfo] = Field(default_factory=list)
    stats: DocumentStats = Field(default_factory=DocumentStats)
    style_info: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class FillRequest(BaseModel):
    """Request to fill a template with values.

    Attributes:
        template_path: Path to the template .docx file.
        values: Mapping of placeholder names to their replacement values.
        output_path: Where to write the filled document. Defaults to
            template_path with "_filled" suffix.
        preserve_styles: Whether to preserve original run formatting when filling.
    """

    model_config = ConfigDict(frozen=True)

    template_path: str
    values: dict[str, str] = Field(default_factory=dict)
    output_path: str | None = None
    preserve_styles: bool = True


class FillResult(BaseModel):
    """Result of a template fill operation.

    Attributes:
        success: Whether the fill operation completed without errors.
        output_path: Path to the generated output file.
        filled_count: Number of placeholders successfully filled.
        unfilled: List of placeholder names that were not filled.
        warnings: Non-fatal warnings encountered during filling.
    """

    model_config = ConfigDict(frozen=True)

    success: bool
    output_path: str
    filled_count: int = 0
    unfilled: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
