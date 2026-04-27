# Config Consolidation & Documentation Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate scattered config (TOML+YAML+JSON+.env) into unified TOML with clear global/project layering, plus standalone docs for config, CLI, and quick-start.

**Architecture:** User-level config in `~/.agent-nexus/config.toml` (providers, sources, runtime), optional project-level override in `./agent-nexus.toml` (model, stage overrides). `sources.yaml` merged into `[sources]` section. All paths resolved relative to the config file containing them. Config priority: CLI args > env vars > .env > project config > global config > built-in defaults.

**Tech Stack:** Python (tomllib/toml, Pydantic FrozenModel), Rust (serde, toml), Typer CLI, Clap CLI

---

### Task 1: Add `stages` and `sources` to config models

**Files:**
- Modify: `src/agent_nexus/models/config.py` — add `stages` dict to Python `ModelConfig`, add `SourceEntry` to `PlatformConfig`
- Modify: `crates/ap-core/src/models/config.rs` — add `stages` field to Rust `ModelConfig`, add `sources` field to `PlatformConfig`

**Goal:** Data models support the new unified config schema before loader logic changes.

- [ ] **Step 1: Add `stages` to Python `ModelConfig`**

Read `src/agent_nexus/models/config.py` to find `ModelConfig`. Add `stages: dict[str, str]` field with default empty dict:

```python
class ModelConfig(FrozenModel):
    default: str = DEFAULT_MODEL_STRING
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    stages: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 2: Add `sources` and `schema_version` to Python `PlatformConfig`**

In the same file, add `sources` and `schema_version` fields to `PlatformConfig`. Import `SourceEntry` if needed, or use a forward reference:

```python
class PlatformConfig(FrozenModel):
    schema_version: str = "1.0"
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    sources: list[SourceEntry] = Field(default_factory=list)
```

Check if `SourceEntry` is in `agent_nexus.models.distribution`. If so, add import: `from agent_nexus.models.distribution import SourceEntry`.

- [ ] **Step 3: Add `stages` to Rust `ModelConfig`**

In `crates/ap-core/src/models/config.rs`, add `stages` field to `ModelConfig`:

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ModelConfig {
    #[serde(default = "default_model")]
    pub default: String,
    #[serde(default)]
    pub providers: std::collections::HashMap<String, ProviderConfig>,
    #[serde(default)]
    pub stages: std::collections::HashMap<String, String>,
}
```

- [ ] **Step 4: Add `sources` and `schema_version` to Rust `PlatformConfig`**

In the same file, add to `PlatformConfig`:

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PlatformConfig {
    #[serde(default = "default_schema_version")]
    pub schema_version: String,
    #[serde(default)]
    pub runtime: RuntimeConfig,
    #[serde(default)]
    pub models: ModelConfig,
    #[serde(default)]
    pub sources: Vec<SourceEntry>,
}

fn default_schema_version() -> String { "1.0".to_string() }
```

Add a `SourceEntry` struct at the top of the file (or in a separate models file):

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SourceEntry {
    pub name: String,
    #[serde(default = "default_source_type")]
    pub r#type: String,
    #[serde(default)]
    pub url: String,
    #[serde(default = "default_branch")]
    pub branch: String,
}

fn default_source_type() -> String { "git".to_string() }
fn default_branch() -> String { "main".to_string() }
```

- [ ] **Step 5: Run tests to verify models still work**

```bash
cd /Users/yangyitian/Documents/dev/Agents/agent-nexus
uv run pytest tests/unit/test_config_loader.py -x -q
cargo test -p ap-core
```

Expected: Python tests pass (new fields have defaults). Rust tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/models/config.py crates/ap-core/src/models/config.rs
git commit -m "feat(config): add stages, sources, schema_version to config models"
```

---

### Task 2: Python — load sources from config.toml [sources] section

**Files:**
- Modify: `src/agent_nexus/platform/config/loader.py` — add `_load_sources_from_toml()` method, update `load_config()` to parse sources
- Modify: `src/agent_nexus/platform/config/defaults.py` — mark `SOURCES_FILE` as deprecated

**Goal:** `ConfigLoader.load_config()` returns `PlatformConfig.sources` from `[sources]` in config.toml. The separate `load_sources()` method becomes a backward-compat wrapper.

- [ ] **Step 1: Add `_parse_sources_from_raw` static method to ConfigLoader**

In `loader.py`, add a helper that extracts sources from the raw TOML dict:

```python
@staticmethod
def _parse_sources_from_raw(raw: dict[str, Any]) -> list[SourceEntry]:
    """Extract source entries from [sources] section of config.toml."""
    sources_list = raw.get("sources", [])
    if not isinstance(sources_list, list):
        return []
    entries: list[SourceEntry] = []
    for item in sources_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            entry = SourceEntry(
                name=name,
                type=item.get("type", "git"),
                url=item.get("url", ""),
                branch=item.get("branch", "main"),
            )
            entries.append(entry)
        except Exception:
            logger.warning("Skipping invalid source entry: %s", item)
    return entries
