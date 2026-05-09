"""Unit tests for agent_nexus.models.permission module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.permission import (
    PathAccess,
    PathRule,
    PermissionConfig,
    PermissionDecision,
    PermissionMode,
)

# ---------------------------------------------------------------------------
# PermissionMode enum
# ---------------------------------------------------------------------------


class TestPermissionMode:
    def test_members(self):
        assert set(PermissionMode) == {
            PermissionMode.DEFAULT,
            PermissionMode.PLAN,
            PermissionMode.FULL_AUTO,
        }

    def test_values(self):
        assert PermissionMode.DEFAULT == "default"
        assert PermissionMode.PLAN == "plan"
        assert PermissionMode.FULL_AUTO == "full_auto"

    def test_from_string(self):
        assert PermissionMode("default") is PermissionMode.DEFAULT
        assert PermissionMode("plan") is PermissionMode.PLAN
        assert PermissionMode("full_auto") is PermissionMode.FULL_AUTO

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            PermissionMode("unknown")


# ---------------------------------------------------------------------------
# PathAccess enum
# ---------------------------------------------------------------------------


class TestPathAccess:
    def test_members(self):
        assert set(PathAccess) == {
            PathAccess.READ,
            PathAccess.WRITE,
            PathAccess.READ_WRITE,
            PathAccess.DENY,
        }

    def test_values(self):
        assert PathAccess.READ == "read"
        assert PathAccess.WRITE == "write"
        assert PathAccess.READ_WRITE == "read-write"
        assert PathAccess.DENY == "deny"


# ---------------------------------------------------------------------------
# PathRule
# ---------------------------------------------------------------------------


class TestPathRule:
    def test_construction_with_pattern(self):
        pr = PathRule(pattern="*.docx", access=PathAccess.READ_WRITE)
        assert pr.pattern == "*.docx"
        assert pr.access is PathAccess.READ_WRITE

    def test_default_access_is_read(self):
        pr = PathRule(pattern="*.txt")
        assert pr.access is PathAccess.READ

    def test_deny_pattern(self):
        pr = PathRule(pattern="*.env", access=PathAccess.DENY)
        assert pr.access is PathAccess.DENY

    def test_glob_patterns(self):
        patterns = ["*.py", "src/**/*.ts", "/etc/passwd", "~/.ssh/*"]
        for pat in patterns:
            pr = PathRule(pattern=pat)
            assert pr.pattern == pat

    def test_frozen(self):
        pr = PathRule(pattern="*.docx")
        with pytest.raises(ValidationError):
            pr.pattern = "changed"

    def test_serialization_round_trip(self):
        pr = PathRule(pattern="*.docx", access=PathAccess.READ_WRITE)
        data = pr.model_dump()
        pr2 = PathRule(**data)
        assert pr2 == pr

    def test_json_serialization(self):
        pr = PathRule(pattern="*.env", access=PathAccess.DENY)
        json_str = pr.model_dump_json()
        pr2 = PathRule.model_validate_json(json_str)
        assert pr2 == pr

    def test_missing_pattern_raises(self):
        with pytest.raises(ValidationError):
            PathRule()


# ---------------------------------------------------------------------------
# PermissionConfig
# ---------------------------------------------------------------------------


class TestPermissionConfig:
    def test_defaults(self):
        cfg = PermissionConfig()
        assert cfg.mode is PermissionMode.DEFAULT
        assert cfg.allowed_tools == []
        assert cfg.denied_tools == []
        assert cfg.path_rules == []
        assert cfg.denied_commands == []

    def test_with_allowed_tools(self):
        cfg = PermissionConfig(
            allowed_tools=["file_read", "file_write", "mcp__docx__*"],
        )
        assert len(cfg.allowed_tools) == 3
        assert "file_read" in cfg.allowed_tools

    def test_with_denied_tools(self):
        cfg = PermissionConfig(
            denied_tools=["bash", "rm"],
        )
        assert "bash" in cfg.denied_tools

    def test_with_path_rules(self):
        cfg = PermissionConfig(
            path_rules=[
                PathRule(pattern="*.docx", access=PathAccess.READ_WRITE),
                PathRule(pattern="*.env", access=PathAccess.DENY),
            ],
        )
        assert len(cfg.path_rules) == 2
        assert cfg.path_rules[0].access is PathAccess.READ_WRITE
        assert cfg.path_rules[1].access is PathAccess.DENY

    def test_full_construction(self):
        cfg = PermissionConfig(
            mode=PermissionMode.PLAN,
            allowed_tools=["file_read"],
            denied_tools=["bash"],
            path_rules=[PathRule(pattern="*.docx")],
            denied_commands=["rm -rf", "sudo"],
        )
        assert cfg.mode is PermissionMode.PLAN
        assert len(cfg.denied_commands) == 2

    def test_frozen(self):
        cfg = PermissionConfig()
        with pytest.raises(ValidationError):
            cfg.mode = PermissionMode.FULL_AUTO

    def test_serialization_round_trip(self):
        cfg = PermissionConfig(
            mode=PermissionMode.FULL_AUTO,
            allowed_tools=["file_read"],
            path_rules=[PathRule(pattern="*.docx", access=PathAccess.READ_WRITE)],
        )
        data = cfg.model_dump()
        cfg2 = PermissionConfig(**data)
        assert cfg2 == cfg

    def test_json_serialization(self):
        cfg = PermissionConfig(mode=PermissionMode.PLAN, denied_tools=["bash"])
        json_str = cfg.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["mode"] == "plan"
        cfg2 = PermissionConfig.model_validate_json(json_str)
        assert cfg2 == cfg

    def test_full_auto_mode(self):
        cfg = PermissionConfig(mode=PermissionMode.FULL_AUTO)
        assert cfg.mode is PermissionMode.FULL_AUTO


# ---------------------------------------------------------------------------
# PermissionDecision
# ---------------------------------------------------------------------------


class TestPermissionDecision:
    def test_allowed_decision_defaults(self):
        pd = PermissionDecision(allowed=True)
        assert pd.allowed is True
        assert pd.reason == ""
        assert pd.requires_confirmation is False

    def test_denied_with_reason(self):
        pd = PermissionDecision(
            allowed=False,
            reason="Path pattern *.env is denied",
        )
        assert pd.allowed is False
        assert "denied" in pd.reason
        assert pd.requires_confirmation is False

    def test_frozen(self):
        pd = PermissionDecision(allowed=True)
        with pytest.raises(ValidationError):
            pd.allowed = False
