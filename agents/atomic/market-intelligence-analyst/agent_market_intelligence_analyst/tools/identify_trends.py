"""Trend identification tool — extract and evaluate market trends.

Scans market data for trend signals, evaluates their direction and impact,
and produces a comprehensive trend report with confidence scoring.
"""

from __future__ import annotations

import re

from agent_market_intelligence_analyst.models import TrendItem, TrendReport

# Trend signal keywords
TREND_SIGNALS: list[dict] = [
    {
        "name": "AI/技术驱动增长",
        "direction": "up",
        "impact": "high",
        "keywords": ["AI", "人工智能", "机器学习", "machine learning", "深度学习", "大模型", "LLM"],
        "timeframe": "1-3年",
    },
    {
        "name": "数字化转型加速",
        "direction": "up",
        "impact": "high",
        "keywords": ["数字化", "digital", "云", "cloud", "SaaS", "平台化"],
        "timeframe": "1-5年",
    },
    {
        "name": "可持续/ESG趋势",
        "direction": "up",
        "impact": "medium",
        "keywords": ["ESG", "可持续", "sustainable", "绿色", "green", "碳中和"],
        "timeframe": "3-10年",
    },
    {
        "name": "市场增长",
        "direction": "up",
        "impact": "high",
        "keywords": ["增长", "growth", "扩大", "expand", "上升", "增加"],
        "timeframe": "1-3年",
    },
    {
        "name": "市场竞争加剧",
        "direction": "up",
        "impact": "high",
        "keywords": ["竞争加剧", "价格战", "竞争对手增多", "market share battle"],
        "timeframe": "1-2年",
    },
    {
        "name": "监管趋严",
        "direction": "up",
        "impact": "medium",
        "keywords": ["监管", "regulation", "合规", "compliance", "数据保护"],
        "timeframe": "1-5年",
    },
    {
        "name": "消费升级",
        "direction": "up",
        "impact": "medium",
        "keywords": ["消费升级", "高端化", "premium", "品质", "体验"],
        "timeframe": "3-5年",
    },
    {
        "name": "全球化退缩",
        "direction": "down",
        "impact": "medium",
        "keywords": ["贸易战", "trade war", "脱钩", "decoupling", "关税", "地缘"],
        "timeframe": "3-10年",
    },
    {
        "name": "市场收缩",
        "direction": "down",
        "impact": "high",
        "keywords": ["下降", "decline", "萎缩", "shrink", "经济下行", "recession"],
        "timeframe": "1-3年",
    },
    {
        "name": "人口老龄化",
        "direction": "up",
        "impact": "medium",
        "keywords": ["老龄化", "aging", "人口结构", "银发经济", "养老"],
        "timeframe": "10年以上",
    },
]

# Direction keywords for overriding default direction
UP_KEYWORDS = ["增长", "上升", "加速", "growth", "increase", "rising", "surge", "boom"]
DOWN_KEYWORDS = ["下降", "萎缩", "衰退", "decline", "decrease", "falling", "shrink", "recession"]
STABLE_KEYWORDS = ["稳定", "平稳", "stable", "steady", "flat", "maintain"]


def _detect_direction_override(text: str) -> str | None:
    """Check if text contains direction-override keywords."""
    text_lower = text.lower()
    for kw in UP_KEYWORDS:
        if kw in text_lower:
            return "up"
    for kw in DOWN_KEYWORDS:
        if kw in text_lower:
            return "down"
    for kw in STABLE_KEYWORDS:
        if kw in text_lower:
            return "stable"
    return None


def _compute_confidence(trends: list[TrendItem], data_length: int) -> float:
    """Compute confidence score based on number of trends and data richness."""
    if not trends:
        return 0.0
    # Base confidence on number of trends found and data length
    trend_factor = min(len(trends) / 5.0, 1.0)
    data_factor = min(data_length / 500.0, 1.0)
    return round(0.6 * trend_factor + 0.4 * data_factor, 2)


def _generate_summary(trends: list[TrendItem]) -> str:
    """Generate a summary of identified trends."""
    if not trends:
        return "未检测到明显趋势信号"

    up_count = sum(1 for t in trends if t.direction == "up")
    down_count = sum(1 for t in trends if t.direction == "down")
    high_impact = sum(1 for t in trends if t.impact == "high")

    parts: list[str] = [f"共识别 {len(trends)} 个趋势"]
    if up_count:
        parts.append(f"{up_count} 个上升趋势")
    if down_count:
        parts.append(f"{down_count} 个下降趋势")
    if high_impact:
        parts.append(f"{high_impact} 个高影响趋势")

    return "，".join(parts)


def identify_trends(data: str) -> TrendReport:
    """Identify and evaluate market trends from data.

    Scans market data for trend signals, evaluates their direction and impact,
    and produces a trend report with confidence scoring.

    Args:
        data: Market data text to analyze.

    Returns:
        TrendReport with identified trends, summary, and confidence score.
    """
    if not data or not data.strip():
        return TrendReport(
            trends=[],
            summary="无数据可供分析",
            confidence=0.0,
        )

    trends: list[TrendItem] = []
    seen_names: set[str] = set()

    for signal in TREND_SIGNALS:
        keywords = signal["keywords"]
        hits = sum(1 for kw in keywords if kw.lower() in data.lower())

        if hits >= 1:
            # Collect evidence
            evidence_parts: list[str] = []
            for kw in keywords:
                if kw.lower() in data.lower():
                    evidence_parts.append(kw)

            # Check for direction override
            direction = signal["direction"]
            override = _detect_direction_override(data)
            if override:
                direction = override

            if signal["name"] not in seen_names:
                seen_names.add(signal["name"])
                trends.append(
                    TrendItem(
                        name=signal["name"],
                        direction=direction,
                        impact=signal["impact"],
                        evidence="、".join(evidence_parts[:5]),
                        timeframe=signal["timeframe"],
                    )
                )

    summary = _generate_summary(trends)
    confidence = _compute_confidence(trends, len(data))

    return TrendReport(
        trends=trends,
        summary=summary,
        confidence=confidence,
    )
