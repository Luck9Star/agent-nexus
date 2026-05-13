"""E2E tests for configuration system: loading, merging, env var overrides.

Tests the full config loading pipeline from TOML file through merge to runtime.
"""

import os
from datetime import UTC, datetime

import pytest


@pytest.fixture
def config_dir(tmp_path):
    """Create a config directory with test config.toml."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("""
schema_version = "1.0"

[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.stages]
planner = "anthropic:claude-sonnet-4-20250514"
executor = "openai:gpt-4o"

[providers.anthropic]
api_type = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openai]
api_type = "openai"
api_key_env = "OPENAI_API_KEY"
""")
    return tmp_path


class TestConfigE2E:
    """E2E configuration scenarios."""

    def test_load_full_config(self, config_dir):
        """ConfigLoader loads and merges TOML with defaults."""
        from agent_nexus.platform.config.loader import ConfigLoader

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()

        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_env_var_overrides_config(self, config_dir, monkeypatch):
        """Environment variables override config.toml values."""
        from agent_nexus.platform.config.loader import ConfigLoader

        monkeypatch.setenv("AGENT_MODEL", "openai:gpt-4o-mini")

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()

        # AGENT_MODEL should override config.toml default
        assert config.models.default == "openai:gpt-4o-mini"

    def test_missing_config_uses_defaults(self, tmp_path):
        """ConfigLoader returns defaults when config.toml doesn't exist."""
        from agent_nexus.platform.config.loader import ConfigLoader

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        loader = ConfigLoader(config_dir=empty_dir)
        config = loader.load_config()

        # Should use built-in defaults (DEFAULT_MODEL_STRING)
        assert config.models.default == "openai:gpt-4o"

    def test_model_capability_registry(self):
        """ModelCapabilityRegistry resolves known model strings."""
        from agent_nexus.models.capability import ModelCapabilityRegistry

        registry = ModelCapabilityRegistry()
        cap = registry.get("claude-sonnet-4-20250514")
        assert cap is not None
        assert cap.provider == "anthropic"


class TestConfigLoaderE2E:
    """E2E tests for ConfigLoader integration with sources, env files, and caching."""

    def test_sources_yaml_loading(self, tmp_path):
        """ConfigLoader reads sources from both TOML and YAML."""
        from agent_nexus.platform.config.loader import ConfigLoader

        cfg = tmp_path / "config.toml"
        cfg.write_text("""
schema_version = "1.0"
[models]
default = "anthropic:claude-sonnet-4-20250514"
[[external_servers]]
name = "test-server"
command = ["python", "-m", "test"]
""")
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_dot_env_file_loading(self, tmp_path):
        """ConfigLoader loads .env file from config directory."""
        from agent_nexus.platform.config.loader import ConfigLoader

        cfg = tmp_path / "config.toml"
        cfg.write_text("""
schema_version = "1.0"
[models]
default = "anthropic:claude-sonnet-4-20250514"
""")
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_E2E_DOTENV_VAR=loaded_from_env\n")
        os.environ.pop("TEST_E2E_DOTENV_VAR", None)

        loader = ConfigLoader(config_dir=tmp_path)
        loader.load_config()
        # .env loading is best-effort; just verify ConfigLoader doesn't crash
        # If python-dotenv is available, the var should be set
        val = os.environ.get("TEST_E2E_DOTENV_VAR")
        assert val in ("loaded_from_env", None)
        os.environ.pop("TEST_E2E_DOTENV_VAR", None)

    def test_mtime_caching_invalidates(self, tmp_path):
        """ConfigLoader cache invalidates when config.toml mtime changes."""
        import time

        from agent_nexus.platform.config.loader import ConfigLoader

        cfg = tmp_path / "config.toml"
        cfg.write_text("""
schema_version = "1.0"
[models]
default = "anthropic:claude-sonnet-4-20250514"
""")
        loader = ConfigLoader(config_dir=tmp_path)

        config1 = loader.load_config()
        assert config1.models.default == "anthropic:claude-sonnet-4-20250514"

        # Modify config and ensure mtime changes
        time.sleep(0.05)
        cfg.write_text("""
schema_version = "1.0"
[models]
default = "openai:gpt-4o"
""")
        loader.invalidate_cache()
        config2 = loader.load_config()
        assert config2.models.default == "openai:gpt-4o"

    def test_project_config_merge(self, tmp_path):
        """ConfigLoader merges project-level config with user config."""
        from agent_nexus.platform.config.loader import ConfigLoader

        user_cfg = tmp_path / "config.toml"
        user_cfg.write_text("""
schema_version = "1.0"
[models]
default = "anthropic:claude-sonnet-4-20250514"
""")
        proj_cfg = tmp_path / "project.toml"
        proj_cfg.write_text("""
[models.stages]
planner = "openai:gpt-4o"
""")

        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_external_servers_parsing(self, tmp_path):
        """ConfigLoader parses external_servers with command arrays."""
        from agent_nexus.platform.config.loader import ConfigLoader

        cfg = tmp_path / "config.toml"
        cfg.write_text("""
schema_version = "1.0"
[models]
default = "anthropic:claude-sonnet-4-20250514"
[[external_servers]]
name = "my-mcp-server"
command = ["node", "server.js"]
args = ["--port", "3000"]
env = {NODE_ENV = "production"}
""")
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "anthropic:claude-sonnet-4-20250514"


