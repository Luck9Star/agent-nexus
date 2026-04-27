"""Agency CLI commands: import-experts, plan-composition, validate-output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .importer import AgencyImporter
from .planner import DynamicCompositePlanner, SubtaskDef
from .qa_gate import QAGate, QAGateInput
from .registry import ExpertRegistry
from .selector import SelectionRequest, SpecialistSelector

def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (directory with .git)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parents[4]  # fallback


_SCHEMA_PATH = _find_repo_root() / "schemas" / "expert-profile.schema.json"


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
    try:
        importer = AgencyImporter(
            vendor_path=vendor_path,
            allowlist_path=allowlist,
            output_dir=".",
        )
        profiles = importer.dry_run()
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])

    # Select specialists
    selector = SpecialistSelector(registry)

    # Infer capabilities from task
    from .task_composer import infer_capabilities

    required_caps = infer_capabilities(task)
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
        # Use the agent's ACTUAL capabilities (not the task-inferred ones)
        # so resolve_dependencies can compute correct dependency edges.
        agent_caps = (
            profile.get("capabilities", required_caps)
            if profile
            else required_caps
        )
        subtasks.append(
            SubtaskDef(
                id=sel.agent_id.replace("agency.", ""),
                goal=task,
                needed_capabilities=agent_caps,
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
            click.echo(
                f"  GitNexus gate failures: "
                f"{', '.join(result.gitnexus_result.failed_checks)}"
            )
        sys.exit(1)


@cli.command("list-experts")
@click.option("--vendor-path", required=True, help="Path to the agency-agents vendor repo")
@click.option("--allowlist", required=True, help="Path to the allowlist YAML file")
def list_experts(
    vendor_path: str,
    allowlist: str,
) -> None:
    """Preview experts available for import from the vendor repo."""
    try:
        importer = AgencyImporter(
            vendor_path=vendor_path,
            allowlist_path=allowlist,
            output_dir=".",
        )
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

    # Load JSON schema for validation if available
    schema = None
    try:
        import jsonschema  # type: ignore[import-untyped]

        if _SCHEMA_PATH.is_file():
            schema = json.loads(_SCHEMA_PATH.read_text())
    except ImportError:
        pass

    for jf in json_files:
        try:
            with jf.open() as f:
                profile = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{jf.name}: failed to read — {exc}")
            continue

        checked += 1

        # Schema-based validation (preferred)
        if schema is not None:
            try:
                jsonschema.validate(profile, schema)  # type: ignore[name-defined]
            except jsonschema.ValidationError as exc:  # type: ignore[name-defined]
                errors.append(f"{jf.name}: schema validation failed — {exc.message}")
            continue

        # Fallback: manual key validation (when jsonschema not installed)
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


@cli.command("run-composition")
@click.option("--task", required=True, help="Task description to plan and execute")
@click.option("--mode", default="plan", help="Task mode (plan, review, implementation_plan)")
@click.option("--max-parallel", default=3, type=int, help="Max concurrent expert executions")
@click.option("--vendor-path", required=True, help="Path to the agency-agents vendor repo")
@click.option("--allowlist", required=True, help="Path to the allowlist YAML file")
@click.option("--model", default=None, help="Override model string (e.g. 'api:MiniMax-M2.7-highspeed')")
@click.option("--config-dir", default=None, help="Config directory (default: ~/.agent-nexus/)")
def run_composition(
    task: str,
    mode: str,
    max_parallel: int,
    vendor_path: str,
    allowlist: str,
    model: str | None,
    config_dir: str | None,
) -> None:
    """Full pipeline: load experts, select, build DAG, execute, integrate, QA."""
    # Load .env from config dir so API keys are available
    _env_dir = config_dir or "~/.agent-nexus"
    _env_path = Path(_env_dir).expanduser() / ".env"
    if _env_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(_env_path)

    try:
        # Step 1: Load experts
        importer = AgencyImporter(
            vendor_path=vendor_path,
            allowlist_path=allowlist,
            output_dir=".",
        )
        profiles = importer.dry_run()
    except Exception as exc:
        click.echo(f"Error loading experts: {exc}", err=True)
        sys.exit(1)

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])

    # Step 2: Infer capabilities + select specialists
    from .task_composer import infer_capabilities

    required_caps = infer_capabilities(task)
    selector = SpecialistSelector(registry)
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

    # Step 3: Build subtasks with real agent capabilities
    subtasks: list[SubtaskDef] = []
    for sel in selected:
        profile = registry.get(sel.agent_id)
        agent_caps = (
            profile.get("capabilities", required_caps)
            if profile
            else required_caps
        )
        artifact_type = (
            profile.get("output_contract", {}).get("artifact_type", "report")
            if profile
            else "report"
        )
        subtasks.append(
            SubtaskDef(
                id=sel.agent_id.replace("agency.", ""),
                goal=task,
                needed_capabilities=agent_caps,
                output_contract=artifact_type,
                assigned_agent=sel.agent_id,
            )
        )

    # Step 4: Generate DAG
    planner = DynamicCompositePlanner()
    dag = planner.resolve_dependencies(
        subtasks,
        composition_name=f"composition-{mode}",
        max_parallel=max_parallel,
    )

    # Step 5: Execute with LLMExecutor (falls back to ProfileBasedExecutor if no config)
    from .dag_dispatcher import DAGDispatcher
    from .executor import LLMExecutor, ProfileBasedExecutor
    from agent_nexus.platform.orchestration.task_graph import TaskGraph

    try:
        executor = LLMExecutor(
            registry=registry,
            model_string=model,
            config_dir=Path(config_dir) if config_dir else None,
        )
        click.echo(f"Using LLM executor (model: {executor._model_name})")
    except Exception as exc:
        click.echo(f"LLM config unavailable ({exc}), falling back to profile-based executor", err=True)
        executor = ProfileBasedExecutor(registry=registry)

    graph = TaskGraph(":memory:")
    dispatcher = DAGDispatcher(
        graph=graph,
        executor=executor,
        max_batch_size=max_parallel,
        concurrent=True,
    )
    dispatch_result = dispatcher.dispatch(dag, task)
    graph.close()

    # Step 6: Integrate + QA
    if dispatch_result.artifacts:
        from .integrator import Integrator
        from .qa_gate import QAGate, QAGateInput

        artifacts = list(dispatch_result.artifacts.values())
        integrated = Integrator.merge(artifacts)

        # QA validation
        first_profile = registry.get(selected[0].agent_id)
        required_sections = (
            first_profile.get("output_contract", {}).get("required_sections", [])
            if first_profile
            else []
        )
        gate_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=required_sections,
            task_type=mode,
        )
        qa_result = QAGate.run(gate_input)

        # Output results
        click.echo("\n=== Composition Result ===")
        click.echo(f"Selected: {len(selected)} experts")
        click.echo(f"Completed: {len(dispatch_result.completed)}")
        click.echo(f"Failed: {len(dispatch_result.failed)}")
        click.echo(f"QA passed: {qa_result.passed}")
        click.echo(f"\n--- Merged Output ---")
        for key, value in integrated.merged_sections.items():
            click.echo(f"\n## {key}")
            click.echo(str(value))
    else:
        click.echo("No artifacts produced — all experts failed.")
        for tid, err in dispatch_result.errors.items():
            click.echo(f"  {tid}: {err}")


if __name__ == "__main__":
    cli()
