"""Requirement analysis tool -- parse text and identify gaps, ambiguities, priorities.

Uses rule-based pattern matching to extract key information from requirement text.
"""

from __future__ import annotations

import re

from agent_requirements_analyzer.models import RequirementAnalysis

# Patterns for detecting ambiguous language
AMBIGUITY_PATTERNS = [
    re.compile(r"(?:应该|should)\s*(?:能?够?|can|be able to)", re.IGNORECASE),
    re.compile(r"(?:可能|maybe|perhaps|might|probably)", re.IGNORECASE),
    re.compile(r"(?:等等|etc\.?|and so on|等等)", re.IGNORECASE),
    re.compile(r"(?:快|fast|slow|性能好|高效|efficient)", re.IGNORECASE),
    re.compile(r"(?:用户友好|user.?(?:friendly|intuitive))", re.IGNORECASE),
    re.compile(r"(?:灵活|flexible|可扩展|scalable)", re.IGNORECASE),
    re.compile(r"(?:安全|secure|safe)", re.IGNORECASE),
    re.compile(r"(?:稳定|stable|reliable|可靠)", re.IGNORECASE),
]

# Patterns for gap detection -- things that are typically needed but missing
GAP_INDICATORS = {
    "missing_user_role": re.compile(r"(?:用户|user)", re.IGNORECASE),
    "missing_error_handling": re.compile(r"(?:功能|feature|操作|operation)", re.IGNORECASE),
    "missing_performance": re.compile(
        r"(?:响应|response|处理|process|并发|concurrent)", re.IGNORECASE
    ),
}

# Pre-compiled regexes for gap detection inner checks
_ROLE_DETAIL_RE = re.compile(
    r"(?:角色|管理员|普通用户|admin|role|manager|operator|游客|guest)",
    re.IGNORECASE,
)
_ERROR_DETAIL_RE = re.compile(
    r"(?:错误|异常|失败|失败处理|error|exception|fail|retry|重试)",
    re.IGNORECASE,
)
_PERF_DETAIL_RE = re.compile(
    r"(?:秒|ms|毫秒|性能指标|QPS|TPS|延迟|latency|throughput)",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"(?:登录|注册|认证|授权|权限|auth|login|register|permission)",
    re.IGNORECASE,
)
_AUTH_DETAIL_RE = re.compile(
    r"(?:密码|OAuth|手机|验证码|token|JWT|SSO|单点登录|password|captcha)",
    re.IGNORECASE,
)
_DATA_RE = re.compile(
    r"(?:数据|存储|数据库|保存|data|storage|database|save|store)",
    re.IGNORECASE,
)
_DATA_DETAIL_RE = re.compile(
    r"(?:字段|表|模型|schema|field|table|model|entity|实体)",
    re.IGNORECASE,
)

# Priority keywords
HIGH_PRIORITY_KEYWORDS = [
    "必须",
    "关键",
    "核心",
    "重要",
    "must",
    "critical",
    "essential",
    "key",
    "important",
    "required",
    "登录",
    "注册",
    "支付",
    "安全",
    "login",
    "register",
    "payment",
    "security",
    "auth",
]
MEDIUM_PRIORITY_KEYWORDS = [
    "应该",
    "需要",
    "支持",
    "should",
    "need",
    "support",
    "管理",
    "manage",
    "查询",
    "search",
    "导出",
    "export",
]
LOW_PRIORITY_KEYWORDS = [
    "可以",
    "建议",
    "最好",
    "nice.to.have",
    "could",
    "optional",
    "如果可能",
    "未来",
    "future",
    "扩展",
    "enhancement",
]


def _detect_ambiguities(text: str) -> list[str]:
    """Detect ambiguous language patterns in the text."""
    ambiguities: list[str] = []
    for pattern in AMBIGUITY_PATTERNS:
        match = pattern.search(text)
        if match:
            ambiguities.append(f"模糊表述: '{match.group()}'")
    return ambiguities


