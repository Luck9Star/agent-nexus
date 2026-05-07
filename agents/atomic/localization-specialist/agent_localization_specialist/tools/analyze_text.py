"""Text analysis tool — detect register, domain, and key terms.

Analyzes source text to determine formality level (register), subject
domain, and identify key terminology that needs glossary support.
"""

from __future__ import annotations

import re

from agent_localization_specialist.models import TextAnalysis

# Formality indicators
_FORMAL_KEYWORDS = frozenset(
    {
        "hereby",
        "therefore",
        "pursuant",
        "notwithstanding",
        "aforementioned",
        "shall",
        "herein",
        "whereas",
        "forthwith",
        "therein",
        "accordance",
        "hereafter",
        "witnesseth",
        "indemnify",
        "obligate",
        "consequently",
        "尊敬的",
        "谨此",
        "兹",
        "特此",
        "根据",
        "鉴于",
        "如下",
    }
)

_INFORMAL_KEYWORDS = frozenset(
    {
        "hey",
        "gonna",
        "wanna",
        "cool",
        "awesome",
        "lol",
        "btw",
        "fyi",
        "omg",
        "ugh",
        "yep",
        "nope",
        "kinda",
        "sorta",
        "dunno",
        "嗨",
        "哈",
        "嘛",
        "啊",
        "呢",
        "呀",
        "嗯",
        "随便",
        "搞定",
    }
)

# Domain indicators
_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "tech": {
        "api",
        "framework",
        "deployment",
        "database",
        "server",
        "client",
        "algorithm",
        "protocol",
        "interface",
        "endpoint",
        "microservice",
        "container",
        "kubernetes",
        "docker",
        "repository",
        "pipeline",
        "authentication",
        "authorization",
        "infrastructure",
        "frontend",
        "backend",
    },
    "legal": {
        "plaintiff",
        "defendant",
        "jurisdiction",
        "liability",
        "statute",
        "regulation",
        "compliance",
        "litigation",
        "arbitration",
        "contract",
        "agreement",
        "indemnification",
        "warrant",
        "clause",
        "provision",
        "原告",
        "被告",
        "管辖权",
        "责任",
        "法规",
    },
    "medical": {
        "diagnosis",
        "symptom",
        "prescription",
        "treatment",
        "patient",
        "clinical",
        "therapy",
        "prognosis",
        "pathology",
        "pharmaceutical",
        "诊断",
        "症状",
        "处方",
        "治疗",
        "患者",
    },
    "business": {
        "revenue",
        "stakeholder",
        "roi",
        "quarterly",
        "fiscal",
        "profit",
        "margin",
        "acquisition",
        "merger",
        "valuation",
        "portfolio",
        "kpi",
        "benchmark",
        "strategy",
        "market",
    },
}

# Technical term pattern (camelCase, UPPER_CASE, or known tech terms)
_TECH_TERM_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:[A-Z][a-z]+)+|"  # camelCase
    r"[A-Z]{2,}(?:_[A-Z]+)*|"  # UPPER_CASE
    r"[A-Z]{2,5}\b|"  # Acronyms like API, SQL, HTML
    r"[a-z]+(?:_[a-z]+)+)"  # snake_case
)


def analyze_text(text: str, source_lang: str = "en") -> TextAnalysis:
    """Analyze source text for localization preparation.

    Detects the register (formality level), domain, key terms, and
    complexity of the text to inform the translation process.

    Args:
        text: Source text to analyze.
        source_lang: Source language code (used for register detection heuristics).

    Returns:
        TextAnalysis with register, domain, key terms, and complexity.
    """
    if not text.strip():
        return TextAnalysis(formality="neutral", domain="general", complexity="low")

    text_lower = text.lower()
    words = set(re.findall(r"\b\w+\b", text_lower))

    formality = _detect_register(text_lower, words)
    domain = _detect_domain(words)
    key_terms = _extract_key_terms(text, words, domain)
    complexity = _assess_complexity(text, key_terms, formality)

    return TextAnalysis(
        formality=formality,
        domain=domain,
        key_terms=key_terms,
        complexity=complexity,
    )


def _detect_register(text_lower: str, words: set[str]) -> str:
    """Detect the formality register of text."""
    formal_count = len(words & _FORMAL_KEYWORDS)
    informal_count = len(words & _INFORMAL_KEYWORDS)

    if formal_count > informal_count and formal_count >= 2:
        return "formal"
    if informal_count > formal_count and informal_count >= 2:
        return "informal"
    if formal_count > 0 and informal_count == 0:
        return "formal"
    if informal_count > 0 and formal_count == 0:
        return "informal"
    return "neutral"


def _detect_domain(words: set[str]) -> str:
    """Detect the subject domain from word usage."""
    best_domain = "general"
    best_score = 0

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = len(words & keywords)
        if score > best_score:
            best_score = score
            best_domain = domain

    return best_domain


def _extract_key_terms(text: str, words: set[str], domain: str) -> list[str]:
    """Extract key terms that likely need glossary support."""
    terms: set[str] = set()

    # Add domain-specific terms found in text
    if domain in _DOMAIN_KEYWORDS:
        domain_terms = words & _DOMAIN_KEYWORDS[domain]
        terms.update(domain_terms)

    # Add technical-looking terms (camelCase, acronyms, etc.)
    tech_matches = _TECH_TERM_RE.findall(text)
    for match in tech_matches:
        if len(match) >= 2:
            terms.add(match)

    # Sort for deterministic output
    return sorted(terms)


def _assess_complexity(text: str, key_terms: list[str], formality: str) -> str:
    """Assess translation complexity."""
    score = 0

    # Longer texts are more complex
    word_count = len(text.split())
    if word_count > 100:
        score += 2
    elif word_count > 30:
        score += 1

    # More key terms = more complex
    if len(key_terms) > 10:
        score += 2
    elif len(key_terms) > 5:
        score += 1

    # Formal formality adds complexity
    if formality == "formal":
        score += 1

    # Mixed punctuation or complex sentence structure
    if text.count(";") + text.count(":") > 3:
        score += 1

    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"
