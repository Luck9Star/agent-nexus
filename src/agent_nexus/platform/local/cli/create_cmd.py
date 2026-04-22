"""``agent-nexus create`` — scaffold a new Atomic Agent.

Generates a complete agent package from a template:
  - agent-manifest.yaml
  - agent.py (top-level entry point)
  - SKILL.md
  - pyproject.toml
  - <pkg>/__init__.py
  - <pkg>/agent.py
  - <pkg>/mcp_adapter.py

Modes:
  Non-interactive:  agent-nexus create my-agent -d "description" --tools pipeline
  Interactive:      agent-nexus create my-agent --wizard
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import typer
import yaml

from agent_nexus.platform.utils import AGENT_NAME_RE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOLS_MODE_MAP = {
    "simple": ["run"],
    "pipeline": ["analyze", "execute", "report"],
}


def _agent_name_to_package(name: str) -> str:
    """``code-reviewer`` → ``agent_code_reviewer``."""
    return "agent_" + name.replace("-", "_")


def _to_class_name(name: str) -> str:
    """``code-reviewer`` → ``CodeReviewer``."""
    return "".join(part.capitalize() for part in name.split("-"))


def _to_entry_fn(name: str) -> str:
    """``code-reviewer`` → ``code_reviewer``."""
    return name.replace("-", "_")


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".scaffold-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Generators — one per file
# ---------------------------------------------------------------------------


def _gen_manifest(
    name: str,
    description: str,
    tools: list[str],
    recommended_model: str = "standard",
    fallback_model: str = "economy",
) -> str:
    data = {
        "name": name,
        "type": "atomic",
        "version": "0.1.0",
        "description": description,
        "capabilities": ["general-purpose"],
        "mcp": {"tools": tools},
        "permissions": {
            "mode": "default",
            "allowed_tools": ["file_read", "grep", "glob"],
            "denied_tools": ["bash"],
        },
        "model_config": {
            "recommended": recommended_model,
            "fallback": fallback_model,
        },
    }
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def _gen_top_level_agent(name: str, tools: list[str]) -> str:
    pkg = _agent_name_to_package(name)
    fn = _to_entry_fn(name)

    if tools == ["run"]:
        return (
            f'"""Top-level entry point for {name} agent."""\n'
            f"\n"
            f"from __future__ import annotations\n"
            f"\n"
            f"\n"
            f"async def run(task: str, _context: dict | None = None) -> str:\n"
            f'    """Execute the {name} agent task."""\n'
            f"    from {pkg}.agent import {fn}_run\n"
            f"    return await {fn}_run(task, _context)\n"
        )

    # pipeline: analyze / execute / report
    imports = "\n".join(
        f"from {pkg}.agent import {fn}_{t}" for t in tools
    )
    handlers = "\n\n".join(
        f"async def {t}(task: str, _context: dict | None = None) -> str:\n"
        f'    """{t.capitalize()} phase for {name}."""\n'
        f"    return await {fn}_{t}(task, _context)"
        for t in tools
    )
    return (
        f'"""Top-level entry point for {name} agent."""\n'
        f"\n"
        f"from __future__ import annotations\n"
        f"\n"
        f"{imports}\n"
        f"\n"
        f"\n"
        f"{handlers}\n"
    )


def _gen_skill_md(name: str, description: str, tools: list[str]) -> str:
    lines = [
        f"# {name} -- {description}",
        "",
        "## Role",
        "",
        f"You are a {name} agent. {description}",
        "",
        "## Capabilities",
        "",
    ]
    for t in tools:
        lines.append(f"- **{t}**: TODO - describe what this tool does")
    lines += [
        "",
        "## Error Handling",
        "",
        "| Scenario | Handling |",
        "|----------|----------|",
        "| Invalid input | Return error message |",
        "| Processing failure | Raise with context |",
        "",
        "## Example Usage",
        "",
    ]
    if tools == ["run"]:
        lines += [
            "```json",
            '{ "task": "example task description" }',
            "```",
        ]
    else:
        for t in tools:
            lines += [
                f"### {t}",
                "```json",
                f'{{ "task": "example {t} input" }}',
                "```",
                "",
            ]
    return "\n".join(lines) + "\n"


def _gen_pyproject(name: str) -> str:
    pkg = _agent_name_to_package(name)
    return (
        f'[project]\n'
        f'name = "agent-{name}"\n'
        f'version = "0.1.0"\n'
        f'description = "TODO: agent description"\n'
        f'requires-python = ">=3.12"\n'
        f'dependencies = [\n'
        f'    "pydantic>=2.0",\n'
        f']\n'
        f'\n'
        f'[project.optional-dependencies]\n'
        f'full = [\n'
        f'    "fastmcp>=2.0",\n'
        f']\n'
        f'dev = [\n'
        f'    "pytest>=8.0",\n'
        f'    "pytest-asyncio>=0.23",\n'
        f']\n'
        f'\n'
        f'[build-system]\n'
        f'requires = ["hatchling"]\n'
        f'build-backend = "hatchling.build"\n'
        f'\n'
        f'[tool.hatch.build.targets.wheel]\n'
        f'packages = ["{pkg}"]\n'
        f'\n'
        f'[tool.pytest.ini_options]\n'
        f'testpaths = ["tests"]\n'
        f'asyncio_mode = "auto"\n'
        f'\n'
        f'[tool.ruff]\n'
        f'target-version = "py312"\n'
        f'line-length = 100\n'
        f'\n'
        f'[tool.ruff.lint]\n'
        f'select = ["E", "F", "I", "N", "UP", "B", "SIM"]\n'
    )


def _gen_pkg_init(name: str) -> str:
    pkg = _agent_name_to_package(name)
    class_name = _to_class_name(name)
    return (
        f'"""agent-{name} — Atomic Agent."""\n'
        f"\n"
        f"from {pkg}.agent import {class_name}Agent\n"
        f"\n"
        f"__all__ = [\n"
        f'    "{class_name}Agent",\n'
        f"]\n"
    )


def _gen_pkg_agent(name: str, tools: list[str]) -> str:
    class_name = _to_class_name(name)
    fn = _to_entry_fn(name)

    if tools == ["run"]:
        return (
            f'"""{class_name}Agent implementation."""\n'
            f"\n"
            f"from __future__ import annotations\n"
            f"\n"
            f"\n"
            f"class {class_name}Agent:\n"
            f'    """{name} agent."""\n'
            f"\n"
            f"    async def run(self, task: str, context: dict | None = None) -> str:\n"
            f'        """Execute the agent task.\n'
            f"\n"
            f"        Args:\n"
            f"            task: Task description.\n"
            f"            context: Optional context dictionary.\n"
            f"\n"
            f"        Returns:\n"
            f"            Task result as string.\n"
            f'        """\n'
            f"        # TODO: Implement agent logic\n"
            f'        return f"Agent {name!r} executed: {{task}}"\n'
            f"\n"
            f"\n"
            f"async def {fn}_run(task: str, context: dict | None = None) -> str:\n"
            f'    """Module-level entry point for MCP adapter."""\n'
            f"    agent = {class_name}Agent()\n"
            f"    return await agent.run(task, context)\n"
        )

    # pipeline: separate methods + entry points
    method_stubs = "\n\n".join(
        f"    async def {t}(self, task: str, context: dict | None = None) -> str:\n"
        f'        """{t.capitalize()} phase.\n'
        f"\n"
        f"        Args:\n"
        f"            task: Task description.\n"
        f"            context: Optional context dictionary.\n"
        f"\n"
        f"        Returns:\n"
        f"            Result as string.\n"
        f'        """\n'
        f"        # TODO: Implement {t} logic\n"
        f'        return f"Agent {name!r} {t}: {{task}}"'
        for t in tools
    )
    entry_points = "\n\n".join(
        f"async def {fn}_{t}(task: str, context: dict | None = None) -> str:\n"
        f'    """Module-level entry point for {t}."""\n'
        f"    agent = {class_name}Agent()\n"
        f"    return await agent.{t}(task, context)"
        for t in tools
    )
    return (
        f'"""{class_name}Agent implementation."""\n'
        f"\n"
        f"from __future__ import annotations\n"
        f"\n"
        f"\n"
        f"class {class_name}Agent:\n"
        f'    """{name} agent with pipeline tools."""\n'
        f"\n"
        f"{method_stubs}\n"
        f"\n"
        f"\n"
        f"{entry_points}\n"
    )


