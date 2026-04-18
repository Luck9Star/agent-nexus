"""agent-doc-filler — Word document template filling specialist.

A two-phase agent that analyzes .docx templates to identify {{placeholder}}
patterns, then fills them with actual values while preserving document styles.
"""

from agent_doc_filler.agent import DocFillerAgent
from agent_doc_filler.models import (
    FillRequest,
    FillResult,
    PlaceholderInfo,
    TemplateAnalysis,
)

__all__ = [
    "DocFillerAgent",
    "FillRequest",
    "FillResult",
    "PlaceholderInfo",
    "TemplateAnalysis",
]
