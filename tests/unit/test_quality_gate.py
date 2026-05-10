"""Unit tests for QualityGate and individual checks."""

from __future__ import annotations

from pathlib import Path


from agent_nexus.platform.local.quality_gate import (
    BaseCheck,
    CheckResult,
    CheckSeverity,
    CheckVerdict,
    DependencyCheck,
    ManifestCheck,
    QualityGate,
    QualityGateResult,
    SecurityCheck,
    SkillFileCheck,
    TestCoverageCheck,
)


# ---------------------------------------------------------------------------
# Helpers: fixture-style directory builders
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


def _write_skill_md(agent_dir: Path, content: str = "# Test Skill\n\nDoes things.") -> Path:
    """Write a SKILL.md file."""
    path = agent_dir / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def _write_python_file(agent_dir: Path, filename: str, code: str) -> Path:
    """Write a Python file into the agent directory."""
    path = agent_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def _make_valid_agent(tmp_path: Path) -> Path:
    """Create a minimal valid agent directory."""
    agent_dir = tmp_path / "test-agent"
    agent_dir.mkdir()
    _write_toml_manifest(agent_dir)
    _write_skill_md(agent_dir)
    return agent_dir


# ============================================================================
# ManifestCheck
# ============================================================================


class TestManifestCheck:
    def test_passes_with_valid_toml(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        result = ManifestCheck().run(agent_dir)
        assert result.passed
        assert result.check_name == "manifest"

    def test_fails_when_no_manifest(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "empty-agent"
        agent_dir.mkdir()
        result = ManifestCheck().run(agent_dir)
        assert not result.passed
        assert "No manifest" in result.message

    def test_fails_when_missing_required_field(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "bad-agent"
        agent_dir.mkdir()
        # Write manifest without "type" field
        manifest = agent_dir / "agent.toml"
        manifest.write_text(
            '[agent]\nname = "x"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        result = ManifestCheck().run(agent_dir)
        assert not result.passed
        assert "type" in result.message

    def test_fails_with_invalid_type(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "bad-type"
        agent_dir.mkdir()
        manifest = agent_dir / "agent.toml"
        manifest.write_text(
            '[agent]\nname = "x"\nversion = "1.0.0"\ntype = "unknown"\ndescription = "test"\n',
            encoding="utf-8",
        )
        result = ManifestCheck().run(agent_dir)
        assert not result.passed
        assert "Invalid agent type" in result.message

    def test_severity_is_critical(self) -> None:
        assert ManifestCheck().severity == CheckSeverity.CRITICAL


# ============================================================================
# SkillFileCheck
# ============================================================================


class TestSkillFileCheck:
    def test_passes_with_valid_skill(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        result = SkillFileCheck().run(agent_dir)
        assert result.passed

    def test_fails_when_missing(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "no-skill"
        agent_dir.mkdir()
        result = SkillFileCheck().run(agent_dir)
        assert not result.passed
        assert "Missing" in result.message

    def test_fails_when_empty(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "empty-skill"
        agent_dir.mkdir()
        _write_skill_md(agent_dir, content="   \n\n  ")
        result = SkillFileCheck().run(agent_dir)
        assert not result.passed
        assert "empty" in result.message

    def test_severity_is_critical(self) -> None:
        assert SkillFileCheck().severity == CheckSeverity.CRITICAL


# ============================================================================
# SecurityCheck
# ============================================================================


class TestSecurityCheck:
    def test_passes_with_safe_code(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "safe.py", "x = 1 + 2\nprint(x)\n")
        result = SecurityCheck().run(agent_dir)
        assert result.passed

    def test_detects_eval(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "danger.py", 'result = eval("1+1")\n')
        result = SecurityCheck().run(agent_dir)
        assert not result.passed
        assert "eval" in result.message

    def test_detects_exec(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "danger.py", 'exec("x=1")\n')
        result = SecurityCheck().run(agent_dir)
        assert not result.passed
        assert "exec" in result.message

    def test_detects_subprocess_import(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "sub.py", "import subprocess\n")
        result = SecurityCheck().run(agent_dir)
        assert not result.passed
        assert "subprocess" in result.message

    def test_detects_subprocess_from_import(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "sub.py", "from subprocess import run\n")
        result = SecurityCheck().run(agent_dir)
        assert not result.passed
        assert "subprocess" in result.message

    def test_detects_subprocess_call(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "sub.py", "import subprocess\nsubprocess.run(['ls'])\n")
        result = SecurityCheck().run(agent_dir)
        assert not result.passed
        # Should detect both the import and the call
        assert "subprocess" in result.message

    def test_skips_unparseable_files(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "bad_syntax.py", "def f(\n")
        result = SecurityCheck().run(agent_dir)
        assert result.passed

    def test_no_python_files_passes(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        result = SecurityCheck().run(agent_dir)
        assert result.passed

    def test_severity_is_critical(self) -> None:
        assert SecurityCheck().severity == CheckSeverity.CRITICAL


# ============================================================================
# DependencyCheck
# ============================================================================


class TestDependencyCheck:
    def test_passes_with_no_dependencies(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        result = DependencyCheck().run(agent_dir)
        assert result.passed

    def test_passes_with_valid_deps(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "dep-agent"
        agent_dir.mkdir()
        _write_toml_manifest(agent_dir, pip_dependencies=["requests>=2.0", "numpy"])
        result = DependencyCheck().run(agent_dir)
        assert result.passed

    def test_fails_with_invalid_dep_name(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "bad-dep"
        agent_dir.mkdir()
        _write_toml_manifest(agent_dir, pip_dependencies=["!!invalid!!"])
        result = DependencyCheck().run(agent_dir)
        assert not result.passed
        assert "invalid" in result.message.lower()

    def test_no_manifest_passes(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "no-manifest"
        agent_dir.mkdir()
        result = DependencyCheck().run(agent_dir)
        assert result.passed

    def test_severity_is_warning(self) -> None:
        assert DependencyCheck().severity == CheckSeverity.WARNING


# ============================================================================
# TestCoverageCheck
# ============================================================================


class TestTestCoverageCheck:
    def test_passes_with_test_files(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        tests_dir = agent_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_foo(): pass\n")
        result = TestCoverageCheck().run(agent_dir)
        assert result.passed

    def test_fails_when_no_tests_dir(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        result = TestCoverageCheck().run(agent_dir)
        assert not result.passed
        assert "No tests/" in result.message

    def test_fails_when_tests_dir_empty(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        tests_dir = agent_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "helper.py").write_text("def helper(): pass\n")
        result = TestCoverageCheck().run(agent_dir)
        assert not result.passed
        assert "no test files" in result.message

    def test_detects_suffix_test_py(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        tests_dir = agent_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "main_test.py").write_text("def test_bar(): pass\n")
        result = TestCoverageCheck().run(agent_dir)
        assert result.passed

    def test_severity_is_warning(self) -> None:
        assert TestCoverageCheck().severity == CheckSeverity.WARNING


# ============================================================================
# QualityGate (integration of all checks)
# ============================================================================


class TestQualityGate:
    def test_perfect_agent_passes(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        # Add tests dir for full score
        tests_dir = agent_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_x(): pass\n")

        result = QualityGate().evaluate(agent_dir)
        assert result.passed
        assert result.verdict == CheckVerdict.PASS
        assert result.score == 1.0
        assert len(result.checks) == 5

    def test_missing_manifest_fails(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "empty"
        agent_dir.mkdir()
        result = QualityGate().evaluate(agent_dir)
        assert not result.passed
        assert result.verdict == CheckVerdict.FAIL

    def test_missing_skill_fails(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "no-skill"
        agent_dir.mkdir()
        _write_toml_manifest(agent_dir)
        result = QualityGate().evaluate(agent_dir)
        assert not result.passed

    def test_security_violation_fails(self, tmp_path: Path) -> None:
        agent_dir = _make_valid_agent(tmp_path)
        _write_python_file(agent_dir, "danger.py", 'eval("x")\n')
        result = QualityGate().evaluate(agent_dir)
        assert not result.passed

    def test_warning_only_reduces_score(self, tmp_path: Path) -> None:
        """Agent with only warning failures (no tests, bad dep name) but all
        critical checks passing should still report a reduced score."""
        agent_dir = _make_valid_agent(tmp_path)
        # Write manifest with invalid dep name (warning)
        _write_toml_manifest(agent_dir, pip_dependencies=["!!bad!!"])
        result = QualityGate().evaluate(agent_dir)
        # DependencyCheck is a warning, so critical checks pass
        # But score is reduced by 0.1 for each warning failure
        assert result.score < 1.0

    def test_custom_floor(self, tmp_path: Path) -> None:
        """With a high floor, a small warning penalty causes FAIL."""
        agent_dir = _make_valid_agent(tmp_path)
        # No tests dir (warning) -> score 0.9
        gate = QualityGate(floor=0.95)
        result = gate.evaluate(agent_dir)
        assert not result.passed
        assert result.score == 0.9

    def test_check_exception_converts_to_critical(self, tmp_path: Path) -> None:
        """A check that raises an exception becomes a CRITICAL failure."""

        class BrokenCheck(BaseCheck):
            name = "broken"

            def run(self, d: Path) -> CheckResult:
                raise RuntimeError("something broke")

        gate = QualityGate(checks=[BrokenCheck()])
        result = gate.evaluate(tmp_path)
        assert not result.passed
        assert any(
            c.check_name == "broken" and c.severity == CheckSeverity.CRITICAL
            for c in result.checks
        )

    def test_custom_checks(self, tmp_path: Path) -> None:
        """QualityGate with a custom check list."""
        agent_dir = _make_valid_agent(tmp_path)
        gate = QualityGate(checks=[ManifestCheck()])
        result = gate.evaluate(agent_dir)
        assert result.passed
        assert len(result.checks) == 1

    def test_empty_agent_dir(self, tmp_path: Path) -> None:
        """Completely empty directory should fail critically."""
        agent_dir = tmp_path / "empty"
        agent_dir.mkdir()
        result = QualityGate().evaluate(agent_dir)
        assert not result.passed
        # Both manifest and skill should fail
        critical_failures = [
            c for c in result.checks
            if not c.passed and c.severity == CheckSeverity.CRITICAL
        ]
        assert len(critical_failures) >= 2

    def test_score_minimum_is_zero(self, tmp_path: Path) -> None:
        """Score cannot go below 0."""
        agent_dir = tmp_path / "no-skill"
        agent_dir.mkdir()
        _write_toml_manifest(agent_dir)
        # Custom gate with 20 warning checks that all fail

        class AlwaysFailWarning(BaseCheck):
            name = "fail"
            severity = CheckSeverity.WARNING

            def run(self, d: Path) -> CheckResult:
                return CheckResult(
                    check_name=self.name, passed=False, severity=self.severity
                )

        gate = QualityGate(checks=[AlwaysFailWarning() for _ in range(20)])
        result = gate.evaluate(agent_dir)
        assert result.score == 0.0


# ============================================================================
# QualityGateResult model
# ============================================================================


class TestQualityGateResult:
    def test_passed_property_true(self) -> None:
        result = QualityGateResult(verdict=CheckVerdict.PASS, score=1.0)
        assert result.passed

    def test_passed_property_false(self) -> None:
        result = QualityGateResult(verdict=CheckVerdict.FAIL, score=0.3)
        assert not result.passed

    def test_checks_default_empty(self) -> None:
        result = QualityGateResult(verdict=CheckVerdict.PASS, score=1.0)
        assert result.checks == []