def _gen_mcp_adapter(name: str, tools: list[str]) -> str:
    pkg = _agent_name_to_package(name)
    fn = _to_entry_fn(name)

    if tools == ["run"]:
        return (
            f'"""MCP adapter — expose {name} as an MCP Server.\n'
            f"\n"
            f'Requires the ``fastmcp`` package.\n'
            f'"""\n'
            f"\n"
            f"from __future__ import annotations\n"
            f"\n"
            f"\n"
            f"def create_mcp_server() -> object:\n"
            f'    """Create and return a FastMCP server for {name}."""\n'
            f"    from fastmcp import FastMCP\n"
            f"\n"
            f'    mcp = FastMCP("{name}")\n'
            f"\n"
            f"    @mcp.tool()\n"
            f"    async def run(task: str, context: dict | None = None) -> str:\n"
            f'        """Execute the {name} agent task."""\n'
            f"        from {pkg}.agent import {fn}_run\n"
            f"        return await {fn}_run(task, context)\n"
            f"\n"
            f"    return mcp\n"
        )

    tool_handlers = "\n\n".join(
        f"    @mcp.tool()\n"
        f"    async def {t}(task: str, context: dict | None = None) -> str:\n"
        f'        """{t.capitalize()} phase for {name}."""\n'
        f"        from {pkg}.agent import {fn}_{t}\n"
        f"        return await {fn}_{t}(task, context)"
        for t in tools
    )
    return (
        f'"""MCP adapter — expose {name} as an MCP Server.\n'
        f"\n"
        f'Requires the ``fastmcp`` package.\n'
        f'"""\n'
        f"\n"
        f"from __future__ import annotations\n"
        f"\n"
        f"\n"
        f"def create_mcp_server() -> object:\n"
        f'    """Create and return a FastMCP server for {name}."""\n'
        f"    from fastmcp import FastMCP\n"
        f"\n"
        f'    mcp = FastMCP("{name}")\n'
        f"\n"
        f"{tool_handlers}\n"
        f"\n"
        f"    return mcp\n"
    )


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


