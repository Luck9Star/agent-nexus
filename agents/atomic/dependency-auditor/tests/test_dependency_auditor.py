"""Tests for dependency-auditor agent.

Covers:
- Models: construction, validation, immutability
- audit_dependencies: dict input, requirements.txt parsing, pyproject.toml parsing
- Version comparison helpers
- Agent: full pipeline
- MCP adapter: server creation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_dependency_auditor.agent import DependencyAuditorAgent
from agent_dependency_auditor.models import AuditReport, DependencyVulnerability
from agent_dependency_auditor.tools.audit_dependencies import (
    _is_vulnerable,
    _parse_pyproject_toml,
    _parse_requirements_txt,
    _version_tuple,
    audit_dependencies,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> DependencyAuditorAgent:
    """Provide a DependencyAuditorAgent instance."""
    return DependencyAuditorAgent()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestDependencyVulnerability:
    """Tests for DependencyVulnerability model."""

    def test_basic_construction(self) -> None:
        v = DependencyVulnerability(
            package="flask", installed_version="2.0.1", cve="CVE-2023-30861"
        )
        assert v.package == "flask"
        assert v.installed_version == "2.0.1"
        assert v.severity == "medium"
        assert v.summary == ""
        assert v.fixed_in == ""

    def test_full_construction(self) -> None:
        v = DependencyVulnerability(
            package="django",
            installed_version="3.0.0",
            cve="CVE-2022-28347",
            severity="critical",
            summary="SQL injection",
            fixed_in="3.2.13",
        )
        assert v.severity == "critical"
        assert v.fixed_in == "3.2.13"

    def test_frozen(self) -> None:
        v = DependencyVulnerability(package="flask", installed_version="1.0")
        with pytest.raises(ValidationError):
            v.package = "django"  # type: ignore[misc]


class TestAuditReport:
    """Tests for AuditReport model."""

    def test_empty(self) -> None:
        r = AuditReport()
        assert r.vulnerabilities == []
        assert r.total_scanned == 0
        assert r.vulnerable_count == 0
        assert r.summary == {}

    def test_with_vulns(self) -> None:
        v = DependencyVulnerability(package="flask", installed_version="2.0.1")
        r = AuditReport(vulnerabilities=[v], total_scanned=5, vulnerable_count=1)
        assert r.total_scanned == 5
        assert r.vulnerable_count == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestVersionTuple:
    """Tests for _version_tuple helper."""

    def test_simple(self) -> None:
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_single(self) -> None:
        assert _version_tuple("5") == (5,)

    def test_complex(self) -> None:
        assert _version_tuple("2.0.1") == (2, 0, 1)


class TestIsVulnerable:
    """Tests for _is_vulnerable helper."""

    def test_vulnerable(self) -> None:
        assert _is_vulnerable("1.0.0", "2.0.0") is True

    def test_not_vulnerable(self) -> None:
        assert _is_vulnerable("2.0.0", "2.0.0") is False

    def test_newer(self) -> None:
        assert _is_vulnerable("3.1.0", "3.0.0") is False


class TestParseRequirementsTxt:
    """Tests for _parse_requirements_txt."""

    def test_basic(self) -> None:
        content = "flask==2.0.1\nrequests==2.25.0\n"
        deps = _parse_requirements_txt(content)
        assert deps == {"flask": "2.0.1", "requests": "2.25.0"}

    def test_comments_ignored(self) -> None:
        content = "# comment\nflask==2.0.1\n# another\n"
        deps = _parse_requirements_txt(content)
        assert deps == {"flask": "2.0.1"}

    def test_empty(self) -> None:
        deps = _parse_requirements_txt("")
        assert deps == {}

    def test_flags_ignored(self) -> None:
        content = "--index-url https://pypi.org/simple\nflask==2.0.1\n"
        deps = _parse_requirements_txt(content)
        assert deps == {"flask": "2.0.1"}


class TestParsePyprojectToml:
    """Tests for _parse_pyproject_toml."""

    def test_basic(self) -> None:
        content = '[project.dependencies]\nflask = ">=2.0.1"\nrequests = ">=2.25.0"\n'
        deps = _parse_pyproject_toml(content)
        assert "flask" in deps
        assert "requests" in deps

    def test_no_deps_section(self) -> None:
        content = '[project]\nname = "test"\n'
        deps = _parse_pyproject_toml(content)
        assert deps == {}


# ---------------------------------------------------------------------------
# audit_dependencies tool
# ---------------------------------------------------------------------------


class TestAuditDependencies:
    """Tests for audit_dependencies tool."""

    def test_dict_input_empty(self) -> None:
        result = audit_dependencies({})
        assert result.total_scanned == 0
        assert result.vulnerable_count == 0

    def test_dict_input_vulnerable(self) -> None:
        result = audit_dependencies({"flask": "1.0.0"})
        assert result.vulnerable_count >= 1
        assert any(v.cve for v in result.vulnerabilities)

    def test_dict_input_safe(self) -> None:
        result = audit_dependencies({"numpy": "99.0.0"})
        assert result.vulnerable_count == 0

    def test_requirements_txt_input(self) -> None:
        content = "flask==1.0.0\nrequests==2.19.0\n"
        result = audit_dependencies(content)
        assert result.total_scanned == 2
        assert result.vulnerable_count >= 1

    def test_pyproject_toml_input(self) -> None:
        content = '[project.dependencies]\nflask = ">=1.0.0"\n'
        result = audit_dependencies(content)
        assert result.total_scanned >= 1

    def test_explicit_format_requirements(self) -> None:
        content = "flask==1.0.0\n"
        result = audit_dependencies(content, fmt="requirements")
        assert result.total_scanned == 1

    def test_explicit_format_pyproject(self) -> None:
        content = '[project.dependencies]\nflask = ">=1.0.0"\n'
        result = audit_dependencies(content, fmt="pyproject")
        assert result.total_scanned >= 1

    def test_invalid_source_type(self) -> None:
        with pytest.raises(TypeError, match="Expected dict or str"):
            audit_dependencies(123)  # type: ignore[arg-type]

    def test_summary_has_totals(self) -> None:
        result = audit_dependencies({"flask": "1.0.0"})
        assert "total" in result.summary


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestDependencyAuditorAgent:
    """Tests for DependencyAuditorAgent class."""

    def test_audit_dict(self, agent: DependencyAuditorAgent) -> None:
        result = agent.audit_dependencies({"flask": "1.0.0"})
        assert isinstance(result, AuditReport)
        assert result.total_scanned == 1

    def test_audit_requirements_txt(self, agent: DependencyAuditorAgent) -> None:
        result = agent.audit_dependencies("flask==1.0.0\nrequests==2.19.0\n")
        assert result.total_scanned == 2

    def test_audit_safe_deps(self, agent: DependencyAuditorAgent) -> None:
        result = agent.audit_dependencies({"numpy": "99.0.0"})
        assert result.vulnerable_count == 0


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_dependency_auditor.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_dependency_auditor.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")
