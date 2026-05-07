"""Tests for agency allowlist loading and validation."""

from __future__ import annotations

import pytest
import yaml

from agent_nexus.platform.agency.allowlist import (
    _SAFE_SOURCE_PATH,
    _validate_capabilities,
    _validate_entries,
    _validate_id,
    _validate_output_contract,
    _validate_source,
    _validate_source_path,
    _validate_string_list,
    _validate_top_level,
    _validate_tools,
    load_allowlist,
    validate_allowlist_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: dict, name: str = "allowlist.yaml"):
    p = tmp_path / name
    p.write_text(yaml.dump(content, default_flow_style=False), encoding="utf-8")
    return str(p)


def _valid_entry(**overrides):
    """Return a minimal valid allowlist entry."""
    entry = {
        "id": "agency.test-agent",
        "source_path": "agents/test.md",
        "capabilities": ["analysis"],
        "output_contract": "markdown report",
    }
    entry.update(overrides)
    return entry


def _valid_allowlist():
    return {
        "source": {"repo": "https://github.com/example/agents", "ref": "main"},
        "agents": [_valid_entry()],
    }


# ===================================================================
# load_allowlist — happy path
# ===================================================================


class TestLoadAllowlistHappyPath:
    def test_loads_valid_file(self, tmp_path):
        p = _write_yaml(tmp_path, _valid_allowlist())
        result = load_allowlist(p)
        assert "source" in result
        assert "agents" in result
        assert len(result["agents"]) == 1

    def test_loads_multiple_agents(self, tmp_path):
        data = _valid_allowlist()
        data["agents"].append(
            _valid_entry(id="agency.another", source_path="agents/another.md")
        )
        p = _write_yaml(tmp_path, data)
        result = load_allowlist(p)
        assert len(result["agents"]) == 2

    def test_preserves_source_fields(self, tmp_path):
        p = _write_yaml(tmp_path, _valid_allowlist())
        result = load_allowlist(p)
        assert result["source"]["repo"] == "https://github.com/example/agents"
        assert result["source"]["ref"] == "main"


# ===================================================================
# load_allowlist — error cases
# ===================================================================


class TestLoadAllowlistErrors:
    def test_non_dict_root(self, tmp_path):
        p = _write_yaml(tmp_path, ["not", "a", "dict"])
        with pytest.raises(ValueError, match="YAML mapping"):
            load_allowlist(p)

    def test_missing_source(self, tmp_path):
        data = _valid_allowlist()
        del data["source"]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="'source' key"):
            load_allowlist(p)

    def test_missing_agents(self, tmp_path):
        p = _write_yaml(tmp_path, {"source": {"repo": "r", "ref": "r"}})
        with pytest.raises(ValueError, match="'agents' list"):
            load_allowlist(p)

    def test_agents_not_list(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            {"source": {"repo": "r", "ref": "r"}, "agents": "not a list"},
        )
        with pytest.raises(ValueError, match="'agents' list"):
            load_allowlist(p)

    def test_entry_not_dict(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            {"source": {"repo": "r", "ref": "r"}, "agents": ["not a dict"]},
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            load_allowlist(p)

    def test_duplicate_ids(self, tmp_path):
        data = _valid_allowlist()
        data["agents"] = [
            _valid_entry(id="agency.dup"),
            _valid_entry(id="agency.dup", source_path="agents/other.md"),
        ]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="Duplicate agent id"):
            load_allowlist(p)

    def test_invalid_entry_propagates_errors(self, tmp_path):
        data = _valid_allowlist()
        data["agents"] = [{"id": "bad"}]
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="missing required field"):
            load_allowlist(p)

    def test_source_not_dict(self, tmp_path):
        data = _valid_allowlist()
        data["source"] = "not a dict"
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="'source' must be a mapping"):
            load_allowlist(p)

    def test_source_missing_repo(self, tmp_path):
        data = _valid_allowlist()
        data["source"] = {"ref": "main"}
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="'repo' and 'ref'"):
            load_allowlist(p)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_allowlist(str(tmp_path / "nonexistent.yaml"))


# ===================================================================
# validate_allowlist_entry
# ===================================================================


class TestValidateAllowlistEntry:
    def test_valid_entry_no_errors(self):
        assert validate_allowlist_entry(_valid_entry()) == []

    def test_missing_all_required_fields(self):
        errors = validate_allowlist_entry({})
        assert len(errors) >= 4
        for field in ("source_path", "id", "capabilities", "output_contract"):
            assert any(field in e for e in errors)

    def test_partial_missing_fields(self):
        errors = validate_allowlist_entry({"id": "agency.test"})
        assert any("source_path" in e for e in errors)
        assert any("capabilities" in e for e in errors)
        assert any("output_contract" in e for e in errors)


