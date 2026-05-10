"""Local platform: lockfile, source management, git-based installation, supervision, CLI.

Usage::

    from agent_nexus.platform.local import (
        AgentSupervisor,
        GitInstaller,
        LockfileManager,
        SourceManager,
    )

    lockfile_mgr = LockfileManager(config_dir / "lockfile.json")
    source_mgr = SourceManager(config_dir / "sources.yaml")
    installer = GitInstaller(source_mgr, lockfile_mgr, config_dir)
    supervisor = AgentSupervisor(process_manager, lockfile_mgr, config_loader)
"""

from .dependency_resolver import ConflictReport, DependencyResolver, ResolvedDependency
from .installer import AgentNotFoundError, GitInstaller, InstallationError
from .lockfile import LockfileManager
from .quality_gate import (
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
from .sources import SourceManager
from .supervisor import AgentSupervisor, RestartTracker

__all__ = [
    # Core classes
    "AgentSupervisor",
    "DependencyResolver",
    "GitInstaller",
    "LockfileManager",
    "QualityGate",
    "RestartTracker",
    "SourceManager",
    # Quality Gate
    "BaseCheck",
    "CheckResult",
    "CheckSeverity",
    "CheckVerdict",
    "DependencyCheck",
    "ManifestCheck",
    "QualityGateResult",
    "SecurityCheck",
    "SkillFileCheck",
    "TestCoverageCheck",
    # Dependency Resolver
    "ConflictReport",
    "ResolvedDependency",
    # Exceptions
    "AgentNotFoundError",
    "InstallationError",
]
