"""Tests for cli/_shared.py -- ConfigMigrator."""

from __future__ import annotations

from pathlib import Path

import toml

from agent_nexus.platform.local.cli._shared import ConfigMigrator


class TestConfigMigratorCheckVersion:
    def test_returns_none_when_no_config(self, tmp_path: Path) -> None:
        result = ConfigMigrator.check_version(tmp_path / "nonexistent.toml")
        assert result is None

    def test_returns_version_from_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('schema_version = "0.9"\n')
        assert ConfigMigrator.check_version(cfg) == "0.9"

    def test_returns_none_when_no_version_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[runtime]\npython_path = "python3"\n')
        assert ConfigMigrator.check_version(cfg) is None


class TestConfigMigratorMergeIfNeeded:
    def test_no_migration_when_already_current(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('schema_version = "1.0"\n[runtime]\npython_path = "python3"\n')
        result = ConfigMigrator.merge_if_needed(cfg)
        assert result is False

    def test_no_migration_when_file_missing(self, tmp_path: Path) -> None:
        result = ConfigMigrator.merge_if_needed(tmp_path / "nonexistent.toml")
        assert result is False

    def test_merges_new_keys_preserving_user_values(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'schema_version = "0.5"\n'
            "[runtime]\n"
            'python_path = "/usr/bin/python3.12"\n'
            "[models]\n"
            'default = "anthropic:claude-sonnet-4-20250514"\n'
        )
        result = ConfigMigrator.merge_if_needed(cfg)
        assert result is True

        merged = toml.loads(cfg.read_text())
        assert merged["schema_version"] == "1.0"
        # user values preserved
        assert merged["runtime"]["python_path"] == "/usr/bin/python3.12"
        assert merged["models"]["default"] == "anthropic:claude-sonnet-4-20250514"
        # new default keys added
        assert "uv_path" in merged["runtime"]

    def test_preserves_user_custom_providers(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'schema_version = "0.5"\n'
            "[models.providers.my-custom]\n"
            'base_url = "https://custom.api/v1"\n'
            'api_key_env = "CUSTOM_KEY"\n'
            'api = "openai-compatible"\n'
        )
        ConfigMigrator.merge_if_needed(cfg)

        merged = toml.loads(cfg.read_text())
        assert "my-custom" in merged["models"]["providers"]
        assert merged["models"]["providers"]["my-custom"]["base_url"] == "https://custom.api/v1"


class TestConfigMigratorDeepMerge:
    def test_user_overrides_default(self) -> None:
        result = ConfigMigrator._deep_merge(
            {"a": 1, "b": 2},
            {"b": 99},
        )
        assert result == {"a": 1, "b": 99}

    def test_recursive_nested_merge(self) -> None:
        result = ConfigMigrator._deep_merge(
            {"outer": {"x": 1, "y": 2}},
            {"outer": {"y": 99, "z": 3}},
        )
        assert result == {"outer": {"x": 1, "y": 99, "z": 3}}