def _run_wizard(name: str) -> dict:
    """Collect agent parameters interactively."""
    typer.echo(f"\n=== Create Agent: {name} ===\n")

    description = typer.prompt("Description")
    typer.echo("\nTool pattern:")
    typer.echo("  1) simple   — single `run` tool")
    typer.echo("  2) pipeline — analyze / execute / report")
    choice = typer.prompt("Choose", default="1")
    tools_key = "pipeline" if choice.strip() == "2" else "simple"

    typer.echo("\nModel tier:")
    typer.echo("  1) lightweight")
    typer.echo("  2) standard")
    typer.echo("  3) premium")
    model_choice = typer.prompt("Recommended model", default="2")
    model_map = {"1": "lightweight", "2": "standard", "3": "premium"}
    recommended = model_map.get(model_choice.strip(), "standard")

    return {
        "description": description,
        "tools_key": tools_key,
        "recommended_model": recommended,
        "fallback_model": "economy",
    }


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


def scaffold_agent(
    name: str,
    description: str,
    tools_key: str,
    recommended_model: str = "standard",
    fallback_model: str = "economy",
    output_dir: Path | None = None,
) -> Path:
    """Create the agent directory tree. Returns the agent root directory."""
    if not AGENT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name {name!r}. "
            "Must match: starts with alphanumeric, then alphanumeric/hyphen/underscore."
        )

    tools = _TOOLS_MODE_MAP[tools_key]
    base = output_dir or Path.cwd() / "agents" / "atomic"
    agent_dir = base / name
    pkg_dir = agent_dir / _agent_name_to_package(name)

    if agent_dir.exists():
        raise FileExistsError(f"Agent directory already exists: {agent_dir}")

    pkg_dir.mkdir(parents=True, exist_ok=True)

    files: dict[Path, str] = {
        agent_dir / "agent-manifest.yaml": _gen_manifest(
            name, description, tools, recommended_model, fallback_model,
        ),
        agent_dir / "agent.py": _gen_top_level_agent(name, tools),
        agent_dir / "SKILL.md": _gen_skill_md(name, description, tools),
        agent_dir / "pyproject.toml": _gen_pyproject(name),
        pkg_dir / "__init__.py": _gen_pkg_init(name),
        pkg_dir / "agent.py": _gen_pkg_agent(name, tools),
        pkg_dir / "mcp_adapter.py": _gen_mcp_adapter(name, tools),
    }

    for path, content in files.items():
        _atomic_write(path, content)

    return agent_dir


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

create_app = typer.Typer(help="Create and scaffold new agents")


@create_app.command("agent")
def create_agent(
    name: str = typer.Argument(help="Agent name (kebab-case, e.g. my-agent)"),
    description: Optional[str] = typer.Option(
        None, "--description", "-d", help="Agent description",
    ),
    tools: str = typer.Option(
        "simple", "--tools", "-t",
        help="Tool pattern: simple (run) or pipeline (analyze/execute/report)",
    ),
    wizard: bool = typer.Option(
        False, "--wizard", "-w", help="Run interactive wizard",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output directory (default: agents/atomic/)",
    ),
) -> None:
    """Scaffold a new Atomic Agent with all required files."""
    if wizard:
        params = _run_wizard(name)
        desc = params["description"]
        tools_key = params["tools_key"]
        recommended = params["recommended_model"]
        fallback = params["fallback_model"]
    else:
        if not description:
            typer.echo("Error: --description is required without --wizard.", err=True)
            raise typer.Exit(1)
        if tools not in _TOOLS_MODE_MAP:
            typer.echo(
                f"Error: --tools must be one of: {', '.join(_TOOLS_MODE_MAP)}", err=True,
            )
            raise typer.Exit(1)
        desc = description
        tools_key = tools
        recommended = "standard"
        fallback = "economy"

    try:
        out = Path(output_dir) if output_dir else None
        agent_dir = scaffold_agent(
            name=name,
            description=desc,
            tools_key=tools_key,
            recommended_model=recommended,
            fallback_model=fallback,
            output_dir=out,
        )
    except (ValueError, FileExistsError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    tool_list = _TOOLS_MODE_MAP[tools_key]
    typer.echo(f"Created agent: {agent_dir}")
    typer.echo(f"  Tools: {', '.join(tool_list)}")
    typer.echo(f"  Files: {len(list(agent_dir.rglob('*')))} generated")
    typer.echo(f"\nNext steps:")
    typer.echo(f"  1. Edit {agent_dir / _agent_name_to_package(name) / 'agent.py'} — implement logic")
    typer.echo(f"  2. Edit {agent_dir / 'SKILL.md'} — document capabilities")
    typer.echo(f"  3. Test:  cd {agent_dir} && uv run pytest")