# ===================================================================
# _validate_id
# ===================================================================


class TestValidateId:
    def test_valid_agency_id(self):
        errors: list[str] = []
        _validate_id({"id": "agency.my-agent"}, errors)
        assert errors == []

    def test_missing_id_field(self):
        errors: list[str] = []
        _validate_id({}, errors)
        assert errors == []

    def test_non_string_id(self):
        errors: list[str] = []
        _validate_id({"id": 123}, errors)
        assert any("agency." in e for e in errors)

    def test_wrong_prefix(self):
        errors: list[str] = []
        _validate_id({"id": "custom.my-agent"}, errors)
        assert any("agency." in e for e in errors)


# ===================================================================
# _validate_capabilities
# ===================================================================


class TestValidateCapabilities:
    def test_valid_capabilities(self):
        errors: list[str] = []
        _validate_capabilities({"capabilities": ["analysis", "coding"]}, errors)
        assert errors == []

    def test_missing_capabilities(self):
        errors: list[str] = []
        _validate_capabilities({}, errors)
        assert errors == []

    def test_not_a_list(self):
        errors: list[str] = []
        _validate_capabilities({"capabilities": "analysis"}, errors)
        assert any("list" in e for e in errors)

    def test_empty_list(self):
        errors: list[str] = []
        _validate_capabilities({"capabilities": []}, errors)
        assert any("non-empty" in e for e in errors)

    def test_non_string_entries(self):
        errors: list[str] = []
        _validate_capabilities({"capabilities": ["ok", 42]}, errors)
        assert any("strings" in e for e in errors)


# ===================================================================
# _validate_output_contract
# ===================================================================


class TestValidateOutputContract:
    def test_valid_contract(self):
        errors: list[str] = []
        _validate_output_contract({"output_contract": "markdown"}, errors)
        assert errors == []

    def test_missing_contract(self):
        errors: list[str] = []
        _validate_output_contract({}, errors)
        assert errors == []

    def test_empty_string(self):
        errors: list[str] = []
        _validate_output_contract({"output_contract": "  "}, errors)
        assert any("non-empty" in e for e in errors)

    def test_non_string(self):
        errors: list[str] = []
        _validate_output_contract({"output_contract": 42}, errors)
        assert any("non-empty string" in e for e in errors)


# ===================================================================
# _validate_source_path
# ===================================================================


class TestValidateSourcePath:
    def test_valid_path(self):
        errors: list[str] = []
        _validate_source_path({"source_path": "agents/test.md"}, errors)
        assert errors == []

    def test_missing_path(self):
        errors: list[str] = []
        _validate_source_path({}, errors)
        assert errors == []

    def test_non_md_extension(self):
        errors: list[str] = []
        _validate_source_path({"source_path": "agents/test.txt"}, errors)
        assert any(".md" in e for e in errors)

    def test_absolute_path(self):
        errors: list[str] = []
        _validate_source_path({"source_path": "/etc/passwd.md"}, errors)
        assert any("absolute" in e for e in errors)

    def test_tilde_path(self):
        errors: list[str] = []
        _validate_source_path({"source_path": "~/secrets.md"}, errors)
        assert any("~" in e for e in errors)

    def test_parent_traversal(self):
        errors: list[str] = []
        _validate_source_path({"source_path": "../../etc/secrets.md"}, errors)
        assert any("'..'" in e for e in errors)

    def test_non_string(self):
        errors: list[str] = []
        _validate_source_path({"source_path": 123}, errors)
        assert any(".md" in e for e in errors)


# ===================================================================
# _validate_string_list
# ===================================================================


class TestValidateStringList:
    def test_valid_list(self):
        errors: list[str] = []
        _validate_string_list(["a", "b"], "field", errors)
        assert errors == []

    def test_not_a_list(self):
        errors: list[str] = []
        _validate_string_list("not list", "field", errors)
        assert any("list" in e for e in errors)

    def test_non_string_entries(self):
        errors: list[str] = []
        _validate_string_list(["ok", 1], "my_field", errors)
        assert any("my_field" in e for e in errors)


# ===================================================================
# _validate_tools
# ===================================================================


