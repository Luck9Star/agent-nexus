"""GitInstaller: install agents from git repositories.

Installation process:
1. Resolve agent source (which repo + path)
2. Sparse clone the agent directory (``git sparse-checkout``)
3. Validate agent package (``agent-manifest.yaml``, ``SKILL.md``)
4. Create per-agent venv with ``uv`` (if agent has ``pyproject.toml``)
5. Update lockfile with version + commit SHA

Version management uses git tags in the format ``{agent-name}/v{semver}``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_nexus.models.agent import AgentManifest, AgentType
from agent_nexus.models.distribution import LockfileEntry, SourceEntry

from .lockfile import LockfileManager
from .sources import SourceManager

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """Raised when an agent is not found in any configured source."""


class InstallationError(Exception):
    """Raised when clone, validation, or venv creation fails."""


class GitInstaller:
    """Install agents from git repositories.

    Parameters
    ----------
    source_manager:
        Resolves agent names to git sources.
    lockfile_manager:
        Reads and writes the lockfile.
    config_dir:
        Platform config directory (typically ``~/.agent-nexus/``).
    """

    def __init__(
        self,
        source_manager: SourceManager,
        lockfile_manager: LockfileManager,
        config_dir: Path,
    ) -> None:
        self._sources = source_manager
        self._lockfile = lockfile_manager
        self._config_dir = config_dir
        self._agents_dir = config_dir / "agents"
        self._venvs_dir = config_dir / "venvs"
        self._cache_dir = config_dir / "cache" / "repos"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def install(
        self,
        agent_name: str,
        version: str | None = None,
        source_url: str | None = None,
    ) -> LockfileEntry:
        """Install an agent from a git source.

        Parameters
        ----------
        agent_name:
            Name of the agent to install.
        version:
            Specific version tag (e.g. ``"1.2.0"``).  ``None`` means latest.
        source_url:
            Direct git URL -- overrides source resolution.

        Returns
        -------
        LockfileEntry
            Entry recorded in the lockfile for the installed agent.

        Raises
        ------
        AgentNotFoundError
            Agent not found in any source.
        InstallationError
            Clone, validation, or venv creation failed.
        """
        # 1. Resolve source
        if source_url:
            source = SourceEntry(
                name=_url_to_source_name(source_url),
                type="git",
                url=source_url,
            )
            relative_path = f"packages/{agent_name}"
        else:
            resolved = self._sources.resolve_agent_source(agent_name)
            if resolved is None:
                raise AgentNotFoundError(
                    f"Agent '{agent_name}' not found in any configured source. "
                    "Add a source in sources.yaml or use --git-url."
                )
            source, relative_path = resolved

        # 2. Determine git ref (tag format: agent-name/v1.2.0)
        ref = f"{agent_name}/v{version}" if version else None

        # 3. Sparse clone
        try:
            agent_dir = await self._sparse_clone(
                source.url, agent_name, relative_path, ref,
            )
        except Exception as exc:
            raise InstallationError(
                f"Failed to clone agent '{agent_name}': {exc}"
            ) from exc

        # 4. Validate
        issues = self._validate_agent_package(agent_dir)
        if issues:
            raise InstallationError(
                f"Agent '{agent_name}' validation failed: {'; '.join(issues)}"
            )

        # 5. Copy to agents dir
        dest = self._agents_dir / agent_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(agent_dir, dest)
        logger.info("Agent files copied to %s", dest)

        # 6. Read manifest for metadata
        manifest_dict = self._read_manifest(dest)
        manifest = AgentManifest(**manifest_dict) if manifest_dict else None
        agent_type = manifest.type if manifest else AgentType.ATOMIC
        manifest_version = manifest.version if manifest else (version or "0.0.0")

        # 7. Create venv if needed
        venv_path = await self._create_venv(agent_name, dest)

        # 8. Get commit SHA
        cache_path = self._get_cache_path(source.url)
        commit_sha = await self._get_commit_sha(cache_path)

        # 9. Update lockfile
        entry = LockfileEntry(
            version=manifest_version,
            source=source.name,
            commit_sha=commit_sha,
            agent_type=agent_type,
            installed_at=datetime.now(timezone.utc),
            venv_path=str(venv_path) if venv_path else "",
            dependencies=manifest.pip_dependencies if manifest else [],
        )
        self._lockfile.add_entry_by_name(agent_name, entry)

        logger.info(
            "Agent installed: %s@%s (sha=%s, venv=%s)",
            agent_name,
            entry.version,
            commit_sha[:12],
            "yes" if venv_path else "no",
        )
        return entry

    async def uninstall(self, agent_name: str) -> bool:
        """Uninstall an agent.

        Removes agent files, venv, and lockfile entry.  Returns ``True`` if
        the agent was installed (and is now removed).
        """
        existing = self._lockfile.get_entry(agent_name)
        if existing is None:
            return False

        # Remove agent files
        agent_dir = self._agents_dir / agent_name
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
            logger.info("Removed agent files: %s", agent_dir)

        # Remove venv (from lockfile path or default location)
        if existing.venv_path:
            venv_path = Path(existing.venv_path)
            if venv_path.exists():
                shutil.rmtree(venv_path)
                logger.info("Removed venv: %s", venv_path)
        else:
            default_venv = self._venvs_dir / agent_name
            if default_venv.exists():
                shutil.rmtree(default_venv)
                logger.info("Removed venv: %s", default_venv)

        # Remove lockfile entry
        self._lockfile.remove_entry(agent_name)
        logger.info("Agent uninstalled: %s", agent_name)
        return True

    async def update(self, agent_name: str) -> LockfileEntry | None:
        """Update an agent to the latest version.

        Returns the new :class:`LockfileEntry`, or ``None`` if already at
        the latest version.
        """
        existing = self._lockfile.get_entry(agent_name)
        if existing is None:
            raise AgentNotFoundError(f"Agent '{agent_name}' is not installed.")

        # Re-install with latest version
        return await self.install(agent_name, version=None, source_url=None)

    def get_installed_version(self, agent_name: str) -> str | None:
        """Return the currently installed version from the lockfile."""
        entry = self._lockfile.get_entry(agent_name)
        return entry.version if entry else None

    # ------------------------------------------------------------------
    # Internal: git operations
    # ------------------------------------------------------------------

    async def _sparse_clone(
        self,
        source_url: str,
        agent_name: str,
        relative_path: str,
        ref: str | None,
    ) -> Path:
        """Sparse clone just the agent directory from the repo.

        Uses ``git sparse-checkout`` to avoid cloning the entire repo.
        Returns the path to the cloned agent directory inside the cache.
        """
        cache_path = self._get_cache_path(source_url)

        git_dir = cache_path / ".git"
        if not git_dir.exists():
            # Only create parent directory; git clone creates the target itself
            cache_path.parent.mkdir(parents=True, exist_ok=True)

        git_dir = cache_path / ".git"
        if not git_dir.exists():
            # Initial clone: no checkout + blobless partial clone + sparse
            await self._run_git(
                [
                    "clone", "--no-checkout", "--depth=1",
                    "--filter=blob:none", "--sparse",
                    source_url, str(cache_path),
                ],
                cwd=cache_path.parent,
            )
        else:
            # Fetch latest refs and tags
            await self._run_git(["fetch", "--tags"], cwd=cache_path)

        # Configure sparse checkout to only include the agent directory
        await self._run_git(
            ["sparse-checkout", "set", relative_path],
            cwd=cache_path,
        )

        # Checkout the desired ref (or HEAD for latest)
        checkout_ref = ref if ref else "HEAD"
        await self._run_git(["checkout", checkout_ref], cwd=cache_path)

        agent_dir = cache_path / relative_path
        if not agent_dir.exists():
            # The relative_path might not exist -- try the agent name directly
            # (for repos where the agent is at the root, not under packages/)
            alt_dir = cache_path / agent_name
            if alt_dir.exists():
                return alt_dir
            raise InstallationError(
                f"Agent directory '{relative_path}' not found in repository"
            )

        return agent_dir

    async def _get_commit_sha(self, repo_path: Path) -> str:
        """Get the current HEAD commit SHA from a local repo."""
        try:
            result = await self._run_git_capture(
                ["rev-parse", "HEAD"],
                cwd=repo_path,
            )
            return result.strip()
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # Internal: validation
    # ------------------------------------------------------------------

    def _validate_agent_package(self, agent_dir: Path) -> list[str]:
        """Validate an agent package.

        Returns a list of issues (empty means valid).

        Checks:
        1. ``agent-manifest.yaml`` exists and is parseable
        2. ``SKILL.md`` exists
        3. Basic manifest structure has required fields
        """
        issues: list[str] = []

        # 1. agent-manifest.yaml
        manifest_path = agent_dir / "agent-manifest.yaml"
        if not manifest_path.exists():
            issues.append("Missing agent-manifest.yaml")
        else:
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    issues.append("agent-manifest.yaml is not a valid mapping")
                else:
                    for field in ("name", "version", "type"):
                        if field not in manifest:
                            issues.append(
                                f"agent-manifest.yaml missing required field: {field}"
                            )
                    if "type" in manifest and manifest["type"] not in ("atomic", "composite"):
                        issues.append(f"Invalid agent type: {manifest['type']}")
            except yaml.YAMLError as exc:
                issues.append(f"agent-manifest.yaml parse error: {exc}")

        # 2. SKILL.md
        if not (agent_dir / "SKILL.md").exists():
            issues.append("Missing SKILL.md")

        return issues

    def _read_manifest(self, agent_dir: Path) -> dict:
        """Read and return the agent manifest as a raw dict."""
        manifest_path = agent_dir / "agent-manifest.yaml"
        if not manifest_path.exists():
            return {}
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Internal: venv management
    # ------------------------------------------------------------------

    async def _create_venv(self, agent_name: str, agent_dir: Path) -> Path | None:
        """Create a per-agent venv if the agent has ``pyproject.toml``.

        Uses ``uv`` for fast venv creation.  Returns the venv path, or
        ``None`` if no venv was needed (or creation failed).
        """
        pyproject = agent_dir / "pyproject.toml"
        if not pyproject.exists():
            logger.debug("No pyproject.toml for %s, skipping venv", agent_name)
            return None

        venv_path = self._venvs_dir / agent_name
        self._venvs_dir.mkdir(parents=True, exist_ok=True)

        # Remove existing venv if present
        if venv_path.exists():
            shutil.rmtree(venv_path)

        try:
            # Create venv with uv
            proc = await asyncio.create_subprocess_exec(
                "uv", "venv", str(venv_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning("uv venv failed for %s: %s", agent_name, stderr.decode())
                return None

            # Install the agent package into the venv
            proc = await asyncio.create_subprocess_exec(
                "uv", "pip", "install", "-e", str(agent_dir),
                "--python", str(venv_path / "bin" / "python"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning("uv pip install failed for %s: %s", agent_name, stderr.decode())

            logger.info("Venv created: %s", venv_path)
            return venv_path

        except FileNotFoundError:
            logger.warning(
                "uv not found -- skipping venv creation for %s. "
                "Install uv for automatic venv management.",
                agent_name,
            )
            return None

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _get_cache_path(self, source_url: str) -> Path:
        """Derive a stable local cache directory from *source_url*."""
        digest = hashlib.sha256(source_url.encode()).hexdigest()[:12]
        return self._cache_dir / digest

    @staticmethod
    async def _run_git(args: list[str], cwd: Path) -> None:
        """Run a git command and raise on failure.

        Uses ``asyncio.create_subprocess_exec`` (not shell) to avoid
        injection risks.
        """
        cmd = ["git", *args]
        logger.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise InstallationError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): "
                f"{stderr.decode().strip()}"
            )

    @staticmethod
    async def _run_git_capture(args: list[str], cwd: Path) -> str:
        """Run a git command and return its stdout."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise InstallationError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): "
                f"{stderr.decode().strip()}"
            )
        return stdout.decode()


def _url_to_source_name(url: str) -> str:
    """Derive a short source name from a git URL.

    Extracts the last path component and strips any ``.git`` suffix.
    """
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "direct"
