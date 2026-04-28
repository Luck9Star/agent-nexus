"""Tests for PermissionChecker — runtime permission evaluator."""

from __future__ import annotations

import pytest

from agent_nexus.models.permission import (
    PathAccess,
    PathRule,
    PermissionConfig,
    PermissionMode,
)
from agent_nexus.platform.runtime.permission_checker import (
    READONLY_TOOLS,
    PermissionChecker,
    _fnmatch_recursive,
)


# ======================================================================
# Helpers
# ======================================================================


def _checker(**overrides) -> PermissionChecker:
    """Build a PermissionChecker with sensible defaults, overridden by *overrides*."""
    cfg = PermissionConfig(**overrides)
    return PermissionChecker(cfg)


# ======================================================================
# check_tool — denied_tools
# ======================================================================


class TestCheckToolDenied:
    """denied_tools acts as a blacklist — matching tools are always denied."""

    def test_denied_tool_is_blocked(self) -> None:
        checker = _checker(denied_tools=["bash"])
        d = checker.check_tool("bash")
        assert not d.allowed
        assert "denied_tools" in d.reason

    def test_denied_tool_overrides_readonly(self) -> None:
        """Even read-only tools can be explicitly denied."""
        checker = _checker(denied_tools=["file_read"])
        d = checker.check_tool("file_read")
        assert not d.allowed

    def test_non_denied_tool_passes(self) -> None:
        checker = _checker(
            denied_tools=["bash"],
            mode=PermissionMode.FULL_AUTO,
        )
        d = checker.check_tool("file_write")
        assert d.allowed


# ======================================================================
# check_tool — allowed_tools (whitelist)
# ======================================================================


class TestCheckToolAllowed:
    """allowed_tools acts as a whitelist — only matching tools are permitted."""

    def test_tool_not_in_whitelist_denied(self) -> None:
        checker = _checker(allowed_tools=["file_read", "file_write"])
        d = checker.check_tool("bash")
        assert not d.allowed
        assert "not in allowed_tools" in d.reason

    def test_tool_in_whitelist_allowed(self) -> None:
        checker = _checker(allowed_tools=["file_read", "file_write"])
        d = checker.check_tool("file_read")
        assert d.allowed

    def test_empty_whitelist_means_no_restriction(self) -> None:
        """When allowed_tools is empty, whitelist is not enforced."""
        checker = _checker(
            allowed_tools=[],
            mode=PermissionMode.FULL_AUTO,
        )
        d = checker.check_tool("anything")
        assert d.allowed

    def test_denied_overrides_allowed(self) -> None:
        """If a tool is in both lists, denied_tools wins."""
        checker = _checker(
            allowed_tools=["bash"],
            denied_tools=["bash"],
        )
        d = checker.check_tool("bash")
        assert not d.allowed


# ======================================================================
# check_tool — glob patterns
# ======================================================================


class TestCheckToolGlob:
    """Glob patterns in allowed_tools / denied_tools."""

    def test_allowed_glob_wildcard(self) -> None:
        checker = _checker(allowed_tools=["mcp__docx__*"])
        d = checker.check_tool("mcp__docx__read_file")
        assert d.allowed

    def test_allowed_glob_no_match(self) -> None:
        checker = _checker(allowed_tools=["mcp__docx__*"])
        d = checker.check_tool("mcp__pdf__read")
        assert not d.allowed

    def test_denied_glob_wildcard(self) -> None:
        checker = _checker(denied_tools=["mcp__internal__*"])
        d = checker.check_tool("mcp__internal__admin")
        assert not d.allowed

    def test_denied_glob_no_match(self) -> None:
        checker = _checker(
            denied_tools=["mcp__internal__*"],
            mode=PermissionMode.FULL_AUTO,
        )
        d = checker.check_tool("mcp__public__read")
        assert d.allowed


# ======================================================================
# check_tool — read-only tools
# ======================================================================


