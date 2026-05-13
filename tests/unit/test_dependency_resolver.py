"""Unit tests for DependencyResolver, ResolvedDependency, and ConflictReport."""

from __future__ import annotations

from pathlib import Path

from agent_nexus.platform.local.dependency_resolver import (
    ConflictReport,
    DependencyResolver,
    ResolvedDependency,
    _parse_dep_string,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml_manifest(
    agent_dir: Path,
    *,
    name: str = "test-agent",
    version: str = "1.0.0",
    agent_type: str = "atomic",
    description: str = "A test agent",
    pip_dependencies: list[str] | None = None,
    atomic_agents: list[str] | None = None,
) -> Path:
    """Write a valid agent.toml manifest into agent_dir."""
    lines = [
        "[agent]",
        f'name = "{name}"',
        f'version = "{version}"',
        f'type = "{agent_type}"',
        f'description = "{description}"',
    ]
    if pip_dependencies:
        items = ", ".join(f'"{d}"' for d in pip_dependencies)
        lines.append(f"pip_dependencies = [{items}]")
    if atomic_agents:
        items = ", ".join(f'"{a}"' for a in atomic_agents)
        lines.append("")
        lines.append("[agent.dependencies]")
        lines.append(f"atomic_agents = [{items}]")
    manifest_path = agent_dir / "agent.toml"
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def _make_agent_dir(
    tmp_path: Path,
    name: str = "test-agent",
    pip_dependencies: list[str] | None = None,
    atomic_agents: list[str] | None = None,
) -> Path:
    """Create a minimal agent directory with manifest."""
    agent_dir = tmp_path / name
    agent_dir.mkdir()
    _write_toml_manifest(
        agent_dir,
        name=name,
        pip_dependencies=pip_dependencies,
        atomic_agents=atomic_agents,
    )
    return agent_dir


# ============================================================================
# _parse_dep_string helper
# ============================================================================


class TestParseDepString:
    def test_simple_name(self) -> None:
        name, spec = _parse_dep_string("requests")
        assert name == "requests"
        assert spec == ""

    def test_name_with_version(self) -> None:
        name, spec = _parse_dep_string("requests>=2.0")
        assert name == "requests"
        assert spec == ">=2.0"

    def test_name_with_complex_version(self) -> None:
        name, spec = _parse_dep_string("numpy>=1.0,<2.0")
        assert name == "numpy"
        assert spec == ">=1.0,<2.0"

    def test_name_with_extras(self) -> None:
        name, spec = _parse_dep_string("requests[security]>=2.0")
        assert name == "requests[security]"
        assert spec == ">=2.0"

    def test_whitespace_stripped(self) -> None:
        name, spec = _parse_dep_string("  requests >= 2.0  ")
        assert name == "requests"
        assert spec == ">= 2.0"


# ============================================================================
# ResolvedDependency dataclass
# ============================================================================


class TestResolvedDependency:
    def test_create_pip_dep(self) -> None:
        dep = ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip")
        assert dep.name == "requests"
        assert dep.dep_type == "pip"
        assert dep.resolved_path is None

    def test_create_agent_dep(self) -> None:
        dep = ResolvedDependency(
            name="doc-filler", version_spec="", dep_type="agent", resolved_path="/agents/doc-filler"
        )
        assert dep.dep_type == "agent"
        assert dep.resolved_path == "/agents/doc-filler"


# ============================================================================
# ConflictReport dataclass
# ============================================================================


class TestConflictReport:
    def test_create(self) -> None:
        report = ConflictReport(
            dep_name="requests",
            agent_a="agent-a",
            agent_b="agent-b",
            conflict_reason="Version mismatch",
        )
        assert report.dep_name == "requests"
        assert report.agent_a == "agent-a"


# ============================================================================
# DependencyResolver.resolve
# ============================================================================


class TestDependencyResolverResolve:
    def test_no_manifest_returns_empty(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "empty"
        agent_dir.mkdir()
        resolver = DependencyResolver()
        assert resolver.resolve(agent_dir) == []

    def test_no_dependencies_returns_empty(self, tmp_path: Path) -> None:
        agent_dir = _make_agent_dir(tmp_path)
        resolver = DependencyResolver()
        deps = resolver.resolve(agent_dir)
        assert deps == []

    def test_resolves_pip_dependencies(self, tmp_path: Path) -> None:
        agent_dir = _make_agent_dir(tmp_path, pip_dependencies=["requests>=2.0", "numpy"])
        resolver = DependencyResolver()
        deps = resolver.resolve(agent_dir)

        assert len(deps) == 2
        assert deps[0].name == "requests"
        assert deps[0].version_spec == ">=2.0"
        assert deps[0].dep_type == "pip"
        assert deps[1].name == "numpy"
        assert deps[1].version_spec == ""
        assert deps[1].dep_type == "pip"

    def test_resolves_atomic_agents(self, tmp_path: Path) -> None:
        # Create agents_dir with a sub-directory for the dependency
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "doc-filler").mkdir()

        agent_dir = _make_agent_dir(tmp_path, name="composite-1", atomic_agents=["doc-filler"])
        resolver = DependencyResolver(agents_dir=agents_dir)
        deps = resolver.resolve(agent_dir)

        assert len(deps) == 1
        assert deps[0].name == "doc-filler"
        assert deps[0].dep_type == "agent"
        assert deps[0].resolved_path is not None
        assert "doc-filler" in deps[0].resolved_path

    def test_atomic_agent_not_installed(self, tmp_path: Path) -> None:
        agent_dir = _make_agent_dir(tmp_path, name="composite-2", atomic_agents=["missing-agent"])
        resolver = DependencyResolver(agents_dir=tmp_path / "agents")
        deps = resolver.resolve(agent_dir)

        assert len(deps) == 1
        assert deps[0].resolved_path is None

    def test_atomic_agent_no_agents_dir(self, tmp_path: Path) -> None:
        agent_dir = _make_agent_dir(tmp_path, name="composite-3", atomic_agents=["some-agent"])
        resolver = DependencyResolver()  # No agents_dir
        deps = resolver.resolve(agent_dir)

        assert len(deps) == 1
        assert deps[0].resolved_path is None

    def test_mixed_dependencies(self, tmp_path: Path) -> None:
        agent_dir = _make_agent_dir(
            tmp_path,
            name="mixed",
            pip_dependencies=["requests>=2.0"],
            atomic_agents=["doc-filler"],
        )
        resolver = DependencyResolver()
        deps = resolver.resolve(agent_dir)

        assert len(deps) == 2
        pip_deps = [d for d in deps if d.dep_type == "pip"]
        agent_deps = [d for d in deps if d.dep_type == "agent"]
        assert len(pip_deps) == 1
        assert len(agent_deps) == 1


# ============================================================================
# DependencyResolver.check_conflicts
# ============================================================================


class TestDependencyResolverCheckConflicts:
    def test_no_conflicts_single_agent(self, tmp_path: Path) -> None:
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert conflicts == []

    def test_no_conflicts_compatible_specs(self, tmp_path: Path) -> None:
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip"),
            ],
            "agent-b": [
                ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert conflicts == []

    def test_detects_version_conflict(self, tmp_path: Path) -> None:
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip"),
            ],
            "agent-b": [
                ResolvedDependency(name="requests", version_spec="<2.0", dep_type="pip"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert len(conflicts) == 1
        assert conflicts[0].dep_name == "requests"
        assert conflicts[0].agent_a == "agent-a"
        assert conflicts[0].agent_b == "agent-b"
        assert "mismatch" in conflicts[0].conflict_reason.lower()

    def test_no_conflict_when_no_version_spec(self, tmp_path: Path) -> None:
        """Agents with no version spec should not trigger conflicts."""
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="requests", version_spec="", dep_type="pip"),
            ],
            "agent-b": [
                ResolvedDependency(name="requests", version_spec="", dep_type="pip"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert conflicts == []

    def test_multiple_conflicts(self, tmp_path: Path) -> None:
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="requests", version_spec=">=2.0", dep_type="pip"),
                ResolvedDependency(name="numpy", version_spec=">=1.0", dep_type="pip"),
            ],
            "agent-b": [
                ResolvedDependency(name="requests", version_spec="<2.0", dep_type="pip"),
                ResolvedDependency(name="numpy", version_spec="<1.0", dep_type="pip"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert len(conflicts) == 2

    def test_agent_deps_not_checked_for_conflicts(self, tmp_path: Path) -> None:
        """Agent-type dependencies should not be checked for version conflicts."""
        resolver = DependencyResolver()
        deps_by_agent = {
            "agent-a": [
                ResolvedDependency(name="doc-filler", version_spec="", dep_type="agent"),
            ],
            "agent-b": [
                ResolvedDependency(name="doc-filler", version_spec="", dep_type="agent"),
            ],
        }
        conflicts = resolver.check_conflicts(deps_by_agent)
        assert conflicts == []

    def test_empty_input(self, tmp_path: Path) -> None:
        resolver = DependencyResolver()
        conflicts = resolver.check_conflicts({})
        assert conflicts == []
