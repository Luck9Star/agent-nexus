"""QualityGate — pre-installation quality checks for agent packages.

Runs a configurable pipeline of checks against an agent directory and
produces a scored result that determines whether the agent is safe to
install.

Design spec: docs/roadmap/p1-3-marketplace.md Phase 2-3.
"""

from __future__ import annotations

import ast
import logging
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from .manifest import find_manifest, load_manifest_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CheckSeverity(StrEnum):
    """Severity of a quality check finding."""

    CRITICAL = "critical"
    WARNING = "warning"


class CheckVerdict(StrEnum):
    """Overall quality gate verdict."""

    PASS = "PASS"
    FAIL = "FAIL"


class CheckResult(BaseModel):
    """Result of a single quality check."""

    check_name: str
    passed: bool
    severity: CheckSeverity = CheckSeverity.CRITICAL
    message: str = ""


class QualityGateResult(BaseModel):
    """Aggregated result of the quality gate pipeline."""

    verdict: CheckVerdict
    score: float
    checks: list[CheckResult] = []

    @property
    def passed(self) -> bool:
        return self.verdict == CheckVerdict.PASS


# ---------------------------------------------------------------------------
# Check interface
# ---------------------------------------------------------------------------

# Dangerous call names for AST-level security scanning.
_FORBIDDEN_CALLS = frozenset({"eval", "exec", "subprocess"})


class BaseCheck:
    """Abstract base for a single quality check."""

    name: str = ""
    severity: CheckSeverity = CheckSeverity.CRITICAL

    def run(self, agent_dir: Path) -> CheckResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class ManifestCheck(BaseCheck):
    """Verify agent.toml/agent-manifest.yaml exists with required fields."""

    name = "manifest"
    severity = CheckSeverity.CRITICAL
    _REQUIRED_FIELDS = ("name", "version", "type")

    def run(self, agent_dir: Path) -> CheckResult:
        manifest_path = find_manifest(agent_dir)
        if manifest_path is None:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="No manifest file found (expected agent.toml or agent-manifest.yaml)",
            )

        issues, _raw = load_manifest_dict(agent_dir)
        if issues:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="; ".join(issues),
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            severity=self.severity,
            message="Manifest valid",
        )


class SkillFileCheck(BaseCheck):
    """Verify SKILL.md exists and is non-empty."""

    name = "skill_file"
    severity = CheckSeverity.CRITICAL

    def run(self, agent_dir: Path) -> CheckResult:
        skill_path = agent_dir / "SKILL.md"
        if not skill_path.exists():
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="Missing SKILL.md",
            )

        content = skill_path.read_text(encoding="utf-8").strip()
        if not content:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="SKILL.md is empty",
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            severity=self.severity,
            message="SKILL.md present and non-empty",
        )


