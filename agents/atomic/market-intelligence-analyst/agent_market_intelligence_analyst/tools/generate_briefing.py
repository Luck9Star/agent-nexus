"""Briefing generation tool — synthesize analysis into a structured report.

Takes a MarketAnalysis and produces a BriefingReport with executive summary,
detailed sections, and strategic recommendations.
"""

from __future__ import annotations

from agent_market_intelligence_analyst.models import BriefingReport, MarketAnalysis

# Framework display names
FRAMEWORK_NAMES: dict[str, str] = {
    "porter": "Porter's Five Forces",
    "swot": "SWOT Analysis",
    "pestel": "PESTEL Analysis",
}


def _generate_title(analysis: MarketAnalysis) -> str:
    """Generate briefing title based on framework."""
    framework_name = FRAMEWORK_NAMES.get(analysis.framework, analysis.framework.title())
    return f"Market Intelligence Briefing — {framework_name}"


def _generate_executive_summary(analysis: MarketAnalysis) -> str:
    """Generate executive summary from analysis insights."""
    if not analysis.insights:
        return "No significant findings from the analysis."

    parts = [
        f"基于{FRAMEWORK_NAMES.get(analysis.framework, analysis.framework)}框架的市场分析结果："
    ]
    for insight in analysis.insights:
        parts.append(f"- {insight}")

    # Add score summary
    if analysis.scores:
        avg_score = sum(analysis.scores.values()) / len(analysis.scores)
        if avg_score >= 4:
            parts.append("整体评估：市场活跃度高，需要积极的战略应对")
        elif avg_score >= 3:
            parts.append("整体评估：市场环境适中，适合稳健发展")
        else:
            parts.append("整体评估：市场信号较弱，可保持观察")

    return "\n".join(parts)


def _generate_sections(analysis: MarketAnalysis) -> dict[str, str]:
    """Generate detailed sections from factor assessments."""
    sections: dict[str, str] = {}

    for factor_key, assessment in analysis.factors.items():
        score = analysis.scores.get(factor_key, 0)
        sections[factor_key] = f"{assessment}\n评分: {score}/5"

    return sections


def _recommend_porter(scores: dict[str, int]) -> list[str]:
    """Generate Porter's Five Forces recommendations."""
    recs: list[str] = []
    if scores.get("rivalry", 0) >= 4:
        recs.append("建议通过差异化产品或服务降低直接竞争")
    if scores.get("buyer_power", 0) >= 4:
        recs.append("建议提升客户忠诚度和转换成本")
    if scores.get("new_entrants", 0) >= 3:
        recs.append("建议加强品牌建设和专利保护")
    if scores.get("supplier_power", 0) >= 4:
        recs.append("建议多元化供应商，降低依赖")
    return recs


def _recommend_swot(scores: dict[str, int]) -> list[str]:
    """Generate SWOT analysis recommendations."""
    recs: list[str] = []
    if scores.get("strengths", 0) >= 4:
        recs.append("利用核心优势进行市场扩张")
    if scores.get("weaknesses", 0) >= 3:
        recs.append("制定改进计划弥补关键劣势")
    if scores.get("opportunities", 0) >= 4:
        recs.append("积极抓住市场机会，加大资源投入")
    if scores.get("threats", 0) >= 4:
        recs.append("建立风险缓解机制，应对外部威胁")
    return recs


def _recommend_pestel(scores: dict[str, int]) -> list[str]:
    """Generate PESTEL analysis recommendations."""
    recs: list[str] = []
    if scores.get("political", 0) >= 4:
        recs.append("密切关注政策变化，建立政府关系")
    if scores.get("technological", 0) >= 4:
        recs.append("加大技术投入，保持创新领先")
    if scores.get("environmental", 0) >= 3:
        recs.append("制定ESG战略，提前布局可持续发展")
    if scores.get("legal", 0) >= 3:
        recs.append("确保合规运营，降低法律风险")
    return recs


_RECOMMENDATION_GENERATORS = {
    "porter": _recommend_porter,
    "swot": _recommend_swot,
    "pestel": _recommend_pestel,
}


def _generate_recommendations(analysis: MarketAnalysis) -> list[str]:
    """Generate strategic recommendations based on analysis."""
    generator = _RECOMMENDATION_GENERATORS.get(analysis.framework)
    recs = generator(analysis.scores) if generator else []
    if not recs:
        recs.append("继续监测市场动态，保持战略灵活性")
    return recs


def generate_briefing(analysis: MarketAnalysis) -> BriefingReport:
    """Generate a structured briefing from market analysis.

    Synthesizes market analysis results into a structured briefing report
    with executive summary, detailed sections, and strategic recommendations.

    Args:
        analysis: MarketAnalysis results to synthesize.

    Returns:
        BriefingReport with title, summary, sections, and recommendations.
    """
    return BriefingReport(
        title=_generate_title(analysis),
        executive_summary=_generate_executive_summary(analysis),
        sections=_generate_sections(analysis),
        recommendations=_generate_recommendations(analysis),
    )