```

- [ ] **Step 2: Update `load_config()` to parse sources from TOML**

In `load_config()`, after parsing models, add sources parsing. Change the return statement and add sources to PlatformConfig:

```python
# --- Sources section ---
sources = self._parse_sources_from_raw(raw)

config = PlatformConfig(
    runtime=runtime,
    models=models,
    sources=sources,
)
```

(If `PlatformConfig` uses `FrozenModel`, pass `sources=sources` to the constructor.)

- [ ] **Step 3: Refactor `load_sources()` to delegate to config.toml first**

Change `load_sources()` to first try loading from config.toml `[sources]`, falling back to the old `sources.yaml` for backward compatibility:

```python
def load_sources(self) -> list[SourceEntry]:
    config = self.load_config()
    if config.sources:
        return list(config.sources)
    # Backward compat: load from separate sources.yaml
    return self._load_sources_from_yaml()
```

Extract the existing YAML-reading logic into `_load_sources_from_yaml()`.

- [ ] **Step 4: Add backward-compat `_load_sources_from_yaml()` method**

Move the existing sources.yaml reading logic from `load_sources()` into this new private method. No logic changes — just extraction.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_config_loader.py -x -q
```

Expected: All existing tests pass. Sources tests still work via backward-compat path.

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/config/loader.py src/agent_nexus/platform/config/defaults.py
git commit -m "feat(config): load sources from config.toml [sources] section with yaml fallback"
```

---

### Task 3: Python — add project-level config loading

**Files:**
- Modify: `src/agent_nexus/platform/config/loader.py` — add `load_project_config()` method
- Modify: `src/agent_nexus/platform/config/defaults.py` — add `PROJECT_CONFIG_FILE` constant

**Goal:** `ConfigLoader` can load an optional `./agent-nexus.toml` from the project root and merge it on top of global config.

- [ ] **Step 1: Add project config constant to defaults.py**

```python
PROJECT_CONFIG_FILE: str = "agent-nexus.toml"
```

- [ ] **Step 2: Add `load_project_config()` to ConfigLoader**

```python
def load_project_config(
    self, project_dir: Path | None = None
) -> PlatformConfig | None:
    """Load optional project-level agent-nexus.toml.
    
    Searches upward from project_dir (default: cwd) for agent-nexus.toml.
    Returns None if not found.
    """
    search_dir = (project_dir or Path.cwd()).resolve()
    project_config_path = search_dir / PROJECT_CONFIG_FILE
    
    if not project_config_path.exists():
        logger.debug("No project config at %s", project_config_path)
        return None
    
    try:
        raw = toml.loads(project_config_path.read_text(encoding="utf-8"))
    except (toml.TomlDecodeError, OSError) as exc:
        logger.warning("Failed to load project config: %s", exc)
        return None
    
    models_raw = raw.get("models", {})
    providers_raw = models_raw.get("providers", {})
    if not isinstance(providers_raw, dict):
        providers_raw = {}
    
    default_model = models_raw.get("default", "")
    stages = models_raw.get("stages", {})
    if not isinstance(stages, dict):
        stages = {}
    
    # Resolve paths relative to project config directory
    config_dir = project_config_path.parent
    
    return PlatformConfig(
        models=ModelConfig(
            default=default_model,
            providers=self._build_providers(providers_raw),
            stages=stages,
        ),
        runtime=RuntimeConfig(),  # project config doesn't override runtime
    )
