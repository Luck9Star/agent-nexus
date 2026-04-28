"""Question generation tool -- produce clarifying questions from analysis results.

Generates targeted questions based on identified gaps, ambiguities,
and priorities from a RequirementAnalysis.
"""

from __future__ import annotations

from agent_requirements_analyzer.models import Question, RequirementAnalysis

# Mapping of gap patterns to question templates
GAP_QUESTION_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "缺少用户角色定义": [
        {
            "text": "系统涉及哪些用户角色？各角色有什么权限差异？",
            "category": "functional",
            "priority": "high",
        },
    ],
    "缺少错误处理说明": [
        {
            "text": "当操作失败时，系统应该如何处理和反馈？",
            "category": "non_functional",
            "priority": "high",
        },
    ],
    "缺少性能指标定义": [
        {
            "text": "系统对响应时间和并发量有什么具体要求？",
            "category": "non_functional",
            "priority": "medium",
        },
    ],
    "缺少认证方式详细说明": [
        {
            "text": "用户认证使用什么方式（账号密码/OAuth/手机验证码/SSO）？",
            "category": "functional",
            "priority": "high",
        },
    ],
    "缺少数据模型定义": [
        {
            "text": "系统的核心数据实体有哪些？它们之间是什么关系？",
            "category": "functional",
            "priority": "medium",
        },
    ],
}

# Category mapping for ambiguity questions
AMBIGUITY_CATEGORY_MAP: dict[str, str] = {
    "性能": "non_functional",
    "安全": "non_functional",
    "灵活": "constraint",
    "扩展": "constraint",
    "稳定": "non_functional",
    "用户友好": "non_functional",
}


def _generate_gap_questions(gaps: list[str]) -> list[Question]:
    """Generate questions for identified gaps."""
    questions: list[Question] = []
    for gap in gaps:
        templates = GAP_QUESTION_TEMPLATES.get(gap, [])
        for tmpl in templates:
            questions.append(Question(
                text=tmpl["text"],
                category=tmpl["category"],
                priority=tmpl["priority"],
                context=f"缺口: {gap}",
            ))
        if not templates:
            # Generic question for unknown gap type
            questions.append(Question(
                text=f"请补充关于「{gap}」的详细信息",
                category="functional",
                priority="medium",
                context=f"缺口: {gap}",
            ))
    return questions


def _generate_ambiguity_questions(ambiguities: list[str]) -> list[Question]:
    """Generate questions for ambiguous statements."""
    questions: list[Question] = []
    for ambiguity in ambiguities:
        # Determine category from ambiguity content
        category = "functional"
        for keyword, cat in AMBIGUITY_CATEGORY_MAP.items():
            if keyword in ambiguity:
                category = cat
                break

        # Extract the ambiguous phrase
        phrase = ambiguity.replace("模糊表述: ", "").strip("'\"")
        questions.append(Question(
            text=f"「{phrase}」具体指什么？请给出明确的量化标准或具体场景。",
            category=category,
            priority="high",
            context=ambiguity,
        ))
    return questions


def _generate_contradiction_questions(contradictions: list[str]) -> list[Question]:
    """Generate questions for identified contradictions."""
    questions: list[Question] = []
    for contradiction in contradictions:
        questions.append(Question(
            text=f"需求中存在矛盾: {contradiction}。请确认优先满足哪个？",
            category="constraint",
            priority="high",
            context=f"矛盾: {contradiction}",
        ))
    return questions


def _generate_priority_questions(
    priorities: dict[str, list[str]],
) -> list[Question]:
    """Generate questions for high-priority items that need clarification."""
    questions: list[Question] = []
    high_items = priorities.get("high", [])
    if len(high_items) > 3:
        questions.append(Question(
            text=f"列出了 {len(high_items)} 个高优先级需求，是否可以进一步区分优先级？",
            category="priority",
            priority="medium",
            context=f"高优先级需求数量: {len(high_items)}",
        ))
    return questions


def generate_questions(analysis: RequirementAnalysis) -> list[Question]:
    """Generate clarifying questions based on requirement analysis.

    Produces targeted questions for each identified gap, ambiguity,
    and contradiction, plus priority-related questions when needed.

    Args:
        analysis: The RequirementAnalysis to generate questions from.

    Returns:
        List of Question objects, sorted by priority (high first).
    """
    questions: list[Question] = []

    # Gap-based questions (highest priority)
    questions.extend(_generate_gap_questions(analysis.gaps))

    # Ambiguity-based questions
    questions.extend(_generate_ambiguity_questions(analysis.ambiguities))

    # Contradiction-based questions
    questions.extend(_generate_contradiction_questions(analysis.contradictions))

    # Priority-related questions
    questions.extend(_generate_priority_questions(analysis.priorities))

    # Sort by priority: high > medium > low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    questions.sort(key=lambda q: priority_order.get(q.priority, 1))

    return questions
