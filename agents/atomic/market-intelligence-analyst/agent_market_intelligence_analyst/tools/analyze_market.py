"""Market analysis tool — apply analytical frameworks to market data.

Supports three frameworks:
- Porter's Five Forces (porter)
- SWOT Analysis (swot)
- PESTEL Analysis (pestel)

Uses keyword-based factor extraction and scoring.
"""

from __future__ import annotations

from agent_market_intelligence_analyst.models import MarketAnalysis

# Framework factor definitions with associated keywords
FRAMEWORK_FACTORS: dict[str, dict[str, dict]] = {
    "porter": {
        "supplier_power": {
            "label": "Supplier Power",
            "keywords": [
                "供应商", "supplier", "原材料", "raw material", "独家供应",
                "monopoly", "供应商议价", "bargaining",
            ],
        },
        "buyer_power": {
            "label": "Buyer Power",
            "keywords": [
                "买方", "buyer", "客户议价", "customer", "价格敏感",
                "price sensitive", "替代选择", "alternatives",
            ],
        },
        "new_entrants": {
            "label": "Threat of New Entrants",
            "keywords": [
                "新进入", "new entrant", "进入壁垒", "barrier to entry",
                "启动成本", "startup cost", "市场份额", "market share",
            ],
        },
        "substitutes": {
            "label": "Threat of Substitutes",
            "keywords": [
                "替代品", "substitute", "替代技术", "alternative technology",
                "颠覆", "disrupt", "数字化转型", "digital transformation",
            ],
        },
        "rivalry": {
            "label": "Industry Rivalry",
            "keywords": [
                "竞争", "compet", "市场份额", "market share", "价格战",
                "price war", "行业竞争", "rivalry", "集中度",
            ],
        },
    },
    "swot": {
        "strengths": {
            "label": "Strengths",
            "keywords": [
                "优势", "strength", "领先", "leading", "核心技术", "core technology",
                "品牌", "brand", "专利", "patent", "效率", "efficiency",
            ],
        },
        "weaknesses": {
            "label": "Weaknesses",
            "keywords": [
                "劣势", "weakness", "不足", "deficiency", "依赖", "dependence",
                "成本高", "high cost", "瓶颈", "bottleneck", "缺乏",
            ],
        },
        "opportunities": {
            "label": "Opportunities",
            "keywords": [
                "机会", "opportunity", "增长", "growth", "新兴市场", "emerging market",
                "政策支持", "policy support", "需求增长", "demand growth",
            ],
        },
        "threats": {
            "label": "Threats",
            "keywords": [
                "威胁", "threat", "风险", "risk", "竞争加剧", "competition intensif",
                "监管", "regulation", "经济下行", "economic downturn",
            ],
        },
    },
    "pestel": {
        "political": {
            "label": "Political",
            "keywords": [
                "政治", "politic", "政策", "policy", "政府", "government",
                "法规", "regulation", "贸易战", "trade war", "关税", "tariff",
            ],
        },
        "economic": {
            "label": "Economic",
            "keywords": [
                "经济", "econom", "GDP", "通胀", "inflation", "利率", "interest rate",
                "消费", "consumption", "投资", "investment",
            ],
        },
        "social": {
            "label": "Social",
            "keywords": [
                "社会", "social", "人口", "demograph", "消费习惯", "consumer behavior",
                "生活方式", "lifestyle", "教育", "education", "健康", "health",
            ],
        },
        "technological": {
            "label": "Technological",
            "keywords": [
                "技术", "technolog", "创新", "innovation", "AI", "人工智能",
                "数字化", "digital", "自动化", "automation", "研发", "R&D",
            ],
        },
        "environmental": {
            "label": "Environmental",
            "keywords": [
                "环境", "environment", "碳", "carbon", "可持续", "sustainable",
                "绿色", "green", "气候", "climate", "ESG",
            ],
        },
        "legal": {
            "label": "Legal",
            "keywords": [
                "法律", "legal", "合规", "compliance", "诉讼", "litigation",
                "知识产权", "intellectual property", "数据保护", "data protection",
            ],
        },
    },
}