class SecurityCheck(BaseCheck):
    """Basic AST-level security scan for dangerous calls.

    Scans Python files in the agent directory for usages of ``eval()``,
    ``exec()``, and ``subprocess``.  This is a heuristic check -- it catches
    direct uses but does not perform data-flow analysis.
    """

    name = "security"
    severity = CheckSeverity.CRITICAL

    def run(self, agent_dir: Path) -> CheckResult:
        findings: list[str] = []

        for py_file in agent_dir.rglob("*.py"):
            try:
                findings.extend(self._scan_file(py_file))
            except SyntaxError:
                # Skip files that cannot be parsed.
                logger.debug("Skipping unparseable file: %s", py_file)

        if findings:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message=f"Dangerous patterns found: {'; '.join(findings)}",
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            severity=self.severity,
            message="No dangerous patterns detected",
        )

    def _scan_file(self, path: Path) -> list[str]:
        """Return a list of finding descriptions for dangerous AST nodes."""
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        findings: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in _FORBIDDEN_CALLS:
                    findings.append(
                        f"{path.name}:{node.lineno} uses {func_name}()"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        findings.append(
                            f"{path.name}:{node.lineno} imports subprocess"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module == "subprocess":
                    findings.append(
                        f"{path.name}:{node.lineno} imports from subprocess"
                    )

        return findings

    @staticmethod
    def _get_call_name(node: ast.Call) -> str | None:
        """Extract the simple name from a Call node (e.g. 'eval' from eval(...))."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None


class DependencyCheck(BaseCheck):
    """Verify pip_dependencies have valid name format."""

    name = "dependency"
    severity = CheckSeverity.WARNING

    # PEP 508: name consists of letters, digits, hyphens, underscores, dots.
    _VALID_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

    def run(self, agent_dir: Path) -> CheckResult:
        manifest_path = find_manifest(agent_dir)
        if manifest_path is None:
            return CheckResult(
                check_name=self.name,
                passed=True,
                severity=self.severity,
                message="No manifest; no dependencies to check",
            )

        _issues, raw = load_manifest_dict(agent_dir)
        deps = raw.get("pip_dependencies", [])
        if not deps:
            return CheckResult(
                check_name=self.name,
                passed=True,
                severity=self.severity,
                message="No pip dependencies declared",
            )

        invalid: list[str] = []
        for dep in deps:
            # Strip version specifiers (e.g. "requests>=2.0" -> "requests")
            name_part = re.split(r"[><=!~;\[]", dep, maxsplit=1)[0].strip()
            if not self._VALID_NAME_RE.match(name_part):
                invalid.append(dep)

        if invalid:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message=f"Invalid dependency names: {', '.join(invalid)}",
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            severity=self.severity,
            message=f"All {len(deps)} dependencies valid",
        )


class TestCoverageCheck(BaseCheck):
    """Check if a tests/ directory exists with at least one test file."""

    name = "test_coverage"
    severity = CheckSeverity.WARNING

    def run(self, agent_dir: Path) -> CheckResult:
        tests_dir = agent_dir / "tests"
        if not tests_dir.is_dir():
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="No tests/ directory found",
            )

        test_files = list(tests_dir.rglob("test_*.py")) + list(
            tests_dir.rglob("*_test.py")
        )
        if not test_files:
            return CheckResult(
                check_name=self.name,
                passed=False,
                severity=self.severity,
                message="tests/ directory exists but contains no test files",
            )

        return CheckResult(
            check_name=self.name,
            passed=True,
            severity=self.severity,
            message=f"Found {len(test_files)} test file(s)",
        )


# ---------------------------------------------------------------------------
# QualityGate
# ---------------------------------------------------------------------------

# Default set of checks in execution order.
_DEFAULT_CHECKS: list[BaseCheck] = [
    ManifestCheck(),
    SkillFileCheck(),
    SecurityCheck(),
    DependencyCheck(),
    TestCoverageCheck(),
]


class QualityGate:
    """Run a configurable pipeline of quality checks against an agent directory.

    Scoring:
    - Each **critical** failure blocks installation (verdict = FAIL regardless of score).
    - Each **warning** failure reduces score by 0.1.
    - Final verdict is PASS if score >= floor AND no critical failures.

    Parameters
    ----------
    floor:
        Minimum score required for a PASS verdict.  Default 0.7.
    checks:
        Custom list of checks.  Defaults to the standard 5-check pipeline.
    """

    def __init__(
        self,
        floor: float = 0.7,
        checks: list[BaseCheck] | None = None,
    ) -> None:
        self._floor = floor
        self._checks = checks if checks is not None else list(_DEFAULT_CHECKS)

    def evaluate(self, agent_dir: Path) -> QualityGateResult:
        """Run all checks and return the aggregated result."""
        results: list[CheckResult] = []
        has_critical_failure = False
        score = 1.0

        for check in self._checks:
            try:
                result = check.run(agent_dir)
            except Exception as exc:
                logger.warning(
                    "Check %s raised %s: %s", check.name, type(exc).__name__, exc,
                )
                result = CheckResult(
                    check_name=check.name or type(check).__name__,
                    passed=False,
                    severity=CheckSeverity.CRITICAL,
                    message=f"Check error: {exc}",
                )
            results.append(result)

            if not result.passed:
                if result.severity == CheckSeverity.CRITICAL:
                    has_critical_failure = True
                elif result.severity == CheckSeverity.WARNING:
                    score -= 0.1

        score = max(score, 0.0)

        if has_critical_failure or score < self._floor:
            verdict = CheckVerdict.FAIL
        else:
            verdict = CheckVerdict.PASS

        return QualityGateResult(
            verdict=verdict,
            score=score,
            checks=results,
        )
