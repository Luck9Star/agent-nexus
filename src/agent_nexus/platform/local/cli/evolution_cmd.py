"""Evolution subsystem commands: status, health, list, history, metrics, fix, promote."""

from __future__ import annotations

import typer

from agent_nexus.platform.local.cli._shared import _get_config_dir

evolution_app = typer.Typer(help="Self-Evolution Engine")


def _get_engine():
    """Create an EvolutionEngine instance from the evolution DB."""
    from agent_nexus.platform.evolution.engine import EvolutionEngine
    from agent_nexus.platform.evolution.store import EvolutionStore

    config_dir = _get_config_dir()
    db_path = config_dir / "evolution.db"
    store = EvolutionStore(db_path)
    engine = EvolutionEngine(store)
    return engine, store


@evolution_app.command("status")
def evolution_status() -> None:
    """Show evolution subsystem status summary."""
    engine, store = _get_engine()
    try:
        summary = engine.health_checker.get_health_summary()

        typer.echo("Evolution Status:")
        typer.echo(f"  Total skills:  {summary.get('total_skills', 0)}")
        typer.echo(f"  Healthy:       {summary.get('healthy', 0)}")
        typer.echo(f"  Unhealthy:     {summary.get('unhealthy', 0)}")
        typer.echo(f"  Suggestions:   {summary.get('suggestions', 0)}")
    finally:
        store.close()


@evolution_app.command("health")
def evolution_health(
    skill_name: str | None = typer.Argument(None, help="Skill name for detailed view"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show threshold details"),
) -> None:
    """Show health diagnostics for skills."""
    engine, store = _get_engine()
    try:
        if skill_name:
            try:
                suggestions = engine.check_health(skill_name)
                if suggestions:
                    typer.echo(f"Skill '{skill_name}': UNHEALTHY")
                    for s in suggestions:
                        typer.echo(f"  [{s.evolution_type.value}] {s.direction}")
                else:
                    typer.echo(f"Skill '{skill_name}': HEALTHY")
            except ValueError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1)
        else:
            reports = engine.diagnose_all()
            if not reports:
                typer.echo("No skills to diagnose.")
                return

            typer.echo(f"{'Name':<30} {'Applied Rate':<15} {'Completion Rate':<18} {'Fallback Rate':<16} {'Verdict'}")
            typer.echo("-" * 95)
            for report in reports.values():
                metrics = report.metrics
                applied = metrics.get("applied_rate", 0)
                completion = metrics.get("completion_rate", 0)
                fallback = metrics.get("fallback_rate", 0)
                verdict = "HEALTHY" if report.is_healthy else "UNHEALTHY"
                typer.echo(
                    f"{report.skill_name:<30} {applied:<15.2%} "
                    f"{completion:<18.2%} {fallback:<16.2%} {verdict}"
                )
    finally:
        store.close()


@evolution_app.command("list")
def evolution_list(
    all_skills: bool = typer.Option(False, "--all", help="Show all skills including inactive"),
) -> None:
    """List skills in the evolution system."""
    engine, store = _get_engine()
    try:
        skills = engine.store.get_all_skills() if all_skills else engine.store.get_active_skills()

        if not skills:
            typer.echo("No skills found.")
            return

        typer.echo(f"{'Name':<30} {'Version':<10} {'Generation':<12} {'Status':<10} {'Created'}")
        typer.echo("-" * 75)
        for skill in skills:
            status = "active" if skill.is_active else "inactive"
            created = skill.first_seen.isoformat().split("T")[0] if skill.first_seen else "-"
            typer.echo(
                f"{skill.name:<30} {skill.version:<10} "
                f"{skill.lineage.generation:<12} {status:<10} {created}"
            )
    finally:
        store.close()


@evolution_app.command("history")
def evolution_history(
    skill_name: str = typer.Argument(help="Skill name or ID to trace ancestry"),
) -> None:
    """Show version lineage for a skill."""
    engine, store = _get_engine()
    try:
        # Find skill by name or ID
        skills = engine.store.get_all_skills()
        skill_id = None
        for s in skills:
            if s.name == skill_name or s.id == skill_name:
                skill_id = s.id
                break

        if skill_id is None:
            typer.echo(f"Skill '{skill_name}' not found.", err=True)
            raise typer.Exit(code=1)

        ancestry = engine.store.get_ancestry(skill_id)
        if not ancestry:
            typer.echo(f"No ancestry found for '{skill_name}'.")
            return

        indent = ""
        for i, ancestor in enumerate(ancestry):
            created = ancestor.first_seen.isoformat().split("T")[0] if ancestor.first_seen else "?"
            typer.echo(f"{indent}{ancestor.name} (gen {ancestor.lineage.generation}, {created})")
            if i < len(ancestry) - 1:
                indent += "  -> "
    finally:
        store.close()


@evolution_app.command("metrics")
def evolution_metrics(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Filter by agent name"),
) -> None:
    """Show evolution quality metrics."""
    engine, store = _get_engine()
    try:
        metrics = engine.store.get_metrics(agent_name=agent)

        typer.echo(f"  Total selections: {metrics.total_selections}")
        typer.echo(f"  Total applied:    {metrics.total_applied}")
        typer.echo(f"  Total completions: {metrics.total_completions}")
        typer.echo(f"  Total fallbacks:  {metrics.total_fallbacks}")

        if metrics.total_selections > 0:
            success_rate = metrics.total_completions / metrics.total_selections
            fallback_rate = metrics.total_fallbacks / metrics.total_selections
            typer.echo(f"  Success rate:     {success_rate:.2%}")
            typer.echo(f"  Fallback rate:    {fallback_rate:.2%}")
    finally:
        store.close()


@evolution_app.command("fix")
def evolution_fix(
    skill_id: str = typer.Argument(help="Skill ID to fix (currently triggers all-skill check)"),
) -> None:
    """Trigger a FIX evolution on an unhealthy skill.

    Note: The current engine runs METRIC_CHECK across all skills.
    Per-skill filtering will be added when the engine supports it.
    """
    from agent_nexus.models.evolution import EvolutionTrigger

    engine, store = _get_engine()
    try:
        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        typer.echo(f"Fix evolution triggered (target: {skill_id}).")
        typer.echo(f"Results: {len(results) if isinstance(results, list) else 1} evolution(s) processed.")
    except Exception as exc:
        typer.echo(f"Fix failed: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        store.close()


@evolution_app.command("promote")
def evolution_promote(
    skill_id: str = typer.Argument(help="Skill ID to promote to agent"),
) -> None:
    """Promote a skill candidate to a standalone agent."""
    from agent_nexus.platform.evolution.promotion import PromotionCandidate

    engine, store = _get_engine()
    try:
        # CLI promote intentionally bypasses quality gates
        candidate = PromotionCandidate(
            skill_id=skill_id,
            skill_name=skill_id,
            effective_rate=0.0,
            total_selections=0,
            directory="",
            reason="cli-promote",
        )
        result = engine.promote_candidate(candidate)
        if result.success:
            typer.echo(f"Skill '{skill_id}' promoted to agent.")
            if result.agent_name:
                typer.echo(f"New agent: {result.agent_name}")
        else:
            typer.echo(f"Promotion not completed for '{skill_id}'.")
    except Exception as exc:
        typer.echo(f"Promotion failed: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        store.close()
