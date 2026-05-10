"""Data models for error-analyzer Agent.

Pydantic v2 frozen models for error analysis, categorization, and fix suggestions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FixSuggestion(BaseModel):
    """A suggested fix for an analyzed error.

    Attributes:
        confidence: Confidence level — high, medium, or low.
        description: Human-readable description of the suggested fix.
        fix_example: Optional code snippet demonstrating the fix.
    """

    model_config = ConfigDict(frozen=True)

    confidence: str = "medium"
    description: str = ""
    fix_example: str = ""


class StackFrame(BaseModel):
    """A single frame from a stack trace.

    Attributes:
        file: File path.
        line: Line number.
        function: Function name.
        code: Source code line (if available).
    """

    model_config = ConfigDict(frozen=True)

    file: str = ""
    line: int = 0
    function: str = ""
    code: str = ""


class AnalysisReport(BaseModel):
    """Result of analyzing an error message or stack trace.

    Attributes:
        error_type: The error class name (e.g. "TypeError", "FileNotFoundError").
        category: Error category (e.g. "type_error", "io_error").
        location: Primary location (file:line).
        message: The error message text.
        stack_trace: Extracted stack frames.
        suggestions: Fix suggestions ordered by confidence.
    """

    model_config = ConfigDict(frozen=True)

    error_type: str = ""
    category: str = "unknown"
    location: str = ""
    message: str = ""
    stack_trace: list[StackFrame] = Field(default_factory=list)
    suggestions: list[FixSuggestion] = Field(default_factory=list)
