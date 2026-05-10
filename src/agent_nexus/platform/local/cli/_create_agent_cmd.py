"""CLI command: ``agent-nexus create-agent``.

Capability-taxonomy aware agent scaffolding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agent_nexus.platform.local.create_agent import AgentCreator, AgentCreatorError


def create_agent_cmd(
    name: str = typer.Argument(help="Agent name (kebab-case, e.g. my-agent)"),
    type: str = typer.Option(
        "atomic",
        "--type",
        "-t",
        help="Agent type: atomic or composite",
    ),
    capabilities: Annotated[
        list[str] | None,
        typer.Option(
            "--capability",
            "-c",
            help="Capability from taxonomy (repeatable)",
        ),
    ] = None,
    description: str | None = typer.Option(
        None,
        "--description",
        "-d",
        help="Agent description",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: agents/{type}/)",
    ),
    list_caps: bool = typer.Option(
        False,
        "--list-capabilities",
        help="List all valid capabilities and exit",
    ),
) -> None:
    """Scaffold a new agent with validated capabilities from the taxonomy."""
    creator = AgentCreator()

    if list_caps:
        typer.echo("Valid capabilities:")
        for cap in creator.get_valid_capabilities():
            typer.echo(f"  {cap}")
        raise typer.Exit()

    try:
        out = Path(output_dir) if output_dir else None
        agent_dir = creator.create(
            name=name,
            type=type,
            capabilities=capabilities or [],
            description=description,
            output_dir=out,
        )
    except (AgentCreatorError, FileExistsError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None

    caps = capabilities or []
    typer.echo(f"Created agent: {agent_dir}")
    typer.echo(f"  Type: {type}")
    typer.echo(f"  Capabilities: {', '.join(caps) if caps else '(none)'}")
    typer.echo(f"  Files: {len(list(agent_dir.rglob('*')))} generated")
    typer.echo("\nNext steps:")
    typer.echo(f"  1. Edit {agent_dir / 'SKILL.md'} -- document capabilities")
    typer.echo(f"  2. Implement agent logic in {agent_dir / 'src'}")
    typer.echo(f"  3. Run: agent-nexus check {agent_dir}")
