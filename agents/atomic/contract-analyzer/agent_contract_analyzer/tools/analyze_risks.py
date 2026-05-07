"""Risk analysis tool — identify legal risks in contract clauses.

Scans extracted clauses for common risk patterns including unequal terms,
ambiguous language, missing mandatory clauses, and excessive liability.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_contract_analyzer.models import ClauseInfo, RiskAnalysis, RiskItem

# Risk detection rules
RISK_RULES: list[dict] = [
    {
        "category": "ambiguity",
        "severity": "medium",
        "keywords": ["合理", "适当", "相关", "reasonable", "appropriate", "relevant", "as needed"],
        "description_template": "条款包含模糊表述: '{keyword}'，可能导致解释争议",
        "mitigation": "建议将模糊表述替换为具体的量化标准或明确定义",
    },
    {
        "category": "excessive_liability",
        "severity": "high",
        "keywords": ["全部责任", "任何损失", "all liabilities", "any losses", "unlimited"],
        "description_template": "条款包含过度责任条款: '{keyword}'，可能导致无限责任",
        "mitigation": "建议设置责任上限，排除间接损失",
    },
    {
        "category": "unilateral_termination",
        "severity": "high",
        "keywords": ["单方终止", "随时终止", "unilateral termination", "terminate at will"],
        "description_template": "条款包含单方终止权: '{keyword}'，权利不对等",
        "mitigation": "建议增加终止通知期和双方对等的终止条件",
    },
    {
        "category": "missing_limitation",
        "severity": "medium",
        "keywords": [],
        "check": "no_limitation",
        "description_template": "合同缺少责任限制条款",
        "mitigation": "建议增加责任限制条款，明确赔偿上限",
    },
    {
        "category": "missing_dispute_resolution",
        "severity": "medium",
        "keywords": [],
        "check": "no_dispute",
        "description_template": "合同缺少争议解决条款",
        "mitigation": "建议增加争议解决机制（仲裁或法院管辖）",
    },
    {
        "category": "vague_payment",
        "severity": "high",
        "keywords": ["另行协商", "另行约定", "to be agreed", "negotiable"],
        "description_template": "付款条款不明确: '{keyword}'，可能导致争议",
        "mitigation": "建议明确付款金额、时间和方式",
    },
]


def _check_keyword_risks(clauses: Sequence[ClauseInfo]) -> list[RiskItem]:
    """Check for keyword-based risks across all clauses."""
    risks: list[RiskItem] = []

    # Pre-compute lowercase content once per clause
    clause_contents_lower = [c.content.lower() for c in clauses]

    for rule in RISK_RULES:
        if not rule["keywords"]:
            continue

        for idx, clause in enumerate(clauses):
            content_lower = clause_contents_lower[idx]
            for keyword in rule["keywords"]:
                if keyword.lower() in content_lower:
                    risks.append(
                        RiskItem(
                            category=rule["category"],
                            severity=rule["severity"],
                            description=rule["description_template"].format(keyword=keyword),
                            affected_clauses=[clause.clause_id],
                            mitigation=rule["mitigation"],
                        )
                    )

    return risks


def _check_missing_clauses(clauses: Sequence[ClauseInfo]) -> list[RiskItem]:
    """Check for missing mandatory clause types."""
    risks: list[RiskItem] = []
    existing_types = {c.type for c in clauses}

    for rule in RISK_RULES:
        if (
            rule.get("check") == "no_limitation"
            and "indemnification" not in existing_types
            or rule.get("check") == "no_dispute"
            and "governing_law" not in existing_types
        ):
            risks.append(
                RiskItem(
                    category=rule["category"],
                    severity=rule["severity"],
                    description=rule["description_template"],
                    affected_clauses=[],
                    mitigation=rule["mitigation"],
                )
            )

    return risks


def _compute_severity_map(risks: Sequence[RiskItem]) -> dict[str, int]:
    """Compute count of risks per severity level."""
    severity_map: dict[str, int] = {}
    for risk in risks:
        severity_map[risk.severity] = severity_map.get(risk.severity, 0) + 1
    return severity_map


def _generate_recommendations(risks: Sequence[RiskItem]) -> list[str]:
    """Generate overall recommendations based on risk analysis."""
    recs: list[str] = []

    critical_count = sum(1 for r in risks if r.severity == "critical")
    high_count = sum(1 for r in risks if r.severity == "high")
    medium_count = sum(1 for r in risks if r.severity == "medium")

    if critical_count > 0:
        recs.append(f"发现 {critical_count} 个严重风险，建议在签署前解决所有严重问题")
    if high_count > 0:
        recs.append(f"发现 {high_count} 个高风险，建议与法律顾问讨论后再决定")
    if medium_count > 0:
        recs.append(f"发现 {medium_count} 个中等风险，建议逐条评估并修改")

    if not risks:
        recs.append("未发现明显风险，合同条款整体可接受")

    return recs


def analyze_risks(clauses: list[ClauseInfo]) -> RiskAnalysis:
    """Analyze extracted clauses for legal risks.

    Scans clauses for keyword-based risk patterns and checks for missing
    mandatory clause types, producing a comprehensive risk analysis.

    Args:
        clauses: List of extracted ClauseInfo to analyze.

    Returns:
        RiskAnalysis with identified risks, severity map, and recommendations.
    """
    if not clauses:
        return RiskAnalysis(
            risks=[],
            severity_map={},
            recommendations=["无条款可供分析"],
        )

    keyword_risks = _check_keyword_risks(clauses)
    missing_risks = _check_missing_clauses(clauses)

    all_risks = keyword_risks + missing_risks
    severity_map = _compute_severity_map(all_risks)
    recommendations = _generate_recommendations(all_risks)

    return RiskAnalysis(
        risks=all_risks,
        severity_map=severity_map,
        recommendations=recommendations,
    )
