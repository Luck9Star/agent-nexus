"""MCP adapter -- expose requirements-analyzer as an MCP Server using FastMCP.

Provides three MCP tools:
- analyze_requirements: Parse requirement text to identify gaps and ambiguities.
- generate_questions: Generate clarifying questions from analysis.
- build_specification: Assemble structured requirement specification.
"""

from __future__ import annotations

from agent_requirements_analyzer.models import (
    Question,
    RequirementAnalysis,
    RequirementSpec,
)
from agent_requirements_analyzer.tools.analyze_requirements import (
    analyze_requirements as _analyze,
)
from agent_requirements_analyzer.tools.build_specification import (
    build_specification as _build,
)
from agent_requirements_analyzer.tools.generate_questions import (
    generate_questions as _generate_questions,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for requirements-analyzer.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-requirements-analyzer[full]

    Returns:
        A FastMCP server instance with analyze_requirements, generate_questions,
        and build_specification tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-requirements-analyzer[full]"
        )

    mcp = FastMCP("requirements-analyzer")

    @mcp.tool()
    def analyze_requirements(text: str) -> dict:
        """Analyze requirement text to identify gaps, ambiguities, and priorities.

        Parses the text using rule-based pattern matching to extract key
        information, detect ambiguous language, and identify missing details.
        """
        result = _analyze(text)
        return result.model_dump()

    @mcp.tool()
    def generate_questions(analysis: dict) -> dict:
        """Generate clarifying questions based on requirement analysis.

        Takes a RequirementAnalysis dict and produces targeted questions
        for each identified gap, ambiguity, and contradiction.
        """
        parsed_analysis = RequirementAnalysis.model_validate(analysis)
        questions = _generate_questions(parsed_analysis)
        return {
            "questions": [q.model_dump() for q in questions],
        }

    @mcp.tool()
    def build_specification(
        answers: dict[str, str],
        analysis: dict | None = None,
        title: str = "需求说明书",
    ) -> dict:
        """Build a structured requirement specification from answers.

        Assembles sections, priorities, constraints, acceptance criteria,
        and a glossary from the analysis results and user-provided answers.
        """
        parsed_analysis = (
            RequirementAnalysis.model_validate(analysis) if analysis else None
        )
        result = _build(answers, parsed_analysis, title)
        return result.model_dump()

    return mcp
