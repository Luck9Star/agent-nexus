"""Dependency checking tool — check project dependencies for known CVEs.

Provides an offline database of known vulnerabilities for common Python packages.
Compares declared versions against known vulnerable version ranges.
"""

from __future__ import annotations

from agent_security_scanner.models import DependencyReport, DependencyVulnerability

# Known vulnerability database (offline snapshot).
# Format: package_name -> list of (affected_versions_below, cve, severity)
_KNOWN_VULNS: dict[str, list[tuple[str, str, str]]] = {
    "flask": [
        ("2.2.0", "CVE-2023-30861", "medium"),
        ("1.0", "CVE-2018-1000656", "high"),
    ],
    "django": [
        ("4.2.0", "CVE-2023-46695", "high"),
        ("3.2.0", "CVE-2022-28347", "critical"),
        ("3.0.0", "CVE-2021-33203", "high"),
    ],
    "requests": [
        ("2.25.0", "CVE-2023-32681", "medium"),
        ("2.19.0", "CVE-2018-18074", "high"),
    ],
    "pillow": [
        ("9.0.0", "CVE-2023-44271", "high"),
        ("8.3.0", "CVE-2022-22817", "critical"),
    ],
    "pyyaml": [
        ("5.4", "CVE-2020-14343", "critical"),
    ],
    "jinja2": [
        ("3.1.3", "CVE-2024-22195", "medium"),
    ],
    "urllib3": [
        ("1.26.0", "CVE-2023-45803", "medium"),
        ("1.25.0", "CVE-2020-26137", "medium"),
    ],
    "cryptography": [
        ("41.0.0", "CVE-2023-49083", "critical"),
    ],
    "sqlalchemy": [
        ("1.4.0", "CVE-2019-7164", "high"),
        ("1.2.0", "CVE-2019-7548", "high"),
    ],
    "numpy": [
        ("1.22.0", "CVE-2021-41495", "medium"),
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


def check_dependencies(deps: dict) -> DependencyReport:
    """Check project dependencies for known vulnerabilities.

    Compares declared dependency versions against a built-in CVE database.
    Returns a structured report listing any known vulnerabilities found.

    Args:
        deps: Mapping of package names to version strings,
            e.g. {"flask": "2.0.1", "requests": "2.25.0"}.

    Returns:
        DependencyReport with all discovered vulnerabilities and counts.
    """
    vulnerabilities: list[DependencyVulnerability] = []

    for package, version in deps.items():
        package_lower = package.lower().replace("-", "_").replace(".", "_")
        # Try both normalized and original names
        known = _KNOWN_VULNS.get(package_lower) or _KNOWN_VULNS.get(package)
        if known is None:
            continue

        for vulnerable_below, cve, severity in known:
            if _is_vulnerable(str(version), vulnerable_below):
                vulnerabilities.append(
                    DependencyVulnerability(
                        package=package,
                        version=str(version),
                        cve=cve,
                        severity=severity,
                    )
                )

    # Deduplicate by (package, cve)
    seen: set[tuple[str, str]] = set()
    unique_vulns: list[DependencyVulnerability] = []
    for v in vulnerabilities:
        key = (v.package, v.cve)
        if key not in seen:
            seen.add(key)
            unique_vulns.append(v)

    return DependencyReport(
        vulnerabilities=unique_vulns,
        total_scanned=len(deps),
        vulnerable_count=len({v.package for v in unique_vulns}),
    )
