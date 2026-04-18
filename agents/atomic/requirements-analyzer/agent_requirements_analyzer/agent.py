"""RequirementsAnalyzerAgent -- 多轮对话需求分析专家。

Three-phase pipeline:
  1. analyze()    -- parse text, identify gaps, ambiguities, priorities
  2. questions()  -- generate clarifying questions from analysis
  3. build()      -- assemble structured specification from answers

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_requirements_analyzer.models import (
    Question,
    RequirementAnalysis,
    RequirementSpec,
)
from agent_requirements_analyzer.tools.analyze_requirements import (
    analyze_requirements,
)
from agent_requirements_analyzer.tools.build_specification import (
    build_specification,
)
from agent_requirements_analyzer.tools.generate_questions import (
    generate_questions,
)


class RequirementsAnalyzerAgent:
    """多轮对话需求分析专家。

    This agent provides a three-phase pipeline for requirement analysis:
    Phase 1 (analyze) parses requirement text to identify gaps, ambiguities,
    and priorities. Phase 2 (questions) generates targeted clarifying questions.
    Phase 3 (build) assembles a structured requirement specification.

    Usage:
        agent = RequirementsAnalyzerAgent()
        analysis = agent.analyze("需要一个用户管理系统，支持登录和注册")
        questions = agent.questions(analysis)
        spec = agent.build({"用户角色": "管理员、普通用户", "登录方式": "账号密码"})
        print(spec.title, spec.sections)
    """

    def analyze(self, text: str) -> RequirementAnalysis:
        """Phase 1: Analyze requirement text to identify gaps and ambiguities.

        Args:
            text: The requirement text to analyze.

        Returns:
            RequirementAnalysis with gaps, ambiguities, priorities, and key terms.
        """
        return analyze_requirements(text)

    def questions(self, analysis: RequirementAnalysis) -> list[Question]:
        """Phase 2: Generate clarifying questions from analysis.

        Args:
            analysis: The RequirementAnalysis from phase 1.

        Returns:
            List of Question objects sorted by priority.
        """
        return generate_questions(analysis)

    def build(
        self,
        answers: dict[str, str],
        analysis: RequirementAnalysis | None = None,
        title: str = "需求说明书",
    ) -> RequirementSpec:
        """Phase 3: Build structured requirement specification.

        Args:
            answers: User-provided answers to clarifying questions.
            analysis: The RequirementAnalysis from phase 1. If None,
                a fresh analysis is not performed; an empty analysis is used.
            title: Title for the specification document.

        Returns:
            RequirementSpec with sections, priorities, constraints, etc.
        """
        return build_specification(answers, analysis, title)
