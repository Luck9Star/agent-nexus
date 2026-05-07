"""E2E: Runtime security pipeline — SecurityChecker + SecurityRules + PermissionChecker.

Tests the full defense-in-depth chain that protects code execution:
  1. SecurityChecker performs AST-level analysis using Import/Function/Attribute/Regex rules
  2. PermissionChecker evaluates tool access using mode + blacklist/whitelist + path_rules
  3. IPythonExecutor enforces security check before execution
"""

import pytest

from agent_nexus.models.permission import (
    PathAccess,
    PathRule,
    PermissionConfig,
    PermissionMode,
)
from agent_nexus.platform.runtime.executor import IPythonExecutor
from agent_nexus.platform.runtime.permission_checker import PermissionChecker
from agent_nexus.platform.runtime.security_checker import SecurityChecker
from agent_nexus.platform.runtime.security_rules import (
    ImportRule,
)

# ---------------------------------------------------------------------------
# SecurityChecker + SecurityRules integration
# ---------------------------------------------------------------------------


class TestSecurityCheckerRuleIntegration:
    """Verify all four rule types work together through SecurityChecker."""

    def test_import_rule_catches_os_subprocess(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("import os\nimport subprocess")
        assert len(violations) >= 2
        types = {v.rule_type for v in violations}
        assert "import" in types

    def test_function_rule_catches_eval(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("eval('1+1')")
        assert len(violations) >= 1
        types = {v.rule_type for v in violations}
        assert "function" in types

    def test_attribute_rule_catches_dunder_access(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x.__class__.__bases__")
        assert len(violations) >= 1
        assert any(v.rule_type == "attribute" for v in violations)

    def test_regex_rule_catches_builtins_subscript(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("__builtins__['eval']")
        assert len(violations) >= 1
        assert any(v.rule_type == "regex" for v in violations)

    def test_clean_code_passes_all_rules(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x = 1 + 2\nresult = x * 3\nprint(result)")
        assert len(violations) == 0

    def test_custom_rules_via_add_rule(self) -> None:
        checker = SecurityChecker()
        custom = ImportRule(["custom_danger"])
        checker.add_rule(custom)
        # Default rule still catches 'os'
        v1 = checker.check_code("import os")
        assert len(v1) >= 1
        # Custom rule catches 'custom_danger'
        v2 = checker.check_code("import custom_danger")
        assert len(v2) >= 1

    def test_multi_vector_attack_blocked(self) -> None:
        checker = SecurityChecker()
        code = "import os\nexec('rm -rf /')\nx.__class__.__subclasses__()"
        violations = checker.check_code(code)
        types = {v.rule_type for v in violations}
        assert "import" in types
        assert "function" in types
        assert "attribute" in types


class TestSecurityCheckerEdgeCases:
    """AST parsing and rule application edge cases."""

    def test_syntax_error_returns_empty(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("def foo(")
        assert isinstance(violations, list)

    def test_empty_code_returns_parse_error(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("")
        assert len(violations) >= 1
        assert violations[0].rule_type == "parse"

    def test_comment_only_code_passes(self) -> None:
        checker = SecurityChecker()
        assert checker.check_code("# just a comment\n# another") == []

    def test_qualified_call_blocked(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("os.system('echo hi')")
        assert len(violations) >= 1

    def test_chained_sandbox_escape_blocked(self) -> None:
        checker = SecurityChecker()
        code = "().__class__.__mro__[-1].__subclasses__()"
        violations = checker.check_code(code)
        assert len(violations) >= 1


# ---------------------------------------------------------------------------
# PermissionChecker integration
# ---------------------------------------------------------------------------


class TestPermissionCheckerModes:
    """Permission evaluation across DEFAULT / PLAN / FULL_AUTO modes."""

    def test_default_mode_readonly_tools_allowed(self) -> None:
        config = PermissionConfig(mode=PermissionMode.DEFAULT)
        checker = PermissionChecker(config)
        for tool in ("file_read", "grep", "glob", "search"):
            decision = checker.check_tool(tool)
            assert decision.allowed is True

    def test_default_mode_write_requires_confirmation(self) -> None:
        config = PermissionConfig(mode=PermissionMode.DEFAULT)
        checker = PermissionChecker(config)
        decision = checker.check_tool("file_write")
        assert decision.allowed is True
        assert decision.requires_confirmation is True

    def test_full_auto_allows_write(self) -> None:
        config = PermissionConfig(mode=PermissionMode.FULL_AUTO)
        checker = PermissionChecker(config)
        decision = checker.check_tool("file_write")
        assert decision.allowed is True

    def test_denied_tools_overrides_mode(self) -> None:
        config = PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            denied_tools=["file_read"],
        )
        checker = PermissionChecker(config)
        assert checker.check_tool("file_read").allowed is False

    def test_allowed_tools_whitelist(self) -> None:
        config = PermissionConfig(
            mode=PermissionMode.DEFAULT,
            allowed_tools=["custom_tool"],
        )
        checker = PermissionChecker(config)
        assert checker.check_tool("custom_tool").allowed is True

    def test_deny_takes_precedence_over_allow(self) -> None:
        config = PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            denied_tools=["custom_tool"],
            allowed_tools=["custom_tool"],
        )
        checker = PermissionChecker(config)
        assert checker.check_tool("custom_tool").allowed is False


class TestPermissionCheckerSensitivePaths:
    """Sensitive path protection is always enforced."""

    def test_ssh_always_denied(self) -> None:
        config = PermissionConfig(mode=PermissionMode.FULL_AUTO)
        checker = PermissionChecker(config)
        decision = checker.check_path("file_read", "~/.ssh/id_rsa")
        assert decision.allowed is False

    def test_aws_credentials_always_denied(self) -> None:
        config = PermissionConfig(mode=PermissionMode.FULL_AUTO)
        checker = PermissionChecker(config)
        decision = checker.check_path("file_read", "~/.aws/credentials")
        assert decision.allowed is False

    def test_normal_path_allowed_in_full_auto(self) -> None:
        config = PermissionConfig(mode=PermissionMode.FULL_AUTO)
        checker = PermissionChecker(config)
        decision = checker.check_path("file_read", "/tmp/workspace/data.txt")
        assert decision.allowed is True

    def test_path_rules_with_glob_patterns(self) -> None:
        config = PermissionConfig(
            mode=PermissionMode.DEFAULT,
            path_rules=[
                PathRule(pattern="/data/**", access=PathAccess.READ),
                PathRule(pattern="/tmp/**", access=PathAccess.READ_WRITE),
            ],
        )
        checker = PermissionChecker(config)
        read_decision = checker.check_path("file_read", "/data/file.csv")
        assert read_decision.allowed is True
        write_decision = checker.check_path("file_write", "/tmp/output.log")
        assert write_decision.allowed is True


# ---------------------------------------------------------------------------
# Full pipeline: SecurityChecker -> IPythonExecutor
# ---------------------------------------------------------------------------


class TestExecutorSecurityPipeline:
    """Verify IPythonExecutor rejects dangerous code and allows safe code."""

    @pytest.mark.asyncio
    async def test_safe_code_executes(self) -> None:
        executor = IPythonExecutor()
        try:
            result = await executor.execute("x = 42")
            assert result.success is True
            assert executor.get("x") == 42
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_import_blocked_before_execution(self) -> None:
        executor = IPythonExecutor()
        try:
            result = await executor.execute("import os")
            assert result.success is False
            assert result.error is not None and "security violation" in result.error.lower()
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_eval_blocked_before_execution(self) -> None:
        executor = IPythonExecutor()
        try:
            result = await executor.execute("eval('1+1')")
            assert result.success is False
            assert result.error is not None and "security violation" in result.error.lower()
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_shell_escape_blocked(self) -> None:
        executor = IPythonExecutor()
        try:
            result = await executor.execute("os.system('echo pwned')")
            assert result.success is False
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_namespace_isolation_after_rejection(self) -> None:
        executor = IPythonExecutor()
        try:
            await executor.execute("import os")
            assert executor.get("os") is None
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_closed_executor_rejects_all(self) -> None:
        executor = IPythonExecutor()
        executor.close()
        result = await executor.execute("x = 1")
        assert result.success is False
        assert result.error is not None and "closed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_multi_statement_safe_code(self) -> None:
        executor = IPythonExecutor()
        try:
            result = await executor.execute("a = 10\nb = 20\nc = a + b")
            assert result.success is True
            assert executor.get("c") == 30
        finally:
            executor.close()
