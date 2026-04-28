"""Python Runtime: in-process code execution for Agent Nexus.

Public API:
    PythonRuntime       — High-level runtime with Variable/Function/Type management
    IPythonExecutor     — Low-level IPython shell executor
    SecurityChecker     — AST-level code safety analysis
    PermissionChecker   — Runtime permission evaluator for agent tool calls
    TieredRuntimeDescriber — L0-L3 context generation for LLM injection
    TokenTracker        — Session-scoped token usage monitor with tiered alerts

Security rules:
    ImportRule          — Block forbidden module imports
    FunctionRule        — Block forbidden function calls
    AttributeRule       — Block forbidden attribute access
    RegexRule           — Catch-all regex patterns
    SecurityRule        — Abstract base for custom rules
"""

from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    SecurityViolation,
    Variable,
)

from .describer import TieredRuntimeDescriber
from .executor import IPythonExecutor
from .permission_checker import PermissionChecker
from .runtime import PythonRuntime
from .security_checker import SecurityChecker
from .security_rules import (
    AttributeRule,
    FunctionRule,
    ImportRule,
    RegexRule,
    SecurityRule,
)
from .token_tracker import TokenAlert, TokenTracker

__all__ = [
    # High-level API
    "PythonRuntime",
    "IPythonExecutor",
    "SecurityChecker",
    "PermissionChecker",
    "TieredRuntimeDescriber",
    "TokenTracker",
    "TokenAlert",
    # Security rules
    "SecurityRule",
    "ImportRule",
    "FunctionRule",
    "AttributeRule",
    "RegexRule",
    # Data models (re-exported for convenience)
    "Variable",
    "Function",
    "RuntimeType",
    "ExecutionResult",
    "SecurityViolation",
]
