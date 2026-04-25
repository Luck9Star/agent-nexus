"""Agency CLI commands: import-experts, plan-composition, validate-output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .importer import AgencyImporter
from .qa_gate import QAGate, QAGateInput
from .registry import ExpertRegistry
from .selector import SelectionRequest, SpecialistSelector
from .planner import DynamicCompositePlanner, SubtaskDef


@click.group()
def cli() -> None:
    """Agency agents management commands."""


@cli.command("import-experts")
@click.option("--vendor-path", required=True, help="Path to the agency-agents vendor repo")
@click.option("--allowlist", required=True, help="Path to the allowlist YAML file")
@click.option("--output-dir", required=True, help="Directory for imported profiles")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without writing files")
def import_experts(
    vendor_path: str,
    allowlist: str,
    output_dir: str,
    dry_run: bool,
) -> None:
    """Import agency-agents from the vendor repo using an allowlist."""
    importer = AgencyImporter(
        vendor_path=vendor_path,
        allowlist_path=allowlist,
        output_dir=output_dir,
    )

    try:
        if dry_run:
            profiles = importer.dry_run()
            click.echo(f"Dry run: {len(profiles)} profiles ready for import")
            for pkg in profiles:
                click.echo(f"  - {pkg['id']}")
        else:
            importer.import_all()
            # Count written files
            json_files = list(Path(output_dir).glob("*.json"))
            click.echo(f"Imported {len(json_files)} expert profiles to {output_dir}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("plan-composition")
@click.option("--task", required=True, help="Task description to plan")
@click.option("--mode", default="plan", help="Task mode (plan, review, implementation_plan)")
@click.option("--max-parallel", default=3, type=int, help="Max parallel experts")
@click.option("--vendor-path", required=True, help="Path to the agency-agents vendor repo")
@click.option("--allowlist", required=True, help="Path to the allowlist YAML file")
def plan_composition(
    task: str,
    mode: str,
    max_parallel: int,
    vendor_path: str,
    allowlist: str,
) -> None:
    """Plan a composition DAG for a given task."""
    # Import and load registry
    importer = AgencyImporter(
        vendor_path=vendor_path,
        allowlist_path=allowlist,
        output_dir="/tmp/agency-plan-composer",
    )
    profiles = importer.dry_run()

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])

    # Select specialists
    selector = SpecialistSelector(registry)

    # Infer capabilities from task
    from .task_composer import _infer_capabilities

    required_caps = _infer_capabilities(task)
    request = SelectionRequest(
        task_type=mode,
        required_capabilities=required_caps,
        optional_capabilities=[],
        max_agents=5,
        permissions="plan",
    )
    selected = selector.select(request)

    if not selected:
        click.echo("No matching specialists found for the task.")
        return

    # Build subtasks and generate DAG
    planner = DynamicCompositePlanner()
    subtasks: list[SubtaskDef] = []
    for sel in selected:
        profile = registry.get(sel.agent_id)
        artifact_type = (
            profile.get("output_contract", {}).get("artifact_type", "report")
            if profile
            else "report"
        )
        subtasks.append(
            SubtaskDef(
                id=sel.agent_id.replace("agency.", ""),
                goal=task,
                needed_capabilities=required_caps,
                output_contract=artifact_type,
                assigned_agent=sel.agent_id,
            )
        )

    from .planner import generate_toml

    dag = planner.resolve_dependencies(
        subtasks,
        composition_name=f"composition-{mode}",
        max_parallel=max_parallel,
    )
    toml_str = generate_toml(dag)

    click.echo(f"Selected {len(selected)} specialists:")
    for sel in selected:
        click.echo(f"  - {sel.agent_id} (score: {sel.score:.2f})")
    click.echo()
    click.echo(toml_str)


@cli.command("validate-output")
@click.option("--output-file", required=True, help="JSON file with expert output")
@click.option(
    "--required-sections",
    required=True,
    help="Comma-separated list of required section names",
)
@click.option("--task-type", default="plan", help="Task type for GitNexus gate check")
def validate_output(
    output_file: str,
    required_sections: str,
    task_type: str,
) -> None:
    """Validate expert output against required sections and GitNexus gate."""
    try:
        with open(output_file) as f:
            output_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        click.echo(f"Error reading output file: {exc}", err=True)
        sys.exit(1)

    sections = [s.strip() for s in required_sections.split(",") if s.strip()]

    gate_input = QAGateInput(
        output=output_data,
        required_sections=sections,
        task_type=task_type,
    )
    result = QAGate.run(gate_input)

    if result.passed:
        click.echo("Validation PASSED")
    else:
        click.echo("Validation FAILED")
        if result.contract_result.missing_sections:
            click.echo(
                f"  Missing sections: {', '.join(result.contract_result.missing_sections)}"
            )
        if not result.gitnexus_result.passed:
            click.echo(f"  GitNexus gate failures: {', '.join(result.gitnexus_result.failed_checks)}")
        sys.exit(1)


@cli.command("list-experts")
@click.option("--vendor-path", required=True, help="Path to the agency-agents vendor repo")
@click.option("--allowlist", required=True, help="Path to the allowlist YAML file")
def list_experts(
    vendor_path: str,
    allowlist: str,
) -> None:
    """Preview experts available for import from the vendor repo."""
    importer = AgencyImporter(
        vendor_path=vendor_path,
        allowlist_path=allowlist,
        output_dir="/tmp/agency-list",
    )

    try:
        profiles = importer.dry_run()
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Found {len(profiles)} experts available for import:")
    click.echo()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        caps = ", ".join(ep.get("capabilities", []))
        contract = ep.get("output_contract", {}).get("artifact_type", "unknown")
        click.echo(f"  {ep['id']}")
        click.echo(f"    Name: {ep['name']}")
        click.echo(f"    Capabilities: {caps}")
        click.echo(f"    Output contract: {contract}")
        click.echo()


@cli.command("check-profiles")
@click.option("--output-dir", required=True, help="Directory with imported profile JSON files")
def check_profiles(output_dir: str) -> None:
    """Validate previously imported expert profiles."""
    output_path = Path(output_dir)
    if not output_path.is_dir():
        click.echo(f"Error: {output_dir} is not a directory", err=True)
        sys.exit(1)

    json_files = sorted(output_path.glob("*.json"))
    if not json_files:
        click.echo("No profile files found.")
        return

    errors: list[str] = []
    checked = 0
    for jf in json_files:
        try:
            with jf.open() as f:
                profile = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{jf.name}: failed to read — {exc}")
            continue

        checked += 1

        # Validate required top-level keys
        required_keys = {"id", "name", "capabilities", "permissions", "output_contract"}
        missing = required_keys - set(profile.keys())
        if missing:
            errors.append(f"{jf.name}: missing keys {missing}")

        # Validate capabilities is non-empty
        caps = profile.get("capabilities", [])
        if not isinstance(caps, list) or len(caps) == 0:
            errors.append(f"{jf.name}: capabilities must be a non-empty list")

        # Validate permissions.mode
        perm_mode = profile.get("permissions", {}).get("mode")
        if perm_mode not in ("plan", "default", "full_auto"):
            errors.append(f"{jf.name}: invalid permission mode '{perm_mode}'")

    if errors:
        click.echo(f"Checked {checked} profiles, found {len(errors)} issue(s):")
        for err in errors:
            click.echo(f"  - {err}")
        sys.exit(1)
    else:
        click.echo(f"All {checked} profiles passed validation.")


if __name__ == "__main__":
    cli()