class TestValidateTools:
    def test_valid_tools(self):
        errors: list[str] = []
        _validate_tools(
            {"tools": {"allowed": ["read"], "denied": ["write"]}}, errors
        )
        assert errors == []

    def test_missing_tools(self):
        errors: list[str] = []
        _validate_tools({}, errors)
        assert errors == []

    def test_tools_not_dict(self):
        errors: list[str] = []
        _validate_tools({"tools": ["not", "a", "dict"]}, errors)
        assert any("mapping" in e for e in errors)

    def test_allowed_not_list(self):
        errors: list[str] = []
        _validate_tools({"tools": {"allowed": "read"}}, errors)
        assert any("tools.allowed" in e for e in errors)

    def test_denied_not_list(self):
        errors: list[str] = []
        _validate_tools({"tools": {"denied": 42}}, errors)
        assert any("tools.denied" in e for e in errors)

    def test_overlap_allowed_denied(self):
        errors: list[str] = []
        _validate_tools(
            {"tools": {"allowed": ["read", "write"], "denied": ["write"]}}, errors
        )
        assert any("both allowed and denied" in e for e in errors)

    def test_no_overlap_passes(self):
        errors: list[str] = []
        _validate_tools(
            {"tools": {"allowed": ["read"], "denied": ["delete"]}}, errors
        )
        assert errors == []

    def test_empty_tools_dict_passes(self):
        errors: list[str] = []
        _validate_tools({"tools": {}}, errors)
        assert errors == []


# ===================================================================
# _SAFE_SOURCE_PATH regex
# ===================================================================


class TestSafeSourcePathRegex:
    """Direct tests for the _SAFE_SOURCE_PATH compiled regex."""

    def test_valid_simple_path(self):
        assert _SAFE_SOURCE_PATH.match("expert.md")

    def test_valid_nested_path(self):
        assert _SAFE_SOURCE_PATH.match("skills/expert.md")

    def test_valid_deep_nested_path(self):
        assert _SAFE_SOURCE_PATH.match("my-agent/skills/review.md")

    def test_windows_style_path_fails(self):
        assert _SAFE_SOURCE_PATH.match("C:\\etc\\passwd.md") is None

    def test_backslash_path_fails(self):
        assert _SAFE_SOURCE_PATH.match("skills\\expert.md") is None

    def test_parent_traversal_fails(self):
        assert _SAFE_SOURCE_PATH.match("../etc/expert.md") is None

    def test_tilde_path_fails(self):
        assert _SAFE_SOURCE_PATH.match("~/expert.md") is None

    def test_absolute_path_fails(self):
        assert _SAFE_SOURCE_PATH.match("/etc/expert.md") is None

    def test_url_encoded_traversal_fails(self):
        assert _SAFE_SOURCE_PATH.match("%2e%2e/expert.md") is None


# ===================================================================
# _validate_entries — extracted helper
# ===================================================================


class TestValidateEntries:
    def test_valid_entries(self):
        entries = [_valid_entry(id="agency.a"), _valid_entry(id="agency.b")]
        _validate_entries(entries)  # should not raise

    def test_duplicate_id_raises(self):
        entries = [_valid_entry(id="agency.dup"), _valid_entry(id="agency.dup")]
        with pytest.raises(ValueError, match="Duplicate"):
            _validate_entries(entries)

    def test_non_dict_entry_raises(self):
        entries = ["not a dict"]
        with pytest.raises(ValueError, match="must be a mapping"):
            _validate_entries(entries)

    def test_invalid_entry_field_raises(self):
        entries = [_valid_entry(id="bad-prefix")]
        with pytest.raises(ValueError, match="agency."):
            _validate_entries(entries)


# ===================================================================
# _validate_source — extracted helper
# ===================================================================


class TestValidateSource:
    def test_valid_source(self):
        _validate_source({"repo": "https://example.com/r", "ref": "main"})

    def test_non_dict_source_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            _validate_source("not a dict")

    def test_missing_repo_raises(self):
        with pytest.raises(ValueError, match="'repo' and 'ref'"):
            _validate_source({"ref": "main"})

    def test_missing_ref_raises(self):
        with pytest.raises(ValueError, match="'repo' and 'ref'"):
            _validate_source({"repo": "https://example.com/r"})


# ===================================================================
# _validate_top_level — extracted helper
# ===================================================================


class TestValidateTopLevel:
    def test_valid_top_level(self):
        _validate_top_level({"source": {}, "agents": []})

    def test_missing_source_raises(self):
        with pytest.raises(ValueError, match="'source'"):
            _validate_top_level({"agents": []})

    def test_missing_agents_raises(self):
        with pytest.raises(ValueError, match="'agents'"):
            _validate_top_level({"source": {}})

    def test_agents_not_list_raises(self):
        with pytest.raises(ValueError, match="'agents' list"):
            _validate_top_level({"source": {}, "agents": "nope"})