def _detect_gaps(text: str) -> list[str]:
    """Identify missing information based on requirement content."""
    gaps: list[str] = []

    # Check for missing role/actor definition
    if GAP_INDICATORS["missing_user_role"].search(text):
        has_role = bool(_ROLE_DETAIL_RE.search(text))
        if not has_role:
            gaps.append("缺少用户角色定义")

    # Check for missing error handling mention
    if GAP_INDICATORS["missing_error_handling"].search(text):
        has_error = bool(_ERROR_DETAIL_RE.search(text))
        if not has_error:
            gaps.append("缺少错误处理说明")

    # Check for missing performance requirements
    if GAP_INDICATORS["missing_performance"].search(text):
        has_perf = bool(_PERF_DETAIL_RE.search(text))
        if not has_perf:
            gaps.append("缺少性能指标定义")

    # Check for missing authentication/authorization
    has_auth = bool(_AUTH_RE.search(text))
    has_auth_detail = bool(_AUTH_DETAIL_RE.search(text))
    if has_auth and not has_auth_detail:
        gaps.append("缺少认证方式详细说明")

    # Check for missing data model
    has_data = bool(_DATA_RE.search(text))
    has_data_detail = bool(_DATA_DETAIL_RE.search(text))
    if has_data and not has_data_detail:
        gaps.append("缺少数据模型定义")

    return gaps


def _extract_key_terms(text: str) -> list[str]:
    """Extract key terms from the requirement text."""
    # Extract quoted terms
    quoted = re.findall(r'[""\u201c](.+?)[""\u201d]', text)
    # Extract technical terms (CamelCase, snake_case with 3+ chars)
    technical = re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text)
    technical += re.findall(r"\b(\w{3,}(?:_\w{2,})+)\b", text)
    # Deduplicate while preserving order
    seen: set[str] = set()
    terms: list[str] = []
    for term in quoted + technical:
        if term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def _categorize_priorities(text: str) -> dict[str, list[str]]:
    """Categorize requirement sentences by priority."""
    priorities: dict[str, list[str]] = {"high": [], "medium": [], "low": []}

    # Split into sentences
    sentences = re.split(r"[。！？.!?\n]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 2]

    for sentence in sentences:
        s_lower = sentence.lower()
        is_high = any(kw in s_lower for kw in HIGH_PRIORITY_KEYWORDS)
        is_low = any(kw in s_lower for kw in LOW_PRIORITY_KEYWORDS)

        if is_high:
            priorities["high"].append(sentence)
        elif is_low:
            priorities["low"].append(sentence)
        else:
            # Check medium keywords or default to medium
            is_medium = any(kw in s_lower for kw in MEDIUM_PRIORITY_KEYWORDS)
            if is_medium or not is_high:
                priorities["medium"].append(sentence)

    return priorities


def _detect_contradictions(text: str) -> list[str]:
    """Detect potential contradictions in the requirement text."""
    contradictions: list[str] = []

    # Check for conflicting time requirements
    if re.search(r"实时|real.?[Tt]ime", text, re.IGNORECASE) and re.search(
        r"批量|batch|异步|async", text, re.IGNORECASE
    ):
        contradictions.append("同时要求实时处理和批量/异步处理")

    # Check for conflicting access requirements
    if re.search(r"公开|public|匿名|anonymous", text, re.IGNORECASE) and re.search(
        r"私密|private|仅限|restricted", text, re.IGNORECASE
    ):
        contradictions.append("同时要求公开访问和私密访问")

    return contradictions


def analyze_requirements(text: str) -> RequirementAnalysis:
    """Analyze a requirement text to identify gaps, ambiguities, and priorities.

    Args:
        text: The requirement text to analyze.

    Returns:
        RequirementAnalysis with identified gaps, ambiguities, priorities,
        key terms, and contradictions.
    """
    if not text or not text.strip():
        return RequirementAnalysis(
            text=text or "",
            gaps=["No input text provided"],
        )

    gaps = _detect_gaps(text)
    ambiguities = _detect_ambiguities(text)
    priorities = _categorize_priorities(text)
    key_terms = _extract_key_terms(text)
    contradictions = _detect_contradictions(text)

    return RequirementAnalysis(
        text=text,
        gaps=gaps,
        ambiguities=ambiguities,
        priorities=priorities,
        key_terms=key_terms,
        contradictions=contradictions,
    )
