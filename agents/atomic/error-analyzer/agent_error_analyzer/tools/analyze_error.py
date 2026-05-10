"""Error analysis tool — parse error messages, categorize, and suggest fixes.

Extracts error type, location, and context from error text. Matches against
known error patterns to provide actionable fix suggestions.
"""

from __future__ import annotations

import re

from agent_error_analyzer.models import AnalysisReport, FixSuggestion, StackFrame

# Error type to category mapping
_ERROR_CATEGORIES: dict[str, str] = {
    "SyntaxError": "syntax_error",
    "IndentationError": "syntax_error",
    "TabError": "syntax_error",
    "TypeError": "type_error",
    "AttributeError": "type_error",
    "ValueError": "value_error",
    "KeyError": "value_error",
    "IndexError": "value_error",
    "RuntimeError": "runtime_error",
    "NotImplementedError": "runtime_error",
    "StopIteration": "runtime_error",
    "ImportError": "import_error",
    "ModuleNotFoundError": "import_error",
    "FileNotFoundError": "io_error",
    "PermissionError": "io_error",
    "IsADirectoryError": "io_error",
    "FileExistsError": "io_error",
    "ConnectionError": "network_error",
    "TimeoutError": "network_error",
    "ConnectionRefusedError": "network_error",
    "ConnectionResetError": "network_error",
    "MemoryError": "memory_error",
    "RecursionError": "memory_error",
    "OSError": "os_error",
    "ZeroDivisionError": "arithmetic_error",
    "OverflowError": "arithmetic_error",
    "FloatingPointError": "arithmetic_error",
    "UnicodeDecodeError": "encoding_error",
    "UnicodeEncodeError": "encoding_error",
}

# Pattern-based fix suggestions
_FIX_PATTERNS: list[tuple[str, str, FixSuggestion]] = [
    (
        r"ModuleNotFoundError:\s*No module named ['\"](.+?)['\"]",
        "import_error",
        FixSuggestion(
            confidence="high",
            description="Missing Python package — install it",
            fix_example="pip install {package}",
        ),
    ),
    (
        r"FileNotFoundError:\s*\[Errno 2\].*?:\s*['\"](.+?)['\"]",
        "io_error",
        FixSuggestion(
            confidence="high",
            description="File or directory does not exist — check the path",
            fix_example="import os; assert os.path.exists('{path}')",
        ),
    ),
    (
        r"PermissionError:\s*\[Errno 13\]",
        "io_error",
        FixSuggestion(
            confidence="high",
            description="Insufficient file system permissions",
            fix_example="Check file/directory permissions or run with elevated privileges",
        ),
    ),
    (
        r"KeyError:\s*['\"](.+?)['\"]",
        "value_error",
        FixSuggestion(
            confidence="high",
            description="Dictionary key not found — check key existence",
            fix_example="value = d.get('{key}', default_value)",
        ),
    ),
    (
        r"TypeError:\s*.*unsupported operand type",
        "type_error",
        FixSuggestion(
            confidence="high",
            description="Type mismatch in operation — check operand types",
            fix_example="Ensure both operands are compatible types",
        ),
    ),
    (
        r"TypeError:\s*.*missing \d+ required positional argument",
        "type_error",
        FixSuggestion(
            confidence="high",
            description="Function call missing required arguments",
            fix_example="Check function signature and provide all required arguments",
        ),
    ),
    (
        r"TypeError:\s*.*got an unexpected keyword argument ['\"](.+?)['\"]",
        "type_error",
        FixSuggestion(
            confidence="high",
            description="Unknown keyword argument — check function signature",
            fix_example="Remove or rename the unexpected keyword argument '{arg}'",
        ),
    ),
    (
        r"AttributeError:\s*['\"](.+?)['\"] object has no attribute ['\"](.+?)['\"]",
        "type_error",
        FixSuggestion(
            confidence="high",
            description="Object does not have the requested attribute",
            fix_example="Check available attributes with dir(obj) or hasattr(obj, '{attr}')",
        ),
    ),
    (
        r"IndexError:\s*list index out of range",
        "value_error",
        FixSuggestion(
            confidence="high",
            description="List index exceeds list length",
            fix_example="if idx < len(my_list): value = my_list[idx]",
        ),
    ),
    (
        r"RecursionError:\s*maximum recursion depth exceeded",
        "memory_error",
        FixSuggestion(
            confidence="high",
            description="Infinite recursion — missing or wrong base case",
            fix_example="Add a base case to stop recursion, or use sys.setrecursionlimit()",
        ),
    ),
    (
        r"ZeroDivisionError:\s*division by zero",
        "arithmetic_error",
        FixSuggestion(
            confidence="high",
            description="Division by zero — guard the divisor",
            fix_example="if divisor != 0: result = numerator / divisor",
        ),
    ),
    (
        r"ConnectionRefusedError",
        "network_error",
        FixSuggestion(
            confidence="high",
            description="Target service not running or wrong port",
            fix_example="Verify the service is running and the port is correct",
        ),
    ),
    (
        r"TimeoutError",
        "network_error",
        FixSuggestion(
            confidence="medium",
            description="Operation timed out — increase timeout or check connectivity",
            fix_example="Increase timeout or add retry logic",
        ),
    ),
    (
        r"UnicodeDecodeError:\s*['\"](.+?)['\"] codec can't decode",
        "encoding_error",
        FixSuggestion(
            confidence="high",
            description="Encoding mismatch — specify the correct encoding",
            fix_example="open(file, encoding='utf-8') or try errors='replace'",
        ),
    ),
    (
        r"SyntaxError:\s*invalid syntax",
        "syntax_error",
        FixSuggestion(
            confidence="medium",
            description="Syntax error — check for missing colons, brackets, or typos",
            fix_example="Review the line for common syntax mistakes",
        ),
    ),
]


