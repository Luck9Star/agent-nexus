"""agent-requirements-analyzer -- 多轮对话需求分析专家。

通过多轮对话分析模糊需求，识别歧义、矛盾和遗漏，最终输出结构化需求说明书。
"""

from agent_requirements_analyzer.agent import RequirementsAnalyzerAgent
from agent_requirements_analyzer.models import (
    Question,
    RequirementAnalysis,
    RequirementSpec,
)

__all__ = [
    "RequirementsAnalyzerAgent",
    "Question",
    "RequirementAnalysis",
    "RequirementSpec",
]
