"""PermissionChecker: runtime permission evaluator for agent tool calls and file access.

Evaluation order (each step can override previous):
    1. denied_tools (blacklist) — immediate deny
    2. allowed_tools (whitelist) — immediate allow (unless denied)
    3. path_rules (glob pattern matching) — for file-related tools
    4. mode baseline (DEFAULT/PLAN/FULL_AUTO)
    5. Read-only tools are always allowed (unless explicitly denied)
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re

from agent_nexus.models.permission import (
    PathAccess,
    PermissionConfig,
    PermissionDecision,
    PermissionMode,
)

logger = logging.getLogger(__name__)

# Tools that are inherently read-only and safe to allow without confirmation.
READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "grep",
        "glob",
        "list",
        "search",
        "info",
        "agent_info",
        "list_agents",
        "search_and_activate",
    }
)

# Built-in sensitive paths that are ALWAYS denied regardless of user config.
# These cannot be overridden by path_rules.
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.config/gcloud/**",
    "~/.azure/**",
    "~/.gnupg/**",
    "~/.docker/**",
    "~/.kube/**",
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/ssh/**",
    "*.env",
    "*.pem",
    "*.key",
)


def _expand_user(path: str) -> str:
    """Expand ~ and resolve to absolute canonical path.

    Resolves ``..`` components to prevent path traversal attacks that
    could bypass sensitive path protection (e.g. ``../../.ssh/id_rsa``).
    """
    return os.path.abspath(os.path.expanduser(path))


# Pre-expanded sensitive path patterns — computed once at import time
# to avoid repeated os.path.abspath/expanduser calls in the hot path.
_EXPANDED_PREFIX_PATTERNS: tuple[str, ...] = tuple(
    _expand_user(p[:-3])  # strip trailing /**
    for p in SENSITIVE_PATH_PATTERNS
    if p.endswith("/**")
)
_EXPANDED_FULL_PATTERNS: tuple[str, ...] = tuple(
    _expand_user(p)
    for p in SENSITIVE_PATH_PATTERNS
    if "/" in p and not p.endswith("/**")
)
_BASENAME_PATTERNS: tuple[str, ...] = tuple(
    p for p in SENSITIVE_PATH_PATTERNS if "/" not in p
)

# Shell commands considered dangerous in DEFAULT mode.
_DANGEROUS_COMMAND_PATTERNS: tuple[str, ...] = (
    "rm ",
    "rm -",
    "sudo ",
    "chmod ",
    "chown ",
    "mkfs.",
    "dd ",
    "> /dev/",
    "curl ",
    "wget ",
    "ssh ",
    "scp ",
)

# Pre-compiled regex patterns derived from _DANGEROUS_COMMAND_PATTERNS.
_COMPILED_DANGEROUS_PATTERNS: list[re.Pattern[str]] = []

for _raw in _DANGEROUS_COMMAND_PATTERNS:
    _escaped = re.escape(_raw.strip())
    _COMPILED_DANGEROUS_PATTERNS.append(re.compile(rf"(?:^|[|&;])\s*{_escaped}\b"))


def _fnmatch_recursive(value: str, pattern: str) -> bool:
    """Match *value* against *pattern*, supporting recursive ``**`` globs.

    ``fnmatch`` does not handle ``**`` (match across directory separators).
    This helper expands ``**`` so that it matches zero or more intermediate
    path segments.

    Supported patterns::

        /tmp/**        — /tmp, /tmp/a, /tmp/a/b/c
        /tmp/**/*.txt  — /tmp/foo.txt, /tmp/a/b/c.txt
        /tmp/**/bar/*  — /tmp/bar/x, /tmp/a/bar/x, /tmp/a/b/bar/x
    """
    if "/**" not in pattern:
        return fnmatch.fnmatch(value, pattern)

    idx = pattern.index("/**")
    prefix = pattern[:idx]
    remainder = pattern[idx + 3:]  # after the /**
    # remainder examples: "" | "/*.txt" | "/bar/*"

    if not remainder:
        # Simple /** — prefix matches value or any descendant.
        return value == prefix or value.startswith(prefix + "/")

    # value must start with the prefix (or be the prefix itself).
    if value != prefix and not value.startswith(prefix + "/"):
        return False

    # Tail is everything after the prefix in value (starts with "/").
    if value == prefix:
        tail = ""
    else:
        tail = value[len(prefix) :]  # e.g. "/a/b/c.txt"

    # ``**`` matches zero or more path segments.
    # Walk every possible split point in *tail* and check if the
    # remainder matches the suffix via plain fnmatch.
    # Split positions: start of each path segment.
    positions = [0]
    for i, ch in enumerate(tail):
        if ch == "/" and i + 1 < len(tail):
            positions.append(i + 1)

    for pos in positions:
        suffix = tail[pos:]  # e.g. "c.txt", "b/c.txt"
        if _fnmatch_recursive(suffix, remainder.lstrip("/")):
            return True

    # Also try matching the entire tail (including leading "/")
    # for patterns like "/*.txt" where fnmatch may handle the "/".
    # Use recursive call so patterns with additional /** segments
    # (e.g. /tmp/**/bar/**) are handled correctly.
    return _fnmatch_recursive(tail, remainder)


def _matches_any_pattern(value: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """Check whether *value* matches any of the given fnmatch patterns."""
    for pattern in patterns:
        if _fnmatch_recursive(value, pattern):
            return True
    return False


def _is_sensitive_path(path: str) -> bool:
    """Return True if *path* matches a built-in sensitive path pattern."""
    expanded = _expand_user(path)
    for prefix in _EXPANDED_PREFIX_PATTERNS:
        if expanded.startswith(prefix + "/") or expanded == prefix:
            return True
    for pattern in _EXPANDED_FULL_PATTERNS:
        if fnmatch.fnmatch(expanded, pattern):
            return True
    # Check basename for glob-only patterns like *.env, *.pem, *.key
    basename = os.path.basename(expanded)
    for pattern in _BASENAME_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            return True
    return False


def _is_write_tool(tool_name: str) -> bool:
    """Heuristic: a tool is considered a 'write' tool if it is not read-only."""
    return tool_name not in READONLY_TOOLS


class PermissionChecker:
    """Runtime permission evaluator for agent tool calls and file access.

    Usage::

        from agent_nexus.models.permission import PermissionConfig, PermissionMode

        config = PermissionConfig(mode=PermissionMode.DEFAULT)
        checker = PermissionChecker(config)

        decision = checker.check_tool("file_read")
        assert decision.allowed

        decision = checker.check_tool("file_write")
        assert decision.allowed and decision.requires_confirmation
    """

    def __init__(self, config: PermissionConfig) -> None:
        self._config = config
        # Pre-expand path rules to avoid repeated _expand_user calls in hot path
        self._expanded_path_rules: list[tuple[str, PathAccess]] = [
            (_expand_user(rule.pattern), rule.access)
            for rule in config.path_rules
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_tool(self, tool_name: str) -> PermissionDecision:
        """Check whether *tool_name* is permitted.

        Evaluation order:
            1. denied_tools (blacklist, with glob support)
            2. allowed_tools whitelist (if non-empty, with glob support)
            3. Read-only tool exemption
            4. Mode baseline (PLAN / FULL_AUTO / DEFAULT)
        """
        # 1. denied_tools — immediate deny
        if _matches_any_pattern(tool_name, self._config.denied_tools):
            return PermissionDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is in denied_tools list",
            )

        # 2. allowed_tools whitelist — if configured, tool must match
        if self._config.allowed_tools:
            if not _matches_any_pattern(tool_name, self._config.allowed_tools):
                return PermissionDecision(
                    allowed=False,
                    reason=f"Tool '{tool_name}' not in allowed_tools",
                )
            # Tool matched the whitelist — allow immediately
            return PermissionDecision(allowed=True, reason="Allowed by allowed_tools")

        # 3. Read-only tools always allowed (unless already denied above)
        if tool_name in READONLY_TOOLS:
            return PermissionDecision(allowed=True, reason="Read-only tool")

        # 4. Mode baseline
        return self._check_mode_baseline(tool_name)

    def check_path(self, tool_name: str, path: str) -> PermissionDecision:
        """Check whether *tool_name* may access *path*.

        Runs ``check_tool`` first. If denied, returns that decision.
        Then checks built-in sensitive paths (always denied), followed by
        user-configured ``path_rules`` (first matching rule wins).
        """
        # 1. Tool-level check first
        tool_decision = self.check_tool(tool_name)
        if not tool_decision.allowed:
            return tool_decision

        # 2. Built-in sensitive paths — always denied, cannot be overridden
        if _is_sensitive_path(path):
            return PermissionDecision(
                allowed=False,
                reason=f"Path '{path}' is a sensitive system path and is always denied",
            )

        # 3. User-configured path_rules — first matching rule wins
        expanded = _expand_user(path)
        for expanded_pattern, access in self._expanded_path_rules:
            if _fnmatch_recursive(expanded, expanded_pattern):
                return self._apply_path_access(access, tool_name, path)

        # 4. No matching rule — default allow (sensitive paths already handled above)
        return tool_decision

    def check_command(self, command: str) -> PermissionDecision:
        """Check whether a shell *command* is permitted.

        Checks ``denied_commands`` first, then applies mode-based rules.
        """
        # 0. Reject empty commands
        if not command or not command.strip():
            return PermissionDecision(
                allowed=False,
                reason="Empty command is not permitted",
            )

        # 1. denied_commands — substring match
        for denied in self._config.denied_commands:
            if denied in command:
                return PermissionDecision(
                    allowed=False,
                    reason=f"Command matches denied pattern '{denied}'",
                )

        # 2. PLAN mode — all commands denied
        if self._config.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False,
                reason="Commands are not allowed in PLAN mode",
            )

        # 3. FULL_AUTO mode — allow everything not explicitly denied
        if self._config.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(allowed=True, reason="FULL_AUTO mode")

        # 4. DEFAULT mode — dangerous commands require confirmation
        if self._is_dangerous_command(command):
            return PermissionDecision(
                allowed=True,
                reason="Dangerous command requires user confirmation",
                requires_confirmation=True,
            )

        return PermissionDecision(allowed=True, reason="Command allowed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_mode_baseline(self, tool_name: str) -> PermissionDecision:
        """Apply mode-based permission rules for write tools."""
        if self._config.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is not allowed in PLAN mode (read-only)",
            )

        if self._config.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(allowed=True, reason="FULL_AUTO mode")

        # DEFAULT mode — allow but require confirmation for write tools
        return PermissionDecision(
            allowed=True,
            reason=f"Tool '{tool_name}' requires user confirmation in DEFAULT mode",
            requires_confirmation=True,
        )

    def _apply_path_access(
        self, access: PathAccess, tool_name: str, path: str
    ) -> PermissionDecision:
        """Translate a PathAccess level into a PermissionDecision."""
        if access == PathAccess.DENY:
            return PermissionDecision(
                allowed=False, reason=f"Path '{path}' access denied by path rule"
            )

        if access == PathAccess.READ:
            if _is_write_tool(tool_name):
                return PermissionDecision(
                    allowed=False,
                    reason=f"Path '{path}' is READ-only, write tool '{tool_name}' denied",
                )
            return PermissionDecision(
                allowed=True, reason=f"Path '{path}' READ access for read-only tool"
            )

        if access in (PathAccess.WRITE, PathAccess.READ_WRITE):
            return PermissionDecision(
                allowed=True, reason=f"Path '{path}' access allowed ({access.value})"
            )

        # Fallback — should not happen with valid PathAccess values
        return PermissionDecision(
            allowed=False, reason=f"Unknown path access level: {access}"
        )

    @staticmethod
    def _is_dangerous_command(command: str) -> bool:
        """Heuristic check for dangerous shell commands.

        Matches patterns at the start of the command or after shell
        operators (&&, |, ;, ||) to avoid false positives like
        ``"perform_rm_analysis"`` or ``"info --curl-option"``.
        """
        stripped = command.strip().lower()
        for pattern in _COMPILED_DANGEROUS_PATTERNS:
            if pattern.search(stripped):
                return True
        return False
