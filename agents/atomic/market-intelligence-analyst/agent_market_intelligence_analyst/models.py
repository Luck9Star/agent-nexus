"""Data models for market-intelligence-analyst Agent.

Pydantic v2 frozen models for market analysis, trend identification,
and briefing generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MarketAnalysis(BaseModel):
    """Result of applying an analytical framework to market data.

    Attributes:
        framework: Name of the analysis framework used.
        factors: Analysis results for each framework factor.
        scores: Score (1-5) for each factor.
        insights: Key insights extracted from the analysis.
    """

    model_config = ConfigDict(frozen=True)

    framework: str
    factors: dict[str, str] = Field(default_factory=dict)
    scores: dict[str, int] = Field(default_factory=dict)
    insights: list[str] = Field(default_factory=list)


class TrendItem(BaseModel):
    """A single identified market trend.

    Attributes:
        name: Name of the trend.
        direction: Trend direction (up/down/stable).
        impact: Impact level (high/medium/low).
        evidence: Supporting evidence for the trend.
        timeframe: Expected timeframe for the trend.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    direction: str = "stable"
    impact: str = "medium"
    evidence: str = ""
    timeframe: str = ""


class TrendReport(BaseModel):
    """Result of trend identification analysis.

    Attributes:
        trends: All identified trends.
        summary: Summary of trend findings.
        confidence: Analysis confidence score (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True)

    trends: list[TrendItem] = Field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0


class BriefingReport(BaseModel):
    """Generated market intelligence briefing.

    Attributes:
        title: Briefing title.
        executive_summary: High-level summary of findings.
        sections: Detailed sections of the briefing.
        recommendations: Strategic recommendations based on analysis.
    """

    model_config = ConfigDict(frozen=True)

    title: str = ""
    executive_summary: str = ""
    sections: dict[str, str] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)