def _extract_error_type(text: str) -> str:
    """Extract the error class name from error text.

    Args:
        text: Error message or traceback text.

    Returns:
        Error type string (e.g. "TypeError") or empty string.
    """
    # Try "ErrorType: message" pattern at the end of traceback
    match = re.search(r"(\w+Error|\w+Exception):\s", text)
    if match:
        return match.group(1)
    return ""


def _extract_message(text: str, error_type: str) -> str:
    """Extract the error message portion.

    Args:
        text: Full error text.
        error_type: The extracted error type.

    Returns:
        The error message string.
    """
    if error_type:
        pattern = re.escape(error_type) + r":\s*(.+)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip().split("\n")[0]
    return text.strip().split("\n")[-1]


def _extract_stack_trace(text: str) -> list[StackFrame]:
    """Extract stack frames from Python traceback text.

    Args:
        text: Traceback text.

    Returns:
        List of StackFrame instances.
    """
    frames: list[StackFrame] = []
    # Match "File "path", line N, in func" or "File "path", line N"
    for match in re.finditer(r'File\s+"([^"]+)",\s*line\s+(\d+)(?:,\s*in\s+(\w+))?', text):
        frames.append(
            StackFrame(
                file=match.group(1),
                line=int(match.group(2)),
                function=match.group(3) or "",
            )
        )
    return frames


def _get_location(frames: list[StackFrame], error_type: str) -> str:
    """Get the primary error location.

    Args:
        frames: Extracted stack frames.
        error_type: The error type.

    Returns:
        Location string "file:line" or empty string.
    """
    if frames:
        last = frames[-1]
        return f"{last.file}:{last.line}"
    # Fallback: try to extract from error line like "line 42"
    return ""


def _generate_suggestions(text: str, error_type: str) -> list[FixSuggestion]:
    """Generate fix suggestions based on error patterns.

    Args:
        text: Full error text.
        error_type: The error type.

    Returns:
        List of FixSuggestion instances, ordered by confidence.
    """
    suggestions: list[FixSuggestion] = []

    for pattern, _category, template in _FIX_PATTERNS:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            suggestion = FixSuggestion(
                confidence=template.confidence,
                description=template.description,
                fix_example=template.fix_example.format(
                    package=groups[0] if groups else "",
                    path=groups[0] if groups else "",
                    key=groups[0] if groups else "",
                    arg=groups[0] if groups else "",
                    attr=groups[-1] if groups else "",
                ),
            )
            suggestions.append(suggestion)

    # Sort by confidence: high > medium > low
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: confidence_order.get(s.confidence, 3))

    return suggestions


def analyze_error(error_text: str, language: str = "auto") -> AnalysisReport:
    """Analyze an error message or stack trace.

    Extracts error type, location, and context. Matches against known patterns
    to provide actionable fix suggestions.

    Args:
        error_text: Error message or full traceback text.
        language: Language hint (currently only "python" / "auto" supported).

    Returns:
        AnalysisReport with error details and fix suggestions.
    """
    error_type = _extract_error_type(error_text)
    category = _ERROR_CATEGORIES.get(error_type, "unknown")
    message = _extract_message(error_text, error_type)
    stack_trace = _extract_stack_trace(error_text)
    location = _get_location(stack_trace, error_type)
    suggestions = _generate_suggestions(error_text, error_type)

    return AnalysisReport(
        error_type=error_type,
        category=category,
        location=location,
        message=message,
        stack_trace=stack_trace,
        suggestions=suggestions,
    )