class TestCheckToolReadonly:
    """Read-only tools are always allowed unless explicitly denied."""

    @pytest.mark.parametrize("tool", list(READONLY_TOOLS))
    def test_readonly_always_allowed_default_mode(self, tool: str) -> None:
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_tool(tool)
        assert d.allowed
        assert not d.requires_confirmation

    @pytest.mark.parametrize("tool", list(READONLY_TOOLS))
    def test_readonly_always_allowed_plan_mode(self, tool: str) -> None:
        checker = _checker(mode=PermissionMode.PLAN)
        d = checker.check_tool(tool)
        assert d.allowed

    @pytest.mark.parametrize("tool", list(READONLY_TOOLS))
    def test_readonly_denied_when_explicitly_listed(self, tool: str) -> None:
        checker = _checker(denied_tools=[tool])
        d = checker.check_tool(tool)
        assert not d.allowed


# ======================================================================
# check_tool — mode baseline
# ======================================================================


class TestCheckToolMode:
    """Mode baseline controls write tool permissions."""

    def test_plan_mode_blocks_write_tools(self) -> None:
        checker = _checker(mode=PermissionMode.PLAN)
        d = checker.check_tool("file_write")
        assert not d.allowed
        assert "PLAN" in d.reason

    def test_full_auto_allows_everything(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_tool("file_write")
        assert d.allowed
        assert not d.requires_confirmation

    def test_default_mode_requires_confirmation_for_writes(self) -> None:
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_tool("file_write")
        assert d.allowed
        assert d.requires_confirmation

    def test_plan_mode_allows_readonly(self) -> None:
        checker = _checker(mode=PermissionMode.PLAN)
        d = checker.check_tool("file_read")
        assert d.allowed
        assert not d.requires_confirmation


# ======================================================================
# check_path — path_rules
# ======================================================================


class TestCheckPath:
    """path_rules match paths using fnmatch; first match wins."""

    def test_path_rule_deny(self) -> None:
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/tmp/**", access=PathAccess.DENY)],
        )
        d = checker.check_path("file_read", "/tmp/secret.txt")
        assert not d.allowed
        assert "denied" in d.reason.lower()

    def test_path_rule_read_allows_readonly_tool(self) -> None:
        checker = _checker(
            path_rules=[PathRule(pattern="*.docx", access=PathAccess.READ)],
        )
        d = checker.check_path("file_read", "report.docx")
        assert d.allowed

    def test_path_rule_read_blocks_write_tool(self) -> None:
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="*.docx", access=PathAccess.READ)],
        )
        d = checker.check_path("file_write", "report.docx")
        assert not d.allowed
        assert "READ-only" in d.reason

    def test_path_rule_write_allows_all(self) -> None:
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="*.txt", access=PathAccess.WRITE)],
        )
        d = checker.check_path("file_write", "notes.txt")
        assert d.allowed

    def test_path_rule_read_write_allows_all(self) -> None:
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="*.log", access=PathAccess.READ_WRITE)],
        )
        d = checker.check_path("file_write", "app.log")
        assert d.allowed

    def test_first_matching_rule_wins(self) -> None:
        """When multiple rules could match, the first one takes effect."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[
                PathRule(pattern="*.txt", access=PathAccess.DENY),
                PathRule(pattern="*.txt", access=PathAccess.WRITE),
            ],
        )
        d = checker.check_path("file_read", "notes.txt")
        assert not d.allowed  # first rule (DENY) wins

    def test_no_matching_rule_defaults_to_allow(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_path("file_read", "/some/random/path.txt")
        assert d.allowed

    def test_tool_denied_propagates_to_path(self) -> None:
        """If the tool is denied, check_path returns the tool denial."""
        checker = _checker(
            denied_tools=["file_write"],
            mode=PermissionMode.FULL_AUTO,
        )
        d = checker.check_path("file_write", "/tmp/file.txt")
        assert not d.allowed
        assert "denied_tools" in d.reason


# ======================================================================
# check_path — sensitive paths
# ======================================================================


class TestCheckPathSensitive:
    """Built-in sensitive paths are always denied, cannot be overridden."""

    @pytest.mark.parametrize(
        "path",
        [
            "~/.ssh/id_rsa",
            "~/.ssh/authorized_keys",
            "~/.aws/credentials",
            "~/.aws/config",
            "~/.config/gcloud/credentials.db",
            "~/.azure/service_principal.json",
            "~/.gnupg/secring.gpg",
            "~/.docker/config.json",
            "~/.kube/config",
            "production.env",
            "server.key",
            "certificate.pem",
        ],
    )
    def test_sensitive_path_always_denied(self, path: str) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_path("file_read", path)
        assert not d.allowed
        assert "sensitive" in d.reason.lower()

    def test_sensitive_path_overrides_path_rule(self) -> None:
        """path_rules cannot override sensitive path protection."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="*", access=PathAccess.READ_WRITE)],
        )
        d = checker.check_path("file_read", "~/.ssh/id_rsa")
        assert not d.allowed

    def test_non_sensitive_path_with_extension(self) -> None:
        """A file with a normal extension should not be flagged."""
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_path("file_read", "document.txt")
        assert d.allowed

    def test_path_traversal_bypass_blocked(self) -> None:
        """Path traversal with '..' cannot bypass sensitive path protection.

        Constructs traversal paths relative to CWD so they actually
        resolve to sensitive locations regardless of test environment.
        """
        import os

        from pathlib import Path

        checker = _checker(mode=PermissionMode.FULL_AUTO)

        # Build real traversal from CWD to each sensitive target
        targets = [
            Path.home() / ".ssh" / "id_rsa",
            Path.home() / ".aws" / "credentials",
            Path.home() / ".config" / "gcloud" / "credentials.db",
        ]
        for target in targets:
            traversal = os.path.relpath(target)
            d = checker.check_path("file_read", traversal)
            assert not d.allowed, (
                f"Traversal '{traversal}' (→ {target}) should be blocked"
            )
            assert "sensitive" in d.reason.lower()


# ======================================================================
# check_command
# ======================================================================


class TestCheckCommand:
    """Command-level permission checks."""

    def test_denied_command_blocked(self) -> None:
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["rm -rf"],
        )
        d = checker.check_command("rm -rf /")
        assert not d.allowed
        assert "denied pattern" in d.reason

    def test_denied_command_substring_match(self) -> None:
        """denied_commands use substring matching."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["dangerous"],
        )
        d = checker.check_command("some dangerous command")
        assert not d.allowed

    def test_plan_mode_blocks_all_commands(self) -> None:
        checker = _checker(mode=PermissionMode.PLAN)
        d = checker.check_command("ls -la")
        assert not d.allowed
        assert "PLAN" in d.reason

    def test_full_auto_allows_all_commands(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_command("ls -la")
        assert d.allowed

    def test_default_dangerous_command_requires_confirmation(self) -> None:
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("rm -rf /tmp/old")
        assert d.allowed
        assert d.requires_confirmation

    def test_default_safe_command_allowed(self) -> None:
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("ls -la")
        assert d.allowed
        assert not d.requires_confirmation

    def test_denied_overrides_full_auto(self) -> None:
        """denied_commands takes priority over FULL_AUTO mode."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            denied_commands=["format"],
        )
        d = checker.check_command("format C:")
        assert not d.allowed

    def test_sudo_requires_confirmation_default(self) -> None:
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("sudo apt install something")
        assert d.allowed
        assert d.requires_confirmation

    def test_rm_no_false_positive_on_substring(self) -> None:
        """Word-boundary matching: 'perform_rm_analysis' should NOT match 'rm'."""
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("perform_rm_analysis --data input.csv")
        assert d.allowed
        assert not d.requires_confirmation

    def test_curl_no_false_positive_on_substring(self) -> None:
        """Word-boundary matching: 'info --curl-option' should NOT match 'curl'."""
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("info --curl-option value")
        assert d.allowed
        assert not d.requires_confirmation

    def test_actual_dangerous_command_still_caught(self) -> None:
        """Word-boundary matching still catches real dangerous commands."""
        checker = _checker(mode=PermissionMode.DEFAULT)
        d = checker.check_command("rm -rf /tmp/old")
        assert d.allowed
        assert d.requires_confirmation


# ======================================================================
# Edge cases
# ======================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_config_defaults(self) -> None:
        """Default config: DEFAULT mode, no tool lists, no path rules."""
        checker = _checker()
        # Read-only tool allowed
        assert checker.check_tool("file_read").allowed
        # Write tool allowed but requires confirmation
        d = checker.check_tool("file_write")
        assert d.allowed and d.requires_confirmation

    def test_config_is_frozen(self) -> None:
        """PermissionConfig is frozen (immutable Pydantic model)."""
        cfg = PermissionConfig()
        with pytest.raises(Exception):
            cfg.mode = PermissionMode.FLAN_AUTO  # type: ignore[misc]

    def test_multiple_denied_tools(self) -> None:
        checker = _checker(denied_tools=["bash", "exec", "eval"])
        assert not checker.check_tool("bash").allowed
        assert not checker.check_tool("exec").allowed
        assert not checker.check_tool("eval").allowed

    def test_full_auto_write_no_confirmation(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_tool("file_write")
        assert d.allowed and not d.requires_confirmation


# ======================================================================
# min_length=1 validation tests (iter30)
# ======================================================================


class TestPathRuleMinLength:
    """PathRule.pattern rejects empty strings."""

    def test_empty_pattern_raises(self):
        from pydantic import ValidationError

        from agent_nexus.models.permission import PathRule

        with pytest.raises(ValidationError):
            PathRule(pattern="")


# ============================================================================
# _fnmatch_recursive ** glob support (from iter39)
# ============================================================================


class TestFnmatchRecursive:
    """_fnmatch_recursive handles ** patterns that fnmatch does not support."""

    def test_recursive_glob_matches_nested_path(self) -> None:
        """Pattern /tmp/** matches /tmp/a/b/c."""
        assert _fnmatch_recursive("/tmp/a/b/c", "/tmp/**")

    def test_recursive_glob_matches_direct_child(self) -> None:
        """Pattern /tmp/** matches /tmp/file.txt."""
        assert _fnmatch_recursive("/tmp/file.txt", "/tmp/**")

    def test_recursive_glob_matches_base(self) -> None:
        """Pattern /tmp/** matches /tmp itself."""
        assert _fnmatch_recursive("/tmp", "/tmp/**")

    def test_recursive_glob_no_false_positive(self) -> None:
        """Pattern /tmp/** does not match /home/file.txt."""
        assert not _fnmatch_recursive("/home/file.txt", "/tmp/**")

    def test_plain_glob_still_works(self) -> None:
        """Patterns without ** still work via fnmatch."""
        assert _fnmatch_recursive("report.docx", "*.docx")
        assert not _fnmatch_recursive("report.txt", "*.docx")


class TestPathRuleRecursiveGlob:
    """User path_rules with ** patterns work correctly in PermissionChecker."""

    def test_recursive_glob_deny(self) -> None:
        """PathRule with /tmp/** pattern denies nested paths."""
        checker = PermissionChecker(PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/tmp/**", access=PathAccess.DENY)],
        ))
        d = checker.check_path("file_read", "/tmp/secrets/deep/nested/key.pem")
        assert not d.allowed

    def test_recursive_glob_write(self) -> None:
        """PathRule with /data/** WRITE pattern allows write to nested paths."""
        checker = PermissionChecker(PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/data/**", access=PathAccess.WRITE)],
        ))
        d = checker.check_path("file_write", "/data/projects/myapp/config.json")
        assert d.allowed

    def test_recursive_glob_read_blocks_write_tool(self) -> None:
        """PathRule with /docs/** READ pattern blocks write tool on nested paths."""
        checker = PermissionChecker(PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/docs/**", access=PathAccess.READ)],
        ))
        d = checker.check_path("file_write", "/docs/archive/old/report.txt")
        assert not d.allowed

    def test_recursive_glob_does_not_match_unrelated(self) -> None:
        """PathRule with /tmp/** does not affect /home paths."""
        checker = PermissionChecker(PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/tmp/**", access=PathAccess.DENY)],
        ))
        d = checker.check_path("file_read", "/home/user/file.txt")
        assert d.allowed


# ============================================================================
# Coverage gap tests — lines 109-136, 266, 347
# ============================================================================


class TestFnmatchRecursiveSuffixBranches:
    """Lines 109-136: _fnmatch_recursive complex **/suffix matching branches."""

    def test_glob_star_suffix_matches_nested(self) -> None:
        """Pattern /tmp/**/*.txt matches deeply nested .txt files."""
        assert _fnmatch_recursive("/tmp/a/b/c.txt", "/tmp/**/*.txt")

    def test_glob_star_suffix_matches_direct_child(self) -> None:
        """Pattern /tmp/**/*.txt matches direct child .txt files."""
        assert _fnmatch_recursive("/tmp/file.txt", "/tmp/**/*.txt")

    def test_glob_star_suffix_no_match_wrong_ext(self) -> None:
        """Pattern /tmp/**/*.txt does not match .log files."""
        assert not _fnmatch_recursive("/tmp/a/b/c.log", "/tmp/**/*.txt")

    def test_glob_star_suffix_no_match_wrong_prefix(self) -> None:
        """Pattern /tmp/**/*.txt does not match paths under /home."""
        assert not _fnmatch_recursive("/home/a/b/c.txt", "/tmp/**/*.txt")

    def test_glob_star_middle_pattern(self) -> None:
        """Pattern /tmp/**/bar/* matches paths with intermediate 'bar' directory."""
        assert _fnmatch_recursive("/tmp/a/bar/file.txt", "/tmp/**/bar/*")

    def test_glob_star_middle_pattern_deep(self) -> None:
        """Pattern /tmp/**/bar/* matches deeply nested bar paths."""
        assert _fnmatch_recursive("/tmp/a/b/c/bar/file.txt", "/tmp/**/bar/*")

    def test_glob_star_middle_no_match_no_bar(self) -> None:
        """Pattern /tmp/**/bar/* does not match when no bar directory."""
        assert not _fnmatch_recursive("/tmp/a/baz/file.txt", "/tmp/**/bar/*")

    def test_value_equals_prefix_with_suffix(self) -> None:
        """When value == prefix but there's a suffix remainder, no match."""
        # /tmp == /tmp prefix but remainder /*.txt won't match empty tail
        assert not _fnmatch_recursive("/tmp", "/tmp/**/*.txt")

    def test_double_star_star_chained(self) -> None:
        """Chained ** patterns: /tmp/**/bar/** matches multi-segment paths."""
        # This exercises the recursive call at line 136
        assert _fnmatch_recursive("/tmp/a/bar/b/c.txt", "/tmp/**/bar/**")

    def test_double_star_star_chained_direct(self) -> None:
        """Pattern /tmp/**/bar/** matches /tmp/bar (zero segments both sides)."""
        assert _fnmatch_recursive("/tmp/bar", "/tmp/**/bar/**")

    def test_double_star_star_chained_nested(self) -> None:
        """Pattern /tmp/**/bar/** matches /tmp/bar/x/y."""
        assert _fnmatch_recursive("/tmp/bar/x/y", "/tmp/**/bar/**")


class TestCheckToolEmpty:
    """Empty tool_name rejection."""

    def test_empty_tool_name_denied(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_tool("")
        assert not d.allowed
        assert "Empty tool name" in d.reason

    def test_whitespace_tool_name_denied(self) -> None:
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_tool("   ")
        assert not d.allowed
        assert "Empty tool name" in d.reason


class TestCheckCommandEmpty:
    """Line 266: empty command rejection."""

    def test_empty_command_denied(self) -> None:
        """Empty string command is denied."""
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_command("")
        assert not d.allowed
        assert "Empty command" in d.reason

    def test_whitespace_command_denied(self) -> None:
        """Whitespace-only command is denied."""
        checker = _checker(mode=PermissionMode.FULL_AUTO)
        d = checker.check_command("   ")
        assert not d.allowed
        assert "Empty command" in d.reason


class TestApplyPathAccessFallback:
    """Line 347: unknown PathAccess value returns denial fallback."""

    def test_unknown_path_access_denied(self) -> None:
        """An unrecognized PathAccess value falls through to the denial."""
        checker = _checker(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[PathRule(pattern="/tmp/**", access=PathAccess.READ)],
        )
        # Use _apply_path_access directly with a mock PathAccess
        from agent_nexus.models.permission import PathAccess as PA

        # Create a mock PathAccess that is not in the known set
        class FakeAccess:
            """Fake enum value that won't match any real PathAccess."""
            def __eq__(self, other):
                # Never equals any real PathAccess
                return False

            def __hash__(self):
                return hash("fake")

            @property
            def value(self):
                return "fake"

        result = checker._apply_path_access(FakeAccess(), "file_read", "/tmp/test")
        assert not result.allowed
        assert "Unknown path access" in result.reason
