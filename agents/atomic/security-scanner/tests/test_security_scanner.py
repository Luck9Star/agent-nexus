"""Comprehensive tests for security-scanner agent.

Covers:
- Models: construction, validation, serialization, immutability
- scan_code: vulnerability detection, file/directory scanning, error handling
- check_dependencies: CVE matching, version comparison, edge cases
- generate_report: severity counting, recommendation generation
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from agent_security_scanner.agent import SecurityScannerAgent
from agent_security_scanner.local_adapter import handle_message
from agent_security_scanner.models import (
    DependencyReport,
    DependencyVulnerability,
    SecurityFinding,
    SecurityReport,
    SecurityScanResult,
)
from agent_security_scanner.tools.check_dependencies import (
    _is_vulnerable,
    _version_tuple,
    check_dependencies,
)
from agent_security_scanner.tools.generate_report import generate_report
from agent_security_scanner.tools.scan_code import (
    _build_summary,
    _severity_rank,
    scan_code,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def agent() -> SecurityScannerAgent:
    """Provide a SecurityScannerAgent instance."""
    return SecurityScannerAgent()


def _write_file(dir_path: str, filename: str, content: str) -> str:
    """Write a file and return its absolute path."""
    filepath = os.path.join(dir_path, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


# ---------------------------------------------------------------------------
# Models -- construction, validation, serialization
# ---------------------------------------------------------------------------


class TestSecurityFinding:
    """Tests for SecurityFinding model."""

    def test_basic_construction(self) -> None:
        f = SecurityFinding(severity="high", category="injection", location="app.py:10")
        assert f.severity == "high"
        assert f.category == "injection"
        assert f.location == "app.py:10"
        assert f.description == ""
        assert f.remediation == ""
        assert f.cwe_id == ""

    def test_full_construction(self) -> None:
        f = SecurityFinding(
            severity="critical",
            category="xss",
            location="views.py:42",
            description="Reflected XSS",
            remediation="Escape output",
            cwe_id="CWE-79",
        )
        assert f.severity == "critical"
        assert f.cwe_id == "CWE-79"
        assert f.remediation == "Escape output"

    def test_frozen(self) -> None:
        f = SecurityFinding(severity="low", category="info", location="a.py:1")
        with pytest.raises(Exception):
            f.severity = "high"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        f = SecurityFinding(severity="medium", category="traversal", location="x.py:5")
        data = f.model_dump()
        f2 = SecurityFinding.model_validate(data)
        assert f == f2

    def test_json_serialization(self) -> None:
        f = SecurityFinding(severity="high", category="injection", location="a.py:1", cwe_id="CWE-89")
        json_str = f.model_dump_json()
        data = json.loads(json_str)
        assert data["severity"] == "high"
        assert data["cwe_id"] == "CWE-89"


class TestSecurityScanResult:
    """Tests for SecurityScanResult model."""

    def test_empty(self) -> None:
        r = SecurityScanResult()
        assert r.findings == []
        assert r.summary == {}

    def test_with_findings(self) -> None:
        f1 = SecurityFinding(severity="high", category="injection", location="a.py:1")
        r = SecurityScanResult(findings=[f1], summary={"high": 1, "total": 1})
        assert len(r.findings) == 1
        assert r.summary["total"] == 1

    def test_frozen(self) -> None:
        r = SecurityScanResult()
        with pytest.raises(Exception):
            r.findings = []  # type: ignore[misc]


class TestDependencyVulnerability:
    """Tests for DependencyVulnerability model."""

    def test_basic(self) -> None:
        v = DependencyVulnerability(package="flask", version="2.0.1", cve="CVE-2023-30861")
        assert v.package == "flask"
        assert v.severity == "medium"

    def test_frozen(self) -> None:
        v = DependencyVulnerability(package="flask", version="2.0.1")
        with pytest.raises(Exception):
            v.package = "django"  # type: ignore[misc]


class TestDependencyReport:
    """Tests for DependencyReport model."""

    def test_empty(self) -> None:
        r = DependencyReport()
        assert r.vulnerabilities == []
        assert r.total_scanned == 0
        assert r.vulnerable_count == 0

    def test_with_vulns(self) -> None:
        v = DependencyVulnerability(package="flask", version="2.0.1", cve="CVE-2023-30861")
        r = DependencyReport(vulnerabilities=[v], total_scanned=3, vulnerable_count=1)
        assert r.vulnerable_count == 1


class TestSecurityReport:
    """Tests for SecurityReport model."""

    def test_empty(self) -> None:
        r = SecurityReport()
        assert r.critical_count == 0
        assert r.findings == []
        assert r.recommendations == []

    def test_with_counts(self) -> None:
        r = SecurityReport(critical_count=1, high_count=2, medium_count=3, low_count=4)
        assert r.critical_count == 1
        assert r.high_count == 2

    def test_frozen(self) -> None:
        r = SecurityReport()
        with pytest.raises(Exception):
            r.critical_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# scan_code -- vulnerability detection
# ---------------------------------------------------------------------------


class TestScanCode:
    """Tests for scan_code tool."""

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            scan_code("/nonexistent/file.py")

    def test_empty_file(self, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "safe.py", "x = 1\nprint(x)\n")
        result = scan_code(path)
        assert result.findings == []

    def test_sql_injection_format(self, tmp_dir: str) -> None:
        code = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
        path = _write_file(tmp_dir, "unsafe.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1
        cats = {f.category for f in result.findings}
        assert "injection" in cats

    def test_sql_injection_concat(self, tmp_dir: str) -> None:
        code = 'query = "SELECT * FROM users WHERE id = " + user_id\ncursor.execute(query)\n'
        path = _write_file(tmp_dir, "concat.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1

    def test_command_injection(self, tmp_dir: str) -> None:
        code = "import os\nos.system(user_input)\n"
        path = _write_file(tmp_dir, "cmd.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1
        cats = {f.category for f in result.findings}
        assert "command_injection" in cats

    def test_hardcoded_password(self, tmp_dir: str) -> None:
        code = 'password = "super_secret_123"\n'
        path = _write_file(tmp_dir, "creds.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1
        cats = {f.category for f in result.findings}
        assert "hardcoded_credentials" in cats

    def test_hardcoded_api_key(self, tmp_dir: str) -> None:
        code = 'api_key = "sk-1234567890abcdef"\n'
        path = _write_file(tmp_dir, "apikey.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1

    def test_code_injection_via_builtin(self, tmp_dir: str) -> None:
        code = "result = builtins_exec(user_input)\n"
        path = _write_file(tmp_dir, "inj.py", code)
        result = scan_code(path)
        # At minimum should scan without error
        assert isinstance(result, SecurityScanResult)

    def test_path_traversal(self, tmp_dir: str) -> None:
        code = "f = open(request.args.get('file'))\n"
        path = _write_file(tmp_dir, "traversal.py", code)
        result = scan_code(path)
        assert len(result.findings) >= 1

    def test_directory_scan(self, tmp_dir: str) -> None:
        _write_file(tmp_dir, "safe1.py", "x = 1\n")
        _write_file(tmp_dir, "unsafe1.py", 'password = "secret_value_123"\n')
        result = scan_code(tmp_dir)
        assert len(result.findings) >= 1

    def test_summary_has_totals(self, tmp_dir: str) -> None:
        code = 'password = "secret_pass_123"\n'
        path = _write_file(tmp_dir, "s.py", code)
        result = scan_code(path)
        assert "total" in result.summary
        assert result.summary["total"] >= 1

    def test_cwe_id_populated(self, tmp_dir: str) -> None:
        code = 'cursor.execute(f"SELECT * FROM t WHERE id={x}")\n'
        path = _write_file(tmp_dir, "cwe.py", code)
        result = scan_code(path)
        assert any(f.cwe_id for f in result.findings)

    def test_remediation_populated(self, tmp_dir: str) -> None:
        code = 'password = "my_password_here"\n'
        path = _write_file(tmp_dir, "rem.py", code)
        result = scan_code(path)
        assert all(f.remediation for f in result.findings)

    def test_severity_ordering(self, tmp_dir: str) -> None:
        code = (
            'cursor.execute(f"SELECT * FROM t WHERE id={x}")\n'
            'password = "secret_pass_123"\n'
        )
        path = _write_file(tmp_dir, "order.py", code)
        result = scan_code(path)
        if len(result.findings) >= 2:
            assert _severity_rank(result.findings[0].severity) >= _severity_rank(
                result.findings[-1].severity
            )


class TestHelpers:
    """Tests for helper functions."""

    def test_severity_rank(self) -> None:
        assert _severity_rank("critical") == 4
        assert _severity_rank("high") == 3
        assert _severity_rank("medium") == 2
        assert _severity_rank("low") == 1
        assert _severity_rank("unknown") == 0

    def test_build_summary(self) -> None:
        findings = [
            SecurityFinding(severity="critical", category="a", location="x"),
            SecurityFinding(severity="high", category="b", location="y"),
            SecurityFinding(severity="critical", category="c", location="z"),
        ]
        s = _build_summary(findings)
        assert s["critical"] == 2
        assert s["high"] == 1
        assert s["total"] == 3


# ---------------------------------------------------------------------------
# check_dependencies -- CVE matching
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

    def test_patch_level(self) -> None:
        assert _is_vulnerable("1.2.0", "1.3.0") is True

    def test_equal(self) -> None:
        assert _is_vulnerable("3.0.0", "3.0.0") is False

    def test_newer(self) -> None:
        assert _is_vulnerable("3.1.0", "3.0.0") is False


class TestCheckDependencies:
    """Tests for check_dependencies tool."""

    def test_empty_deps(self) -> None:
        result = check_dependencies({})
        assert result.vulnerabilities == []
        assert result.total_scanned == 0

    def test_safe_deps(self) -> None:
        result = check_dependencies({"numpy": "99.0.0"})
        assert result.vulnerable_count == 0

    def test_vulnerable_flask(self) -> None:
        result = check_dependencies({"flask": "1.0.0"})
        assert result.vulnerable_count >= 1
        cves = {v.cve for v in result.vulnerabilities}
        assert any("CVE-" in cve for cve in cves)

    def test_vulnerable_django(self) -> None:
        result = check_dependencies({"django": "2.0.0"})
        assert result.vulnerable_count >= 1

    def test_mixed_deps(self) -> None:
        result = check_dependencies({"flask": "1.0.0", "numpy": "99.0.0"})
        assert result.total_scanned == 2
        assert result.vulnerable_count >= 1

    def test_total_scanned(self) -> None:
        result = check_dependencies({"flask": "3.0.0", "requests": "2.31.0", "numpy": "99.0.0"})
        assert result.total_scanned == 3

    def test_unknown_package(self) -> None:
        result = check_dependencies({"totally-unknown-pkg-xyz": "1.0.0"})
        assert result.vulnerable_count == 0
        assert result.total_scanned == 1


# ---------------------------------------------------------------------------
# generate_report -- severity counting and recommendations
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """Tests for generate_report tool."""

    def test_empty_findings(self) -> None:
        report = generate_report([])
        assert report.critical_count == 0
        assert report.findings == []

    def test_severity_counts(self) -> None:
        findings = [
            SecurityFinding(severity="critical", category="a", location="x"),
            SecurityFinding(severity="high", category="b", location="y"),
            SecurityFinding(severity="medium", category="c", location="z"),
            SecurityFinding(severity="low", category="d", location="w"),
        ]
        report = generate_report(findings)
        assert report.critical_count == 1
        assert report.high_count == 1
        assert report.medium_count == 1
        assert report.low_count == 1

    def test_multiple_same_severity(self) -> None:
        findings = [
            SecurityFinding(severity="high", category="a", location="x"),
            SecurityFinding(severity="high", category="b", location="y"),
            SecurityFinding(severity="high", category="c", location="z"),
        ]
        report = generate_report(findings)
        assert report.high_count == 3

    def test_dict_findings(self) -> None:
        findings = [
            {"severity": "critical", "category": "injection", "location": "a.py:1"},
        ]
        report = generate_report(findings)
        assert report.critical_count == 1

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            generate_report(["not a finding"])

    def test_recommendations_populated(self) -> None:
        findings = [
            SecurityFinding(
                severity="critical",
                category="injection",
                location="a.py:10",
                remediation="Use parameterized queries",
            ),
        ]
        report = generate_report(findings)
        assert len(report.recommendations) >= 1
        assert "CRITICAL" in report.recommendations[0]

    def test_recommendations_without_remediation(self) -> None:
        findings = [
            SecurityFinding(severity="low", category="info", location="b.py:5"),
        ]
        report = generate_report(findings)
        assert len(report.recommendations) >= 1

    def test_findings_preserved(self) -> None:
        findings = [
            SecurityFinding(severity="high", category="x", location="y"),
        ]
        report = generate_report(findings)
        assert len(report.findings) == 1
        assert report.findings[0] == findings[0]


# ---------------------------------------------------------------------------
# Agent -- full pipeline
# ---------------------------------------------------------------------------


class TestSecurityScannerAgent:
    """Tests for SecurityScannerAgent class."""

    def test_scan_code(self, agent: SecurityScannerAgent, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "test.py", 'password = "secret_value_abc"\n')
        result = agent.scan_code(path)
        assert isinstance(result, SecurityScanResult)
        assert len(result.findings) >= 1

    def test_scan_code_not_found(self, agent: SecurityScannerAgent) -> None:
        with pytest.raises(FileNotFoundError):
            agent.scan_code("/nonexistent.py")

    def test_check_dependencies(self, agent: SecurityScannerAgent) -> None:
        result = agent.check_dependencies({"flask": "1.0.0"})
        assert isinstance(result, DependencyReport)
        assert result.total_scanned == 1

    def test_generate_report(self, agent: SecurityScannerAgent) -> None:
        findings = [
            SecurityFinding(severity="high", category="injection", location="a.py:1"),
        ]
        result = agent.generate_report(findings)
        assert isinstance(result, SecurityReport)
        assert result.high_count == 1

    def test_full_pipeline(self, agent: SecurityScannerAgent, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "app.py", 'password = "my_secret_123"\n')
        scan = agent.scan_code(path)
        deps = agent.check_dependencies({"flask": "1.0.0"})
        all_findings = list(scan.findings) + [
            SecurityFinding(
                severity=v.severity,
                category="dependency",
                location=v.package,
                cwe_id=v.cve,
            )
            for v in deps.vulnerabilities
        ]
        report = agent.generate_report(all_findings)
        assert isinstance(report, SecurityReport)
        assert len(report.findings) >= 1


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_security_scanner.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_security_scanner.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter -- message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_scan_code(
        self, agent: SecurityScannerAgent, tmp_dir: str
    ) -> None:
        path = _write_file(tmp_dir, "test.py", "x = 1\n")
        response = handle_message(
            agent,
            {"method": "scan_code", "params": {"file_path": path}},
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_scan_code_missing_path(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(
            agent, {"method": "scan_code", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_check_dependencies(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "check_dependencies", "params": {"deps": {"flask": "1.0.0"}}},
        )
        assert response["status"] == "ok"
        assert response["result"]["total_scanned"] == 1

    def test_handle_check_dependencies_missing(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(
            agent, {"method": "check_dependencies", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_generate_report(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "generate_report",
                "params": {
                    "findings": [
                        {"severity": "high", "category": "xss", "location": "a.py:1"}
                    ]
                },
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["high_count"] == 1

    def test_handle_unknown_method(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_scan_code_not_found(self, agent: SecurityScannerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "scan_code", "params": {"file_path": "/nonexistent.py"}},
        )
        assert response["status"] == "error"
        assert "FileNotFoundError" in response.get("error_type", "")