# Insight templates per framework
INSIGHT_TEMPLATES: dict[str, list[str]] = {
    "porter": [
        "行业竞争格局由{top_factor}主导",
        "市场进入壁垒{barrier_level}",
        "买方和供应商的力量对比呈{power_balance}态势",
    ],
    "swot": [
        "核心优势集中在{strengths_area}",
        "主要风险来源于{threats_area}",
        "增长机会与{opportunities_area}密切相关",
    ],
    "pestel": [
        "政策环境{political_assessment}",
        "技术创新是主要驱动力",
        "ESG合规要求日益重要",
    ],
}

SUPPORTED_FRAMEWORKS = {"porter", "swot", "pestel"}


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in the text."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _compute_score(keyword_hits: int, total_keywords: int) -> int:
    """Compute a 1-5 score based on keyword hit ratio."""
    if total_keywords == 0:
        return 3
    ratio = keyword_hits / total_keywords
    if ratio >= 0.6:
        return 5
    elif ratio >= 0.4:
        return 4
    elif ratio >= 0.2:
        return 3
    elif ratio >= 0.1:
        return 2
    return 1


def _generate_insights(framework: str, scores: dict[str, int]) -> list[str]:
    """Generate insights based on factor scores."""
    insights: list[str] = []

    if framework == "porter":
        if scores.get("rivalry", 0) >= 4:
            insights.append("行业竞争激烈，需要差异化战略")
        if scores.get("new_entrants", 0) >= 4:
            insights.append("新进入者威胁较大，需加强护城河")
        if scores.get("buyer_power", 0) >= 4:
            insights.append("买方议价能力强，需提升客户粘性")
        if not insights:
            insights.append("市场环境相对稳定，适合稳健发展")

    elif framework == "swot":
        if scores.get("strengths", 0) >= 4:
            insights.append("具备显著竞争优势，应积极扩展")
        if scores.get("threats", 0) >= 4:
            insights.append("外部威胁显著，需制定风险缓解策略")
        if scores.get("opportunities", 0) >= 4:
            insights.append("市场机会丰富，建议加大投入")
        if not insights:
            insights.append("优劣势均衡，需找准差异化定位")

    elif framework == "pestel":
        if scores.get("technological", 0) >= 4:
            insights.append("技术创新是核心驱动力，应持续投入研发")
        if scores.get("political", 0) >= 4:
            insights.append("政策环境活跃，需密切关注监管变化")
        if scores.get("economic", 0) >= 4:
            insights.append("经济因素影响显著，需建立抗周期机制")
        if not insights:
            insights.append("宏观环境平稳，适合长期战略规划")

    return insights


def _assess_factor(hits: int, label: str) -> str:
    """Generate a brief assessment for a single factor."""
    if hits >= 5:
        return f"{label}: 影响显著（关键词命中 {hits} 次）"
    elif hits >= 3:
        return f"{label}: 有一定影响（关键词命中 {hits} 次）"
    elif hits >= 1:
        return f"{label}: 影响有限（关键词命中 {hits} 次）"
    else:
        return f"{label}: 未检测到明显信号"


def analyze_market(data: str, framework: str = "porter") -> MarketAnalysis:
    """Apply an analytical framework to market data.

    Analyzes market data using the specified framework (porter/swot/pestel),
    extracting factor assessments, scores, and insights.

    Args:
        data: Market data text to analyze.
        framework: Framework to use ("porter", "swot", "pestel").

    Returns:
        MarketAnalysis with factor assessments, scores, and insights.

    Raises:
        ValueError: If framework is not supported.
    """
    framework = framework.lower().strip()
    if framework not in SUPPORTED_FRAMEWORKS:
        raise ValueError(
            f"Unsupported framework: '{framework}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_FRAMEWORKS))}"
        )

    if not data or not data.strip():
        return MarketAnalysis(
            framework=framework,
            factors={},
            scores={},
            insights=["无数据可供分析"],
        )

    factors_config = FRAMEWORK_FACTORS[framework]
    factors: dict[str, str] = {}
    scores: dict[str, int] = {}

    # Pre-compute lowercase text once
    text_lower = data.lower()

    for factor_key, config in factors_config.items():
        keywords = config["keywords"]
        label = config["label"]
        hits = _count_keyword_hits(text_lower, keywords)
        factors[factor_key] = _assess_factor(hits, label)
        scores[factor_key] = _compute_score(hits, len(keywords))

    insights = _generate_insights(framework, scores)

    return MarketAnalysis(
        framework=framework,
        factors=factors,
        scores=scores,
        insights=insights,
    )
