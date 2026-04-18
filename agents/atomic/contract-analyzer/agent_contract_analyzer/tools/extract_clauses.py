"""Clause extraction tool — identify and categorize contract clauses.

Parses contract text to detect clause boundaries using common numbering
patterns, classifies clause types, and extracts dependencies, obligations,
and party references.
"""

from __future__ import annotations

import re
from typing import Sequence

from agent_contract_analyzer.models import ClauseInfo

# Clause numbering patterns (ordered by specificity)
CLAUSE_PATTERNS = [
    # Chinese: 第一条, 第一条, etc.
    re.compile(r"^第([一二三四五六七八九十百千\d]+)条\b", re.MULTILINE),
    # English: Article I, Article II, Section 1, Section 2, etc.
    re.compile(r"^(?:Article|Section)\s+([IVXLCDM\d]+[a-z]?)\b", re.MULTILINE | re.IGNORECASE),
    # Numbered: 1., 2., 3.1, 3.1.1, etc.
    re.compile(r"^(\d+(?:\.\d+)*)[.\s]\s", re.MULTILINE),
    # Parenthesized: (a), (b), (1), (2)
    re.compile(r"^\((\d+)\)\s", re.MULTILINE),
]

# Clause type keywords mapping
TYPE_KEYWORDS: dict[str, list[str]] = {
    "definition": ["定义", "解释", "definition", "interpretation", "含义", "means"],
    "obligation": ["义务", "责任", "obligation", "responsibility", "shall", "应当", "应"],
    "condition": ["条件", "前提", "condition", "precedent", "先决条件"],
    "representation": ["声明", "保证", "representation", "warranty", "保证"],
    "indemnification": ["赔偿", "补偿", "indemnif", "hold harmless"],
    "termination": ["终止", "解除", "termination", "cancel", "解除"],
    "governing_law": ["适用法律", "管辖", "governing law", "jurisdiction", "争议解决"],
    "confidentiality": ["保密", "confidential", "non-disclosure", "nda"],
    "payment": ["付款", "费用", "payment", "fee", "price", "报酬", "对价"],
}

# Dependency reference patterns
DEP_PATTERNS = [
    re.compile(r"第([一二三四五六七八九十百千\d]+)条"),
    re.compile(r"(?:Article|Section)\s+([IVXLCDM\d]+[a-z]?)", re.IGNORECASE),
    re.compile(r"第(\d+(?:\.\d+)*)[条节款]"),
    re.compile(r"(\d+(?:\.\d+)*)[条节款]"),
]

# Party patterns
PARTY_PATTERNS = [
    re.compile(r"(?:甲方|乙方|丙方|Party\s+A|Party\s+B|甲方|乙方)"),
    re.compile(r"(?:买方|卖方|Buyer|Seller)"),
    re.compile(r"(?:出租方|承租方|Lessor|Lessee)"),
    re.compile(r"(?:雇主|承包商|Employer|Contractor)"),
]


# Type priority for tiebreaking (higher = preferred when counts are equal)
TYPE_PRIORITY: dict[str, int] = {
    "governing_law": 10,
    "indemnification": 9,
    "confidentiality": 8,
    "termination": 7,
    "payment": 6,
    "representation": 5,
    "condition": 4,
    "obligation": 3,
    "definition": 2,
    "other": 0,
}


def _classify_type(text: str) -> str:
    """Classify clause type based on keyword matching with priority tiebreaking."""
    text_lower = text.lower()
    best_type = "other"
    best_count = 0
    best_priority = 0

    for clause_type, keywords in TYPE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in text_lower)
        priority = TYPE_PRIORITY.get(clause_type, 0)
        if count > best_count or (count == best_count and count > 0 and priority > best_priority):
            best_count = count
            best_priority = priority
            best_type = clause_type

    return best_type


def _extract_dependencies(text: str, current_id: str) -> list[str]:
    """Extract references to other clauses from text."""
    deps: list[str] = []
    seen: set[str] = set()

    for pattern in DEP_PATTERNS:
        for match in pattern.finditer(text):
            ref_id = match.group(1)
            if ref_id != current_id and ref_id not in seen:
                seen.add(ref_id)
                deps.append(ref_id)

    return deps


def _extract_obligations(text: str) -> list[str]:
    """Extract obligation statements from clause text."""
    obligations: list[str] = []

    # Chinese obligation patterns
    obligation_re_cn = re.compile(r"[^。；\n]*[应当应须必须][^。；\n]*[。；]?", re.UNICODE)
    for match in obligation_re_cn.finditer(text):
        sentence = match.group(0).strip()
        if len(sentence) > 5:
            obligations.append(sentence)

    # English obligation patterns
    obligation_re_en = re.compile(
        r"[^.]*\b(?:shall|must|will|is required to|is obligated to)\b[^.]*\.?",
        re.IGNORECASE,
    )
    for match in obligation_re_en.finditer(text):
        sentence = match.group(0).strip()
        if len(sentence) > 10:
            obligations.append(sentence)

    return obligations[:10]  # Limit to 10 most relevant


def _extract_parties(text: str) -> list[str]:
    """Extract party references from clause text."""
    parties: list[str] = []
    seen: set[str] = set()

    for pattern in PARTY_PATTERNS:
        for match in pattern.finditer(text):
            party = match.group(0)
            if party not in seen:
                seen.add(party)
                parties.append(party)

    return parties


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split contract text into (section_id, section_text) pairs."""
    for pattern in CLAUSE_PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) >= 2:
            sections: list[tuple[str, str]] = []
            for i, match in enumerate(matches):
                section_id = match.group(1)
                start = match.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                section_text = text[start:end].strip()
                sections.append((section_id, section_text))
            if sections:
                return sections

    # Fallback: treat entire text as a single clause
    return [("1", text.strip())]


def extract_clauses(text: str) -> list[ClauseInfo]:
    """Extract and categorize clauses from contract text.

    Splits contract text into individual clauses using numbering patterns,
    classifies each clause by type, and extracts dependencies, obligations,
    and party references.

    Args:
        text: Full contract text to analyze.

    Returns:
        List of ClauseInfo with all identified clauses.
    """
    if not text or not text.strip():
        return []

    sections = _split_into_sections(text)
    clauses: list[ClauseInfo] = []

    for section_id, section_text in sections:
        if not section_text.strip():
            continue

        clause_type = _classify_type(section_text)
        dependencies = _extract_dependencies(section_text, section_id)
        obligations = _extract_obligations(section_text)
        parties = _extract_parties(section_text)

        clauses.append(
            ClauseInfo(
                clause_id=section_id,
                type=clause_type,
                content=section_text,
                dependencies=dependencies,
                obligations=obligations,
                parties=parties,
            )
        )

    return clauses
