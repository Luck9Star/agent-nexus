"""DocFillerAgent — Word document template filling specialist.

Two-phase pipeline:
  1. analyze() — identify placeholders, types, formatting context
  2. fill()    — populate with values, preserve styles

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_doc_filler.models import FillRequest, FillResult, TemplateAnalysis
from agent_doc_filler.tools.analyze_template import analyze_template
from agent_doc_filler.tools.fill_template import fill_template


class DocFillerAgent:
    """Word document template filling specialist.

    This agent provides a two-phase pipeline for template processing:
    Phase 1 (analyze) scans a .docx file to discover all {{placeholder}}
    patterns and extract their formatting context. Phase 2 (fill) replaces
    placeholders with actual values while preserving original document styles.

    Usage:
        agent = DocFillerAgent()
        analysis = agent.analyze("template.docx")
        print(analysis.placeholders)
        result = agent.fill(FillRequest(
            template_path="template.docx",
            values={"name": "Alice", "date": "2025-01-15"},
            output_path="filled.docx",
        ))
        print(result.success, result.filled_count)
    """

    def analyze(self, template_path: str) -> TemplateAnalysis:
        """Phase 1: Analyze a .docx template to identify placeholders.

        Scans paragraphs, tables, and headers/footers for {{placeholder}}
        patterns and returns structured analysis including formatting context.

        Args:
            template_path: Path to the .docx template file.

        Returns:
            TemplateAnalysis with all discovered placeholders and style info.

        Raises:
            FileNotFoundError: If the template file does not exist.
            ValueError: If the file is not a .docx file.
        """
        return analyze_template(template_path)

    def fill(self, request: FillRequest) -> FillResult:
        """Phase 2: Fill a template with provided values.

        Replaces placeholders with actual content while preserving the original
        document's formatting (font, size, color, bold, italic, etc.).

        Args:
            request: FillRequest specifying template, values, and output path.

        Returns:
            FillResult with success status, filled count, and any warnings.
        """
        return fill_template(
            template_path=request.template_path,
            values=request.values,
            output_path=request.output_path,
        )