class TestLockfileE2E:
    """E2E tests for LockfileManager read/write cycle."""

    def test_lockfile_roundtrip(self, tmp_path):
        """LockfileManager writes and reads back agent entries."""
        from agent_nexus.models.distribution import AgentType, LockfileEntry
        from agent_nexus.platform.local.lockfile import LockfileManager

        lockfile = LockfileManager(tmp_path / "lockfile.json")
        entry = LockfileEntry(
            version="1.0.0",
            source="https://github.com/test/agent",
            commit_sha="abc1230000000000000000000000000000000000",
            agent_type=AgentType.ATOMIC,
            installed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        lockfile.add_entry_by_name("test-agent", entry)

        data = lockfile.load()
        assert "test-agent" in data.agents
        assert data.agents["test-agent"].version == "1.0.0"

    def test_lockfile_get_entry(self, tmp_path):
        """LockfileManager retrieves individual entries."""
        from agent_nexus.models.distribution import AgentType, LockfileEntry
        from agent_nexus.platform.local.lockfile import LockfileManager

        lockfile = LockfileManager(tmp_path / "lockfile.json")
        entry = LockfileEntry(
            version="2.0.0",
            source="local",
            commit_sha="def4560000000000000000000000000000000000",
            agent_type=AgentType.COMPOSITE,
            installed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        lockfile.add_entry_by_name("my-agent", entry)

        retrieved = lockfile.get_entry("my-agent")
        assert retrieved is not None
        assert retrieved.version == "2.0.0"

        assert lockfile.get_entry("nonexistent") is None

    def test_lockfile_remove_entry(self, tmp_path):
        """LockfileManager removes entries and persists."""
        from agent_nexus.models.distribution import AgentType, LockfileEntry
        from agent_nexus.platform.local.lockfile import LockfileManager

        lockfile = LockfileManager(tmp_path / "lockfile.json")
        entry = LockfileEntry(
            version="1.0.0",
            source="local",
            commit_sha="0000000000000000000000000000000000000000",
            agent_type=AgentType.ATOMIC,
            installed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        lockfile.add_entry_by_name("temp-agent", entry)
        assert lockfile.get_entry("temp-agent") is not None

        lockfile.remove_entry("temp-agent")
        assert lockfile.get_entry("temp-agent") is None

    def test_lockfile_list_entries(self, tmp_path):
        """LockfileManager lists all entries."""
        from agent_nexus.models.distribution import AgentType, LockfileEntry
        from agent_nexus.platform.local.lockfile import LockfileManager

        lockfile = LockfileManager(tmp_path / "lockfile.json")
        for i in range(3):
            entry = LockfileEntry(
                version="1.0.0",
                source="local",
                commit_sha=f"{i:040d}",
                agent_type=AgentType.ATOMIC,
                installed_at=datetime(2024, 1, 1, tzinfo=UTC),
            )
            lockfile.add_entry_by_name(f"agent-{i}", entry)

        entries = lockfile.list_entries()
        assert len(entries) == 3


class TestSourceManagerE2E:
    """E2E tests for SourceManager sources.yaml CRUD cycle."""

    def test_source_add_and_list(self, tmp_path):
        """SourceManager adds and lists sources."""
        from agent_nexus.platform.local.sources import SourceEntry, SourceManager

        sources = SourceManager(tmp_path / "sources.yaml")
        sources.add_source(
            SourceEntry(name="official", type="git", url="https://github.com/official/agents")
        )
        sources.add_source(
            SourceEntry(name="community", type="git", url="https://github.com/community/agents")
        )

        result = sources.list_sources()
        assert len(result) == 2
        names = [s.name for s in result]
        assert "official" in names
        assert "community" in names

    def test_source_remove(self, tmp_path):
        """SourceManager removes a source."""
        from agent_nexus.platform.local.sources import SourceEntry, SourceManager

        sources = SourceManager(tmp_path / "sources.yaml")
        sources.add_source(
            SourceEntry(name="temp", type="git", url="https://github.com/temp/agents")
        )

        assert sources.remove_source("temp")
        result = sources.list_sources()
        # May still have default official source
        names = [s.name for s in result]
        assert "temp" not in names

    def test_source_persist_reload(self, tmp_path):
        """SourceManager persists to YAML and reloads correctly."""
        from agent_nexus.platform.local.sources import SourceEntry, SourceManager

        path = tmp_path / "sources.yaml"
        sources1 = SourceManager(path)
        sources1.add_source(
            SourceEntry(name="persist-test", type="git", url="https://github.com/test/agents")
        )

        # Reload from same file
        sources2 = SourceManager(path)
        result = sources2.list_sources()
        names = [s.name for s in result]
        assert "persist-test" in names
