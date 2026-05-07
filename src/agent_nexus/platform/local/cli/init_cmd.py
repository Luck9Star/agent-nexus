"""Init, doctor, version, and env commands."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from agent_nexus.platform.config.defaults import DEFAULT_OLLAMA_BASE_URL
from agent_nexus.platform.local.cli._shared import (
    ConfigMigrator,
    _get_config_dir,
    _load_dot_env,
)

logger = logging.getLogger(__name__)

init_app = typer.Typer(help="Setup and diagnostics")


# =====================================================================
# version
# =====================================================================


@init_app.command()
def version() -> None:
    """Print the agent-nexus version."""
    try:
        ver = importlib.metadata.version("agent-nexus")
    except importlib.metadata.PackageNotFoundError:
        ver = "unknown (dev mode)"
    typer.echo(f"agent-nexus {ver}")


# =====================================================================
# doctor
# =====================================================================


@init_app.command()
def doctor() -> None:
    """Run diagnostic checks on the agent-nexus installation."""
    config_dir = _get_config_dir()
    config_path = config_dir / "config.toml"
    checks: list[tuple[str, bool, str]] = []

    # Load .env before checking API keys so that keys stored in
    # ~/.agent-nexus/.env are visible to os.environ.get() below.
    _load_dot_env(config_dir)

    # Check 1: config.toml exists and parses
    try:
        import toml

        toml.loads(config_path.read_text(encoding="utf-8"))
        checks.append(("config.toml exists and parses", True, "OK"))
    except FileNotFoundError:
        checks.append(("config.toml exists and parses", False, "not found"))
    except Exception as exc:
        checks.append(("config.toml exists and parses", False, str(exc)))

    # Check 2: API key configured
    # Read api_key_env from user's config.toml (may have custom providers)
    config_key_envs: list[str] = []
    try:
        import toml

        raw = toml.loads(config_path.read_text(encoding="utf-8"))
        providers = raw.get("models", {}).get("providers", {})
        config_key_envs = [
            str(v["api_key_env"])
            for v in providers.values()
            if isinstance(v, dict) and "api_key_env" in v
        ]
    except Exception:
        pass
    # Fallback to built-in defaults if config has no providers
    if not config_key_envs:
        from agent_nexus.platform.config.defaults import DEFAULT_PROVIDERS

        config_key_envs = [
            str(p["api_key_env"])
            for p in DEFAULT_PROVIDERS.values()
            if isinstance(p, dict) and "api_key_env" in p
        ]
    has_key = any(os.environ.get(k) for k in config_key_envs)
    checks.append(("API key configured", has_key, "at least one set" if has_key else "none set"))

    # Check 3: git on PATH
    git_path = shutil.which("git")
    checks.append(("git on PATH", git_path is not None, git_path or "not found"))

    # Check 4: uv on PATH
    uv_path = shutil.which("uv")
    checks.append(("uv on PATH", uv_path is not None, uv_path or "not found"))

    # Check 5: Python version
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", py_ok, sys.version.split()[0]))

    # Check 6: config directory writable (lockfile will be created here)
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(config_dir, os.W_OK)
        checks.append(("config dir writable", writable, "OK" if writable else "not writable"))
    except Exception as exc:
        checks.append(("config dir writable", False, str(exc)))

    # Check 7: Evolution DB accessible
    try:
        from agent_nexus.platform.evolution.store import EvolutionStore

        store = EvolutionStore(Path(":memory:"))
        store.close()
        checks.append(("Evolution DB accessible", True, "OK"))
    except Exception as exc:
        checks.append(("Evolution DB accessible", False, str(exc)))

    # Output
    all_pass = True
    for label, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        typer.echo(f"  [{status}] {label}: {detail}")

    typer.echo()
    passed_count = sum(1 for _, p, _ in checks if p)
    typer.echo(f"  {passed_count}/{len(checks)} checks passed.")

    if not all_pass:
        raise typer.Exit(code=1)


# =====================================================================
# init
# =====================================================================


@init_app.command()
def init(
    wizard: bool = typer.Option(False, "--wizard", "-w", help="Interactive setup wizard"),
) -> None:
    """Initialize the agent-nexus platform configuration.

    Creates ~/.agent-nexus/ directory tree with default config files.
    Use --wizard for interactive setup with API key configuration.
    """
    from agent_nexus.platform.config.loader import ConfigLoader

    config_dir = _get_config_dir()

    # Step 1: Ensure directory tree
    loader = ConfigLoader(config_dir)
    loader.ensure_config_dir()
    typer.echo(f"Config directory: {config_dir}")

    # Step 2: Generate config.toml if missing
    config_path = config_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(_default_config_template(), encoding="utf-8")
        typer.echo("Created config.toml with default settings.")
    else:
        typer.echo("config.toml already exists.")
        if ConfigMigrator.merge_if_needed(config_path):
            typer.echo("Config migrated to latest schema version.")

    # Step 3: Register official source in config.toml [sources]
    config = loader.load_config()
    has_official = any(s.name == "official" for s in config.sources)
    if not has_official:
        import toml

        raw = toml.loads(config_path.read_text(encoding="utf-8"))
        raw.setdefault("sources", [])
        raw["sources"].append(
            {
                "name": "official",
                "type": "git",
                "url": "https://github.com/anthropics/agent-nexus-packages.git",
                "branch": "main",
            }
        )
        config_path.write_text(toml.dumps(raw), encoding="utf-8")
        typer.echo("Registered official source.")
    else:
        typer.echo("Official source already registered.")

    # Step 4: Detect API keys
    key_envs = {
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "ANTHROPIC_AUTH_TOKEN": "anthropic",
        "DEEPSEEK_API_KEY": "deepseek",
        "DASHSCOPE_API_KEY": "qwen",
        "OLLAMA_HOST": "ollama",
    }
    detected = sorted(
        set(provider for env_var, provider in key_envs.items() if os.environ.get(env_var))
    )
    if detected:
        typer.echo(f"Detected API keys for: {', '.join(detected)}")
    else:
        typer.echo("No API keys detected in environment.")

    # Step 5: Wizard mode
    if wizard:
        _run_wizard(config_path)

    # Next steps
    typer.echo()
    typer.echo("Next steps:")
    typer.echo("  1. Set API keys: export OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...")
    typer.echo("  2. Browse agents: agent-nexus search <query>")
    typer.echo("  3. Install an agent: agent-nexus install <name>")
    typer.echo("  4. Run diagnostics: agent-nexus doctor")


# =====================================================================
# env
# =====================================================================


@init_app.command()
def env() -> None:
    """Print resolved environment snapshot."""
    config_dir = _get_config_dir()
    _load_dot_env(config_dir)

    from agent_nexus.platform.config.loader import ConfigLoader

    loader = ConfigLoader(config_dir)
    cfg = loader.load_config()

    provider_status: list[str] = []
    for name, preset in cfg.models.providers.items():
        has_key = bool(preset.api_key_env and os.environ.get(preset.api_key_env))
        provider_status.append(f"{name} (key: {'set' if has_key else 'not set'})")

    git_ver = shutil.which("git")
    uv_ver = shutil.which("uv")

    typer.echo(f"Config dir:    {config_dir}")
    typer.echo(f"Python:        {sys.version.split()[0]}")
    typer.echo(f"Git:           {'installed' if git_ver else 'not found'}")
    typer.echo(f"uv:            {'installed' if uv_ver else 'not found'}")
    typer.echo(f"Providers:     {', '.join(provider_status)}")


# =====================================================================
# Helpers
# =====================================================================



_PRESET_MODELS: dict[str, tuple[str | None, str, str | None, str]] = {
    "openai": ("OPENAI_API_KEY", "openai-compatible", None, "gpt-4o"),
    "anthropic": ("ANTHROPIC_API_KEY", "anthropic-messages", None, "claude-sonnet-4-20250514"),
    "deepseek": (
        "DEEPSEEK_API_KEY",
        "openai-compatible",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
    ),
    "ollama": (None, "openai-compatible", DEFAULT_OLLAMA_BASE_URL, "llama3"),
}


def _write_provider_config(
    config_path: Path,
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    key_env: str | None = None,
    api_type: str | None = None,
) -> None:
    """Write provider section into config.toml."""
    import toml

    try:
        raw = toml.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    raw.setdefault("models", {})["default"] = f"{provider}:{model}"
    prov_section = (
        raw.setdefault("models", {}).setdefault("providers", {}).setdefault(provider, {})
    )
    if base_url:
        prov_section["base_url"] = base_url
    if key_env:
        prov_section["api_key_env"] = key_env
    if api_type and api_type != "openai-compatible":
        prov_section["api"] = api_type
    config_path.write_text(toml.dumps(raw), encoding="utf-8")
    typer.echo(f"Config updated: default model = {provider}:{model}")


def _run_custom_provider_wizard(questionary: Any, config_path: Path) -> None:
    """Interactive wizard for setting up a custom provider."""
    custom_name = questionary.text(
        "Provider name (lowercase, e.g. my-provider):",
    ).ask()
    if not custom_name:
        return

    provider = custom_name.strip().lower()
    api_type = questionary.select(
        "API type:",
        choices=["openai-compatible", "anthropic-messages"],
    ).ask()
    if api_type is None:
        return

    base_url = questionary.text(
        "Base URL (e.g. https://api.example.com/v1):",
    ).ask()
    key_env = questionary.text(
        "API key environment variable name (e.g. MY_PROVIDER_API_KEY):",
    ).ask()
    model = questionary.text(
        "Default model name (e.g. my-model-v1):",
    ).ask()
    if not model:
        return

    _write_provider_config(
        config_path, provider, model,
        base_url=base_url, key_env=key_env, api_type=api_type,
    )
    if key_env:
        typer.echo(f"  Note: Add your API key to your shell profile: export {key_env}=...")


def _run_builtin_provider_wizard(
    questionary: Any, config_path: Path, provider: str,
) -> None:
    """Interactive wizard for a built-in (preset) provider."""
    key_env, api_type, base_url, default_model = _PRESET_MODELS[provider]

    if key_env:
        questionary.password(
            f"Enter {key_env} value (or leave blank to set via env later):",
        ).ask()

    model = questionary.text(
        "Enter default model:",
        default=default_model,
    ).ask()

    _write_provider_config(
        config_path, provider, model,
        base_url=base_url, key_env=key_env, api_type=api_type,
    )
    if key_env:
        typer.echo(f"  Note: API key stored. Add to shell profile: export {key_env}=****")

    verify = questionary.confirm("Test API connectivity?").ask()
    if verify:
        typer.echo("Connectivity test not yet implemented (placeholder).")

def _run_wizard(config_path: Path) -> None:
    """Interactive setup wizard using questionary."""
    try:
        import questionary  # pyright: ignore[reportMissingImports]
    except ImportError:
        typer.echo("Install questionary for wizard mode: pip install questionary")
        return

    provider = questionary.select(
        "Select default provider:",
        choices=["openai", "anthropic", "deepseek", "ollama", "custom"],
    ).ask()
    if provider is None:
        return

    if provider == "custom":
        _run_custom_provider_wizard(questionary, config_path)
    else:
        _run_builtin_provider_wizard(questionary, config_path, provider)


def _default_config_template() -> str:
    """Return the default config.toml template content."""
    return f"""\
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
base_url = "{DEFAULT_OLLAMA_BASE_URL}"
api = "ollama"
"""
