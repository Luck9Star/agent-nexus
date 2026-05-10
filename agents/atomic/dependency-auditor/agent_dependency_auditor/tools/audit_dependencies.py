"""Dependency auditing tool — parse dependency files and check for known CVEs.

Supports:
- requirements.txt (pip freeze / pip install format)
- pyproject.toml [project.dependencies] section
- Direct dict input {package: version}
"""

from __future__ import annotations

import re

from agent_dependency_auditor.models import AuditReport, DependencyVulnerability

# Known vulnerability database (offline snapshot).
# Format: package_name -> list of (affected_below, cve, severity, summary, fixed_in)
_KNOWN_VULNS: dict[str, list[tuple[str, str, str, str, str]]] = {
    "flask": [
        (
            "2.3.0",
            "CVE-2023-30861",
            "medium",
            "Cookie value disclosure via cookie header",
            "2.3.2",
        ),
        (
            "1.0",
            "CVE-2018-1000656",
            "high",
            "Poor exception handling leads to information disclosure",
            "1.0.1",
        ),
    ],
    "django": [
        (
            "4.2.0",
            "CVE-2023-46695",
            "high",
            "Denial of service in django.utils.encoding.uri_to_iri",
            "4.2.7",
        ),
        (
            "3.2.0",
            "CVE-2022-28347",
            "critical",
            "SQL injection in QuerySet.select_for_update()",
            "3.2.13",
        ),
    ],
    "requests": [
        (
            "2.31.0",
            "CVE-2023-32681",
            "medium",
            "Unintended leak of Proxy-Authorization header",
            "2.31.0",
        ),
        (
            "2.19.0",
            "CVE-2018-18074",
            "high",
            "Credential leak in URL via Authorization header",
            "2.20.0",
        ),
    ],
    "pillow": [
        (
            "10.0.0",
            "CVE-2023-44271",
            "high",
            "Uncontrolled resource consumption via font size",
            "10.0.1",
        ),
    ],
    "pyyaml": [
        (
            "6.0",
            "CVE-2020-14343",
            "critical",
            "Arbitrary code execution via unsafe load",
            "5.4.1",
        ),
    ],
    "jinja2": [
        (
            "3.1.3",
            "CVE-2024-22195",
            "medium",
            "HTML attribute injection via xmlattr filter",
            "3.1.3",
        ),
    ],
    "urllib3": [
        (
            "1.26.0",
            "CVE-2023-45803",
            "medium",
            "Request body not stripped after redirect",
            "1.26.18",
        ),
    ],
    "cryptography": [
        (
            "41.0.0",
            "CVE-2023-49083",
            "critical",
            "NULL pointer dereference in PKCS#12",
            "41.0.6",
        ),
    ],
    "sqlalchemy": [
        (
            "1.4.0",
            "CVE-2019-7164",
            "high",
            "SQL injection via order_by",
            "1.3.0",
        ),
    ],
    "numpy": [
        (
            "1.22.0",
            "CVE-2021-41495",
            "medium",
            "Buffer overflow in numpy.core",
            "1.22.0",
        ),
    ],
}


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Args:
        version: Version string like "1.2.3".

    Returns:
        Tuple of integers, e.g. (1, 2, 3).
    """
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def _is_vulnerable(installed_version: str, vulnerable_below: str) -> bool:
    """Check if installed_version is below the vulnerable_below threshold.

    Args:
        installed_version: The currently installed version string.
        vulnerable_below: Versions below this are considered vulnerable.

    Returns:
        True if the installed version is potentially vulnerable.
    """
    installed = _version_tuple(installed_version)
    threshold = _version_tuple(vulnerable_below)
    return installed < threshold


def _parse_requirements_txt(content: str) -> dict[str, str]:
    """Parse requirements.txt content into {package: version}.

    Supports formats:
    - package==1.2.3
    - package>=1.2.3
    - package~=1.2.3
    - Ignoring comments and -e, --index-url lines.

    Args:
        content: Text content of a requirements.txt file.

    Returns:
        Mapping of package names to their declared version strings.
    """
    deps: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Extract version from == operator
        match = re.match(r"^([a-zA-Z0-9_.-]+)==([0-9][0-9.]*)", line)
        if match:
            deps[match.group(1).lower().replace("-", "_")] = match.group(2)
    return deps


def _parse_pyproject_toml(content: str) -> dict[str, str]:
    """Parse pyproject.toml [project.dependencies] into {package: version}.

    Simple regex-based parser for the common case. Extracts package names and
    version constraints from PEP 621 dependency declarations.

    Args:
        content: Text content of a pyproject.toml file.

    Returns:
        Mapping of package names to their version constraint strings.
    """
    deps: dict[str, str] = {}
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[project.dependencies]":
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("["):
                break
            if not stripped or stripped.startswith("#"):
                continue
            # Match: package = ">=1.2.3" or package = ">=1.2.3,<2.0"
            match = re.match(r'^([a-zA-Z0-9_.-]+)\s*=\s*"([^"]*)"', stripped)
            if match:
                pkg = match.group(1).lower().replace("-", "_")
                version_spec = match.group(2)
                # Extract first version number from constraint
                ver_match = re.search(r"([0-9][0-9.]*)", version_spec)
                if ver_match:
                    deps[pkg] = ver_match.group(1)
    return deps


def _check_vulnerabilities(deps: dict[str, str]) -> list[DependencyVulnerability]:
    """Check dependency dict against known vulnerability database.

    Args:
        deps: Mapping of package names to version strings.

    Returns:
        List of DependencyVulnerability instances for known CVEs.
    """
    vulnerabilities: list[DependencyVulnerability] = []

    for package, version in deps.items():
        package_lower = package.lower().replace("-", "_").replace(".", "_")
        known = _KNOWN_VULNS.get(package_lower) or _KNOWN_VULNS.get(package)
        if known is None:
            continue

        for vulnerable_below, cve, severity, summary, fixed_in in known:
            if _is_vulnerable(str(version), vulnerable_below):
                vulnerabilities.append(
                    DependencyVulnerability(
                        package=package,
                        installed_version=str(version),
                        cve=cve,
                        severity=severity,
                        summary=summary,
                        fixed_in=fixed_in,
                    )
                )

    # Deduplicate by (package, cve)
    seen: set[tuple[str, str]] = set()
    unique: list[DependencyVulnerability] = []
    for v in vulnerabilities:
        key = (v.package, v.cve)
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique


def _build_severity_summary(vulns: list[DependencyVulnerability]) -> dict[str, int]:
    """Build severity count summary.

    Args:
        vulns: List of vulnerabilities to summarize.

    Returns:
        Dict with counts per severity level.
    """
    counts: dict[str, int] = {}
    for v in vulns:
        counts[v.severity] = counts.get(v.severity, 0) + 1
    counts["total"] = len(vulns)
    return counts


def audit_dependencies(
    source: str | dict,
    fmt: str = "auto",
) -> AuditReport:
    """Audit dependencies for known vulnerabilities.

    Accepts either a dict {package: version} or a string (requirements.txt /
    pyproject.toml content). Auto-detects the format unless specified.

    Args:
        source: Dependency data as dict or file content string.
        fmt: Format hint — "auto", "requirements", "pyproject", or "dict".

    Returns:
        AuditReport with vulnerabilities, counts, and severity summary.
    """
    if isinstance(source, dict):
        deps = {k.lower().replace("-", "_"): str(v) for k, v in source.items()}
    elif isinstance(source, str):
        if fmt == "auto":
            # Heuristic: pyproject.toml contains [project section markers
            if "[project" in source or "[tool." in source:
                deps = _parse_pyproject_toml(source)
            else:
                deps = _parse_requirements_txt(source)
        elif fmt == "pyproject":
            deps = _parse_pyproject_toml(source)
        else:
            deps = _parse_requirements_txt(source)
    else:
        raise TypeError(f"Expected dict or str, got {type(source).__name__}")

    vulns = _check_vulnerabilities(deps)
    summary = _build_severity_summary(vulns)

    return AuditReport(
        vulnerabilities=vulns,
        total_scanned=len(deps),
        vulnerable_count=len({v.package for v in vulns}),
        summary=summary,
    )