```

- [ ] **Step 3: Add `load_merged_config()` that merges project on top of global**

```python
def load_merged_config(
    self, project_dir: Path | None = None
) -> PlatformConfig:
    """Load global config merged with optional project-level overrides.
    
    Project config values win where non-empty. Merged priority:
    env vars > project agent-nexus.toml > global config.toml > built-in defaults.
    """
    global_config = self.load_config()
    project_config = self.load_project_config(project_dir)
    
    if project_config is None:
        return global_config
    
    # Merge project model config over global
    merged_default = project_config.models.default or global_config.models.default
    
    # Merge providers: project adds/overrides
    merged_providers = dict(global_config.models.providers)
    merged_providers.update(project_config.models.providers)
    
    # Merge stages: project overrides global
    merged_stages = dict(global_config.models.stages)
    merged_stages.update(project_config.models.stages)
    
    return PlatformConfig(
        schema_version=global_config.schema_version,
        runtime=global_config.runtime,
        models=ModelConfig(
            default=merged_default,
            providers=merged_providers,
            stages=merged_stages,
        ),
        sources=global_config.sources,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_config_loader.py -x -q
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/config/loader.py src/agent_nexus/platform/config/defaults.py
git commit -m "feat(config): add project-level agent-nexus.toml loading with merge"
```

---

### Task 4: Python — fix init template to 6 providers

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py:365-387` — `_default_config_template()`
- Modify: `src/agent_nexus/platform/local/cli/_shared.py:175-196` — `ConfigMigrator._default_config_dict()`

**Goal:** Both the init template generator and the config migrator produce all 6 built-in providers, consistent with `DEFAULT_PROVIDERS`.

- [ ] **Step 1: Update `_default_config_template()` in init_cmd.py**

Replace the 2-provider template with a 6-provider version. Lines 365-387:

```python
def _default_config_template() -> str:
    """Return the default config.toml template content."""
    return """\
# Agent Nexus Configuration
# Schema version: 1.0

schema_version = "1.0"

[runtime]
python_path = "python3"
uv_path = "uv"

[models]
default = "openai:gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"
api = "openai-compatible"

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"

[models.providers.minimax]
base_url = "https://api.minimax.chat/v1"
api_key_env = "MINIMAX_API_KEY"
api = "openai-compatible"

[models.providers.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key_env = "DASHSCOPE_API_KEY"
api = "openai-compatible"

[models.providers.ollama]
base_url = "http://localhost:11434/v1"
api = "ollama"
"""
```

- [ ] **Step 2: Update `_default_config_dict()` in _shared.py**

In `_shared.py`, lines 175-196, expand the providers dict:

```python
@classmethod
def _default_config_dict(cls) -> dict[str, Any]:
    """Return the default config as a plain dict."""
    return {
        "schema_version": cls.TARGET_VERSION,
        "runtime": {
            "python_path": "python3",
            "uv_path": "uv",
        },
        "models": {
            "default": "openai:gpt-4o",
            "providers": {
                "openai": {
                    "api_key_env": "OPENAI_API_KEY",
                    "api": "openai-compatible",
                },
                "anthropic": {
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "api": "anthropic-messages",
                },
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "api": "openai-compatible",
                },
                "minimax": {
                    "base_url": "https://api.minimax.chat/v1",
                    "api_key_env": "MINIMAX_API_KEY",
                    "api": "openai-compatible",
                },
                "qwen": {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "api": "openai-compatible",
                },
                "ollama": {
                    "base_url": "http://localhost:11434/v1",
                    "api_key_env": "",
                    "api": "ollama",
                },
            },
        },
    }
```

- [ ] **Step 3: Run existing tests to verify templates**

```bash
uv run pytest tests/unit/test_config_loader.py tests/unit/test_cli_module.py -x -q
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py src/agent_nexus/platform/local/cli/_shared.py
git commit -m "feat(config): expand init template to all 6 built-in providers"
```

---

### Task 5: Python — update sources command to use config.toml

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/sources_cmd.py` — update `_init_managers` usage
- Modify: `src/agent_nexus/platform/local/cli/_shared.py:80-100` — `_init_managers()` no longer creates SourceManager from separate yaml
- Modify: `src/agent_nexus/platform/local/sources.py` — add config-based source operations

**Goal:** `agent-nexus sources list/add/remove` reads/writes `[sources]` in config.toml instead of `sources.yaml`.

- [ ] **Step 1: Update `_init_managers()` in _shared.py**

Change `_init_managers()` to pass config-based SourceManager:

```python
def _init_managers(
    config_dir: Path | None = None,
) -> tuple:
    from agent_nexus.platform.config.loader import ConfigLoader
    from agent_nexus.platform.local.lockfile import LockfileManager
    from agent_nexus.platform.local.sources import SourceManager

    _config_dir = config_dir or _get_config_dir()
    _load_dot_env(_config_dir)

    loader = ConfigLoader(_config_dir)
    loader.ensure_config_dir()

    lockfile = LockfileManager(_config_dir / "lockfile.json")
    # SourceManager now takes the loader, reads/writes through config.toml
    sources = SourceManager.from_loader(loader)

    return loader, lockfile, sources, _config_dir
```

- [ ] **Step 2: Add `from_loader()` class method to SourceManager**

In `src/agent_nexus/platform/local/sources.py`, add:

```python
@classmethod
def from_loader(cls, loader: ConfigLoader) -> SourceManager:
    """Create SourceManager backed by config.toml [sources]."""
    mgr = cls.__new__(cls)
    mgr._loader = loader
    mgr._config_path = loader.config_dir / CONFIG_FILE
    return mgr
```

- [ ] **Step 3: Add `list_sources()` that reads from config**

In SourceManager, ensure `list_sources()` delegates to `loader.load_config().sources`:

```python
def list_sources(self) -> list[SourceEntry]:
    """List all configured sources from config.toml."""
    config = self._loader.load_config()
    return list(config.sources)
```

- [ ] **Step 4: Add `add_source()` that writes to config.toml**

```python
def add_source(self, entry: SourceEntry) -> None:
    """Add a source to config.toml [sources]."""
    config = self._loader.load_config()
    # Check for duplicate
    for existing in config.sources:
        if existing.name == entry.name:
            raise ValueError(f"Source '{entry.name}' already exists")
    
    import toml
    raw = toml.loads(self._config_path.read_text(encoding="utf-8"))
    raw.setdefault("sources", [])
    raw["sources"].append({
        "name": entry.name,
        "type": entry.type,
        "url": entry.url,
        "branch": entry.branch,
    })
    self._config_path.write_text(toml.dumps(raw), encoding="utf-8")
    self._loader._config_cache = None  # invalidate cache
```

- [ ] **Step 5: Add `remove_source()` that writes to config.toml**

```python
def remove_source(self, name: str) -> bool:
    """Remove a source from config.toml [sources]. Returns True if removed."""
    config = self._loader.load_config()
    if not any(s.name == name for s in config.sources):
        return False
    
    import toml
    raw = toml.loads(self._config_path.read_text(encoding="utf-8"))
    raw["sources"] = [
        s for s in raw.get("sources", [])
        if isinstance(s, dict) and s.get("name") != name
    ]
    self._config_path.write_text(toml.dumps(raw), encoding="utf-8")
    self._loader._config_cache = None
    return True
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/ -x -q -k "source or config"
```

Expected: Tests pass or need minor updates for the new code path.

- [ ] **Step 7: Commit**

```bash
git add src/agent_nexus/platform/local/sources.py src/agent_nexus/platform/local/cli/sources_cmd.py src/agent_nexus/platform/local/cli/_shared.py
git commit -m "feat(config): sources CRUD goes through config.toml [sources]"
```

---

### Task 6: Python — update init command to write sources into config.toml

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py:175-188` — write sources to config.toml instead of sources.yaml

**Goal:** `agent-nexus init` writes the official source into `[sources]` in config.toml.

- [ ] **Step 1: Update init command's source registration**

In `init_cmd.py` `init()` function, lines 175-188, replace SourceManager usage with config.toml [sources] writing:

```python
    # Step 3: Register official source via config.toml
    config = loader.load_config()
    has_official = any(s.name == "official" for s in config.sources)
    if not has_official:
        import toml
        raw = toml.loads(config_path.read_text(encoding="utf-8"))
        raw.setdefault("sources", [])
        raw["sources"].append({
            "name": "official",
            "type": "git",
            "url": "https://github.com/anthropics/agent-nexus-packages.git",
            "branch": "main",
        })
        config_path.write_text(toml.dumps(raw), encoding="utf-8")
        typer.echo("Registered official source.")
    else:
        typer.echo("Official source already registered.")
```

Remove the `from agent_nexus.platform.local.sources import SourceManager` import if no longer needed.

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/ -x -q -k "init or cli"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py
git commit -m "feat(config): init writes official source into config.toml [sources]"
```

---

### Task 7: Rust — add sources loading to ConfigLoader

**Files:**
- Modify: `crates/ap-core/src/config/loader.rs` — add `load_sources()` method or integrate into `PlatformConfig`
- Modify: `crates/ap-cli/src/commands/init.rs` — write sources into config.toml, not separate yaml

**Goal:** Rust loader reads `[sources]` from config.toml. Init command no longer creates separate `sources.yaml`.

- [ ] **Step 1: Update Rust init to write sources into config.toml [sources]**

In `crates/ap-cli/src/commands/init.rs`:

Replace `default_sources_yaml()` with logic that adds sources to the TOML. The simplest approach: serialize `PlatformConfig` with sources included.

```rust
fn default_config_toml_with_sources() -> String {
    let mut config = ap_core::config::default_config();
    config.sources = vec![SourceEntry {
        name: "official".to_string(),
        r#type: "git".to_string(),
        url: "https://github.com/anthropics/agent-nexus-packages.git".to_string(),
        branch: "main".to_string(),
    }];
    toml::to_string_pretty(&config).unwrap_or_default()
}
```

Update `run()` to only write `config.toml` (no more `sources.yaml`):

```rust
pub fn run(dir: &str, output: &OutputFormatter) -> Result<()> {
    let target = validate_init_dir(dir).map_err(|e| anyhow::anyhow!("{e}"))?;

    if !target.exists() {
        std::fs::create_dir_all(&target)?;
    }

    let config_path = target.join("config.toml");

    if config_path.exists() {
        output.info("config.toml already exists, skipping");
    } else {
        std::fs::write(&config_path, default_config_toml_with_sources())?;
        output.success(&format!("Created config.toml in {dir}"));
    }

    // ... rest of API key detection unchanged
    Ok(())
}
```

Remove the `default_sources_yaml()` function and the `sources_path` logic.

- [ ] **Step 2: Update Rust tests**

In `init.rs` tests, update assertions removing sources.yaml references. Update `config.rs` tests to handle new `sources` field:

```rust
#[test]
fn init_creates_config_toml() {
    let dir = tempfile::tempdir().unwrap();
    let output = OutputFormatter::new(false);
    run(dir.path().to_str().unwrap(), &output).unwrap();
    assert!(dir.path().join("config.toml").exists());
    // sources.yaml should NOT exist
    assert!(!dir.path().join("sources.yaml").exists());
}
```

- [ ] **Step 3: Run Rust tests**

```bash
cargo test -p ap-core -p ap-cli
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add crates/ap-core/src/config/loader.rs crates/ap-core/src/models/config.rs crates/ap-cli/src/commands/init.rs
git commit -m "feat(config): Rust init writes sources into config.toml, removes sources.yaml"
```

---

### Task 8: Python — add tests for new config behavior

**Files:**
- Create: `tests/unit/test_config_loader.py` — add test classes

**Goal:** Tests cover: sources in TOML parsing, project config merge, path resolution, backward compat with old sources.yaml.

- [ ] **Step 1: Write test for sources parsing from TOML**

Add to `tests/unit/test_config_loader.py`:

```python
class TestConfigLoaderSourcesFromToml:
    """Sources are parsed from [sources] section in config.toml."""

    def test_sources_from_toml(self, tmp_path: Path) -> None:
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text("""\
[sources]
[[sources]]
name = "official"
type = "git"
url = "https://github.com/official/repo.git"
branch = "main"
[[sources]]
name = "private"
type = "git"
url = "https://git.example.com/private.git"
""")
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert len(config.sources) == 2
        assert config.sources[0].name == "official"
        assert config.sources[0].url == "https://github.com/official/repo.git"
        assert config.sources[1].name == "private"

    def test_sources_empty_when_not_in_toml(self, tmp_path: Path) -> None:
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "test:model"\n')
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.sources == []
```

- [ ] **Step 2: Write test for project config merge**

```python
class TestConfigLoaderProjectConfig:
    """Project-level agent-nexus.toml merging."""

    def test_project_config_overrides_default_model(self, tmp_path: Path) -> None:
        # Global config
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "global:model"\n')
        
        # Project config
        proj_cfg = tmp_path / "agent-nexus.toml"
        proj_cfg.write_text('[models]\ndefault = "project:model"\n')
        
        loader = ConfigLoader(config_dir=tmp_path)
        merged = loader.load_merged_config(project_dir=tmp_path)
        assert merged.models.default == "project:model"

    def test_no_project_config_uses_global(self, tmp_path: Path) -> None:
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "global:model"\n')
        
        loader = ConfigLoader(config_dir=tmp_path)
        merged = loader.load_merged_config(project_dir=tmp_path)
        assert merged.models.default == "global:model"

    def test_project_stages_override_global(self, tmp_path: Path) -> None:
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text(
            '[models]\ndefault = "global:model"\n'
            '[models.stages]\nplanning = "global:planner"\n'
        )
        
        proj_cfg = tmp_path / "agent-nexus.toml"
        proj_cfg.write_text(
            '[models.stages]\nplanning = "project:planner"\n'
            'execution = "project:executor"\n'
        )
        
        loader = ConfigLoader(config_dir=tmp_path)
        merged = loader.load_merged_config(project_dir=tmp_path)
        assert merged.models.stages["planning"] == "project:planner"
        assert merged.models.stages["execution"] == "project:executor"
```

- [ ] **Step 3: Run the new tests to verify they pass**

```bash
uv run pytest tests/unit/test_config_loader.py -x -v -k "SourcesFromToml or ProjectConfig"
```

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_config_loader.py
git commit -m "test(config): add tests for TOML sources parsing and project config merge"
```

---

### Task 9: Write `docs/configuration.md`

**Files:**
- Create: `docs/configuration.md`

**Goal:** Complete config reference documentation.

- [ ] **Step 1: Write the configuration reference doc**

Content to include (paste the full markdown below):

````markdown
# Configuration Reference

## Overview

Agent Nexus uses a two-level TOML configuration system:

```
~/.agent-nexus/config.toml        # Global (user-level) — always loaded
./agent-nexus.toml                # Project-level (optional) — overrides global
```

## Priority Chain

For each setting, the value from the highest-priority source wins:

```
CLI arguments > environment variables > .env file > project config > global config > built-in defaults
```

## Quick Start

```bash
# Generate default global config
agent-nexus init

# Set your API key
export OPENAI_API_KEY="sk-..."

# Verify
agent-nexus doctor
```

## Global Config: `~/.agent-nexus/config.toml`

### Full Schema

```toml
schema_version = "1.0"

[runtime]
python_path = "python3"          # Python binary path
uv_path = "uv"                   # uv package manager path

[models]
default = "openai:gpt-4o"        # Default model string (provider:model_name)

[models.stages]                   # Agency pipeline per-stage overrides
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
integration = "openai:gpt-4o"
qa = "anthropic:claude-sonnet-4-20250514"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"    # Env var name for API key
api = "openai-compatible"         # API type: openai-compatible | anthropic-messages | ollama

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

[[sources]]                       # Agent package sources
name = "official"
type = "git"
url = "https://github.com/anthropics/agent-nexus-packages.git"
branch = "main"
```

### Fields

#### `schema_version`
Version of the config schema. Currently `"1.0"`. Used by `agent-nexus init` for auto-migration.

#### `[runtime]`
| Field | Default | Description |
|-------|---------|-------------|
| `python_path` | `"python3"` | Path to Python 3.11+ binary |
| `uv_path` | `"uv"` | Path to the uv package manager |

#### `[models]`
| Field | Default | Description |
|-------|---------|-------------|
| `default` | `"openai:gpt-4o"` | Default model in `provider:model_name` format |

#### `[models.stages]`
Optional. Per-stage model overrides for the Agency pipeline:

| Stage | Purpose |
|-------|---------|
| `planning` | Task decomposition (LLMPlanner) |
| `execution` | Per-expert LLM calls (LLMExecutor) |
| `integration` | Semantic synthesis (LLMIntegrator) |
| `qa` | Quality evaluation (LLMQualityGate) |

#### `[models.providers.<name>]`
| Field | Default | Description |
|-------|---------|-------------|
| `api_key_env` | `""` | Environment variable holding the API key |
| `api` | `"openai-compatible"` | API protocol: `openai-compatible`, `anthropic-messages`, or `ollama` |
| `base_url` | `""` | Base URL override for the API endpoint |

#### `[[sources]]`
Array of agent package sources. Each entry:
| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Unique source identifier |
| `type` | `"git"` | Source type |
| `url` | `""` | Git repository URL |
| `branch` | `"main"` | Default branch |

## Project Config: `./agent-nexus.toml`

Optional. Place in your project root. Only the fields you want to override need to be present.

```toml
schema_version = "1.0"

[models]
default = "anthropic:claude-opus-4-20250116"

[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
```

All paths in project config are resolved relative to the config file's directory.

## Environment Variables

### API Keys (per-provider)

| Variable | Provider |
|----------|----------|
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic |
| `ANTHROPIC_AUTH_TOKEN` | Anthropic (alt) |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MINIMAX_API_KEY` | MiniMax |
| `DASHSCOPE_API_KEY` | Qwen (DashScope) |
| `OLLAMA_HOST` | Ollama host |
| `API_API_KEY` | Custom API provider |

### Platform Settings

| Variable | Purpose |
|----------|---------|
| `AGENT_NEXUS_HOME` | Override default `~/.agent-nexus/` directory |
| `AGENT_MODEL` | Override default model (highest priority) |
| `DEFAULT_MODEL` | Override default model (falls back from AGENT_MODEL) |

### Runtime

| Variable | Purpose |
|----------|---------|
| `EDITOR` | Text editor for `config edit` command (default: vi) |
| `AGENT_NEXUS_PYTHON` | Python path for Rust runtime command |
| `MCP_TRANSPORT` | MCP transport mode (stdio/SSE) |
| `MCP_PORT` | MCP SSE port |
| `MCP_HOST` | MCP SSE host |

## Model String Format

Models use the format `provider:model_name`:

```
anthropic:claude-sonnet-4-20250514
openai:gpt-4o
deepseek:deepseek-chat
ollama:llama3
api:MiniMax-M2.7-highspeed
```

The provider prefix maps to a `[models.providers.<name>]` section.

## .env File

Optional `~/.agent-nexus/.env` for Docker/deployment scenarios. Simple KEY=VALUE format. Only sets variables not already in the environment.

Priority: `existing env vars > .env > config.toml api_key values`

## Migration from Pre-1.0 Configs

Run `agent-nexus init` — it auto-detects outdated `schema_version` and merges new defaults, preserving all user settings. If you had `sources.yaml`, sources are automatically migrated to `[sources]` in config.toml.
````

- [ ] **Step 2: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: add configuration reference documentation"
```

---

### Task 10: Write `docs/cli.md`

**Files:**
- Create: `docs/cli.md`

**Goal:** Complete CLI reference documentation covering all 17 commands.

- [ ] **Step 1: Write the CLI reference doc**

Content to include (paste the full markdown below):

````markdown
# CLI Reference

## Overview

Agent Nexus provides a unified CLI for all platform operations. Available as both Python (`python -m agent_nexus.cli`) and Rust (`agent-nexus`) binaries with near-identical command surfaces.

## Global Flags

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Print version and exit |
| `--json` | Output as JSON (Rust CLI only) |
| `--follow` | Follow log output (Rust CLI only) |

## Commands

### Setup & Diagnostics

#### `init`

Initialize Agent Nexus configuration.

```bash
agent-nexus init                # Create default config in ~/.agent-nexus/
agent-nexus init --wizard       # Interactive setup wizard
```

Creates:
- `~/.agent-nexus/config.toml` with default providers and official source
- `~/.agent-nexus/` directory tree (agents, venvs, cache, runtimes, logs)

#### `doctor`

Run 7 diagnostic checks on the installation.

```bash
agent-nexus doctor
```

Checks: config.toml validity, API key presence, git/uv on PATH, Python >= 3.11, config dir writable, Evolution DB accessible.

#### `env`

Print resolved environment snapshot (config dir, Python version, provider status).

```bash
agent-nexus env
```

#### `version`

Print the installed version.

```bash
agent-nexus version
```

---

### Agent Discovery & Management

#### `search`

Search available agents by keyword.

```bash
agent-nexus search code-review
agent-nexus search security
```

#### `list`

List installed agents.

```bash
agent-nexus list
agent-nexus list --all            # Include available (not installed)
```

#### `info`

Show detailed information about an agent.

```bash
agent-nexus info code-reviewer
```

#### `install`

Install an agent package.

```bash
agent-nexus install code-reviewer
agent-nexus install code-reviewer --version 1.2.0
agent-nexus install --source official code-reviewer
```

#### `uninstall`

Remove an installed agent.

```bash
agent-nexus uninstall code-reviewer
```

#### `update`

Update installed agents.

```bash
agent-nexus update                 # Update all
agent-nexus update code-reviewer   # Update specific agent
```

---

### Source Management

#### `sources list`

List configured package sources.

```bash
agent-nexus sources list
```

#### `sources add`

Add a new package source.

```bash
agent-nexus sources add --name my-source --url https://github.com/my/agents.git
agent-nexus sources add --name private --url git@git.internal:agents.git --type git
```

#### `sources remove`

Remove a package source.

```bash
agent-nexus sources remove my-source
```

Sources are stored in `~/.agent-nexus/config.toml` under `[[sources]]`.

---

### Runtime

#### `run`

Run an agent.

```bash
agent-nexus run code-reviewer --file src/main.py
agent-nexus run code-reviewer --mcp    # Run in MCP mode
```

#### `runtime`

Manage running agent processes.

```bash
agent-nexus runtime list
agent-nexus runtime stop <id>
agent-nexus runtime logs <id> --follow
```

---

### Configuration

#### `config`

View or edit configuration.

```bash
agent-nexus config                  # Show merged config
agent-nexus config edit             # Open config.toml in $EDITOR
agent-nexus config show             # Show resolved config with sources
```

---

### Development

#### `create`

Scaffold a new agent package.

```bash
agent-nexus create my-agent --type atomic
agent-nexus create my-pipeline --type composite
```

#### `evolution`

Manage the self-evolution engine.

```bash
agent-nexus evolution status
agent-nexus evolution run
agent-nexus evolution history
```

---

## Agency Pipeline

The Agency pipeline requires a project-level `./agent-nexus.toml` (or global config) with model stages configured:

```bash
python -m agent_nexus.platform.agency.cli run-composition \
  --task "Add input validation to the login endpoint" \
  --vendor-path ./vendor \
  --allowlist config/agency-agents.allowlist.yaml \
  --use-llm \
  --temperature 0.7 \
  --max-parallel 3
```

| Flag | Description |
|------|-------------|
| `--task` | The task description |
| `--vendor-path` | Path to vendor agents |
| `--allowlist` | Allowlist YAML for agency agents |
| `--use-llm` | Enable LLM-powered planning/integration/QA |
| `--temperature` | LLM temperature (0.0-1.0) |
| `--max-parallel` | Max parallel experts |

## Environment Variables

See [Configuration Reference](configuration.md) for the full env var table.

## Rust vs Python CLI

The Rust and Python CLIs are functionally equivalent. Differences:

- Rust CLI uses `--json` for machine-readable output
- Rust CLI uses `--follow` for log streaming
- Python-only: `agency` subcommand (Agency pipeline)

Prefer the Rust binary (`agent-nexus`) for production use; use Python (`python -m agent_nexus.cli`) for development and agency workflows.
````

- [ ] **Step 2: Commit**

```bash
git add docs/cli.md
git commit -m "docs: add CLI reference documentation"
```

---

### Task 11: Write `docs/quick-start.md`

**Files:**
- Create: `docs/quick-start.md`

**Goal:** 5-minute guide for new users.

- [ ] **Step 1: Write the quick-start guide**

````markdown
# Quick Start Guide

Get Agent Nexus running in 5 minutes.

## 1. Install

```bash
# Via uv (recommended)
uv tool install agent-nexus

# Or via pip
pip install agent-nexus
```

## 2. Initialize

```bash
agent-nexus init
```

This creates `~/.agent-nexus/config.toml` with default settings.

## 3. Configure an API Key

```bash
# Choose your provider:
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export DEEPSEEK_API_KEY="sk-..."
```

Or add it to `~/.agent-nexus/.env` for persistence:

```
OPENAI_API_KEY=sk-...
```

## 4. Verify

```bash
agent-nexus doctor
```

All 7 checks should pass.

## 5. Browse & Install an Agent

```bash
# See what's available
agent-nexus search security

# Install one
agent-nexus install security-scanner
```

## 6. Run Your First Agent

```bash
agent-nexus run security-scanner --file src/main.py
```

## Next Steps

- [Configuration Reference](configuration.md) — All config options and env vars
- [CLI Reference](cli.md) — Complete command documentation
- [Architecture Overview](01-overview.md) — Platform architecture

## Project-Level Setup (Optional)

For Agency pipeline or project-specific model config, create `./agent-nexus.toml` in your project root:

```toml
[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
```

Project config overrides global config automatically when running CLI commands from the project directory.
````

- [ ] **Step 2: Commit**

```bash
git add docs/quick-start.md
git commit -m "docs: add quick-start guide"
```

---

### Task 12: Slim down READMEs

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/README.md` — add entries for new docs

**Goal:** README becomes a project overview with pointers to detailed docs. Remove inline config/env/CLI tables.

- [ ] **Step 1: Update `README.md`**

In `README.md`, find the inline "Configuration" section and the "CLI Commands" table. Replace each with a short paragraph and a link:

For the Configuration section:
```markdown
## Configuration

See [Configuration Reference](docs/configuration.md) for the complete schema, environment variables, and priority chain.
```

For the CLI section:
```markdown
## CLI

See [CLI Reference](docs/cli.md) for the full command documentation (17 commands, 42 subcommands).
```

Add a Quick Start section near the top:
```markdown
## Quick Start

See the [Quick Start Guide](docs/quick-start.md) for a 5-minute setup walkthrough.
```

- [ ] **Step 2: Apply same changes to `README_EN.md`**

Mirror the changes from Step 1.

- [ ] **Step 3: Update `docs/README.md` index**

Add entries for the three new docs in the documentation index table:

```markdown
| Configuration Reference | `configuration.md` | Complete config schema, env vars, priority chain |
| CLI Reference | `cli.md` | All 17 CLI commands with usage examples |
| Quick Start Guide | `quick-start.md` | 5-minute setup and first agent run |
```

- [ ] **Step 4: Commit**

```bash
git add README.md README_EN.md docs/README.md
git commit -m "docs: slim READMEs, point to new config/cli/quick-start docs"
```
