"""Compliance checking tool — verify clauses against jurisdiction regulations.

Checks contract clauses for compliance with jurisdiction-specific legal
requirements. Supports CN, US, UK, EU, HK, SG jurisdictions with a
pluggable rule system.
"""

from __future__ import annotations

from typing import Sequence

from agent_contract_analyzer.models import ClauseInfo, ComplianceReport

# Jurisdiction-specific mandatory clause types
MANDATORY_CLAUSE_TYPES: dict[str, list[str]] = {
    "CN": ["governing_law", "payment"],
    "US": ["governing_law", "indemnification"],
    "UK": ["governing_law", "termination"],
    "EU": ["governing_law", "termination", "confidentiality"],
    "HK": ["governing_law"],
    "SG": ["governing_law", "payment"],
}

# Jurisdiction-specific content requirements
CONTENT_REQUIREMENTS: dict[str, list[dict]] = {
    "CN": [
        {
            "type": "payment",
            "required_keywords": ["价格", "付款", "支付"],
            "en_required_keywords": ["price", "payment", "pay"],
            "message": "付款条款缺少明确的支付金额或方式",
        },
    ],
    "US": [
        {
            "type": "indemnification",
            "required_keywords": [],
            "en_required_keywords": ["indemnif", "liability"],
            "message": "Missing indemnification or liability limitation clause",
        },
    ],
    "UK": [
        {
            "type": "termination",
            "required_keywords": [],
            "en_required_keywords": ["terminat", "notice period"],
            "message": "Termination clause should specify notice period",
        },
    ],
    "EU": [
        {
            "type": "confidentiality",
            "required_keywords": ["保密"],
            "en_required_keywords": ["confidential", "data protection"],
            "message": "Missing data protection or confidentiality requirements",
        },
    ],
    "HK": [],
    "SG": [],
}

# Supported jurisdictions
SUPPORTED_JURISDICTIONS = {"CN", "US", "UK", "EU", "HK", "SG"}


def _check_mandatory_types(
    clauses: Sequence[ClauseInfo], jurisdiction: str
) -> list[str]:
    """Check for missing mandatory clause types for the jurisdiction."""
    required_types = MANDATORY_CLAUSE_TYPES.get(jurisdiction, [])
    existing_types = {c.type for c in clauses}
    missing: list[str] = []

    for required in required_types:
        if required not in existing_types:
            missing.append(f"缺少必需的{required}类型条款")

    return missing


def _check_content_requirements(
    clauses: Sequence[ClauseInfo], jurisdiction: str
) -> list[str]:
    """Check content-level requirements for the jurisdiction."""
    requirements = CONTENT_REQUIREMENTS.get(jurisdiction, [])
    violations: list[str] = []

    for req in requirements:
        # Find clauses of the required type
        matching_clauses = [c for c in clauses if c.type == req["type"]]

        if not matching_clauses:
            # If the type doesn't exist, the mandatory check already caught it
            continue

        # Check if any matching clause contains required keywords
        all_keywords = req.get("required_keywords", []) + req.get("en_required_keywords", [])
        if not all_keywords:
            continue
        matching_contents_lower = [c.content.lower() for c in matching_clauses]
        has_keywords = any(
            any(kw.lower() in content_lower for kw in all_keywords)
            for content_lower in matching_contents_lower
        )

        if all_keywords and not has_keywords:
            violations.append(req["message"])

    return violations


def _generate_suggestions(violations: list[str], jurisdiction: str) -> list[str]:
    """Generate compliance improvement suggestions."""
    suggestions: list[str] = []

    if not violations:
        suggestions.append(f"合同条款符合 {jurisdiction} 管辖区的基本合规要求")
    else:
        suggestions.append(f"建议咨询 {jurisdiction} 管辖区的法律顾问以解决合规问题")
        for violation in violations:
            suggestions.append(f"  - {violation}")

    return suggestions


def check_compliance(clauses: list[ClauseInfo], jurisdiction: str) -> ComplianceReport:
    """Check clauses against jurisdiction-specific regulations.

    Validates that the contract clauses include all mandatory clause types
    and meet content requirements for the specified jurisdiction.

    Args:
        clauses: List of extracted ClauseInfo to check.
        jurisdiction: Jurisdiction code (e.g. "CN", "US", "UK", "EU", "HK", "SG").

    Returns:
        ComplianceReport with compliance status, violations, and suggestions.
    """
    jurisdiction = jurisdiction.upper().strip()

    if jurisdiction not in SUPPORTED_JURISDICTIONS:
        return ComplianceReport(
            compliant=False,
            violations=[f"不支持的管辖区: {jurisdiction}。支持: {', '.join(sorted(SUPPORTED_JURISDICTIONS))}"],
            suggestions=[f"请使用以下管辖区代码之一: {', '.join(sorted(SUPPORTED_JURISDICTIONS))}"],
        )

    if not clauses:
        return ComplianceReport(
            compliant=False,
            violations=["无条款可供分析"],
            suggestions=["请先提取合同条款"],
        )

    missing_violations = _check_mandatory_types(clauses, jurisdiction)
    content_violations = _check_content_requirements(clauses, jurisdiction)

    all_violations = missing_violations + content_violations
    suggestions = _generate_suggestions(all_violations, jurisdiction)
    is_compliant = len(all_violations) == 0

    return ComplianceReport(
        compliant=is_compliant,
        violations=all_violations,
        suggestions=suggestions,
    )
