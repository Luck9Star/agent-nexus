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
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import toml
import yaml

from agent_nexus.models.agent import AgentManifest, AgentType
from agent_nexus.models.distribution import LockfileEntry, SourceEntry
from agent_nexus.models.errors import AgentNexusError
from agent_nexus.platform.utils import AGENT_NAME_RE

from .lockfile import LockfileManager
from .sources import SourceManager

logger = logging.getLogger(__name__)


def _rmtree_best_effort(path: Path, *, context: str = "") -> None:
    """Remove a directory tree, logging individual failures instead of raising.

    Used in cleanup paths (uninstall, rollback) where partial removal is
    acceptable and the caller should not abort.
    """

    def _on_error(func, p, exc_info):  # type: ignore[no-untyped-def]
        logger.warning("Failed to remove %s during %s: %s", p, context, exc_info[1])

    shutil.rmtree(path, onerror=_on_error)


_ALLOWED_GIT_SCHEMES = ("https://", "http://", "git://", "ssh://")


def _validate_git_url(url: str) -> None:
    """Reject URLs with unsupported schemes (e.g. file:///)."""
    if not any(url.startswith(scheme) for scheme in _ALLOWED_GIT_SCHEMES):
        raise ValueError(
            f"Invalid git URL scheme: {url!r}. Allowed schemes: {', '.join(_ALLOWED_GIT_SCHEMES)}"
        )
    if url.startswith("http://"):
        logger.warning(
            "Using plaintext HTTP for git clone — credentials may be exposed: %s",
            url,
        )


class AgentNotFoundError(AgentNexusError):
    """Raised when an agent is not found in any configured source."""


class InstallationError(AgentNexusError):
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

    @staticmethod
    def _validate_agent_name(agent_name: str) -> None:
        """Raise InstallationError if agent_name is invalid."""
        if not AGENT_NAME_RE.match(agent_name):
            raise InstallationError(
                f"Invalid agent name: '{agent_name}'. "
                "Must start with alphanumeric and contain only "
                "alphanumeric, dots, hyphens, and underscores."
            )

    def _resolve_source(
        self,
        agent_name: str,
        source_url: str | None,
    ) -> tuple[SourceEntry, str]:
        """Resolve agent source to a (SourceEntry, relative_path) tuple."""
        if source_url:
            _validate_git_url(source_url)
            source = SourceEntry(
                name=_url_to_source_name(source_url),
                type="git",
                url=source_url,
            )
            return source, f"packages/{agent_name}"

        resolved = self._sources.resolve_agent_source(agent_name)
        if resolved is None:
            raise AgentNotFoundError(
                f"Agent '{agent_name}' not found in any configured source. "
                "Add a source in sources.yaml or use --git-url."
            )
        return resolved

    def _copy_to_agents_dir(self, agent_name: str, source_dir: Path) -> Path:
        """Copy agent files to the managed agents directory."""
        dest = self._agents_dir / agent_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest)
        logger.info("Agent files copied to %s", dest)
        return dest

    @staticmethod
    def _parse_manifest_safe(
        agent_name: str,
        manifest_dict: dict,
    ) -> AgentManifest | None:
        """Parse manifest dict into AgentManifest, raising InstallationError on failure."""
        if not manifest_dict:
            return None
        try:
            return AgentManifest(**manifest_dict)
        except Exception as exc:
            raise InstallationError(
                f"Agent '{agent_name}' has invalid manifest data: {exc}"
            ) from exc

    @staticmethod
    def _build_lockfile_entry(
        manifest: AgentManifest | None,
        *,
        source_name: str,
        commit_sha: str,
        venv_path: Path | None,
        fallback_version: str = "0.0.0",
    ) -> LockfileEntry:
        """Build a LockfileEntry from manifest data and install metadata."""
        agent_type = manifest.type if manifest else AgentType.ATOMIC
        manifest_version = manifest.version if manifest else fallback_version
        return LockfileEntry(
            version=manifest_version,
            source=source_name,
            commit_sha=commit_sha,
            agent_type=agent_type,
            installed_at=datetime.now(UTC),
            venv_path=str(venv_path) if venv_path else "",
            dependencies=manifest.pip_dependencies if manifest else [],
        )

    @staticmethod
    def _rollback_paths(paths: list[Path], agent_name: str, context: str) -> None:
        """Best-effort removal of paths created during a failed install."""
        for path in reversed(paths):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            except Exception:
                logger.debug("Rollback: failed to remove %s", path, exc_info=True)
        logger.warning("%s of '%s' failed; partial files cleaned up.", context, agent_name)

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
        self._validate_agent_name(agent_name)
        source, relative_path = self._resolve_source(agent_name, source_url)
        ref = f"{agent_name}/v{version}" if version else None

        _created_paths: list[Path] = []
        try:
            try:
                agent_dir = await self._sparse_clone(
                    source.url,
                    agent_name,
                    relative_path,
                    ref,
                )
            except Exception as exc:
                raise InstallationError(f"Failed to clone agent '{agent_name}': {exc}") from exc

            issues, manifest_dict = self._validate_agent_package(agent_dir)
            if issues:
                raise InstallationError(
                    f"Agent '{agent_name}' validation failed: {'; '.join(issues)}"
                )

            dest = self._copy_to_agents_dir(agent_name, agent_dir)
            _created_paths.append(dest)
            manifest = self._parse_manifest_safe(agent_name, manifest_dict)

            venv_path = await self._create_venv(agent_name, dest)
            if venv_path:
                _created_paths.append(venv_path)

            cache_path = self._get_cache_path(source.url)
            commit_sha = await self._get_commit_sha(cache_path)

            entry = self._build_lockfile_entry(
                manifest,
                source_name=source.name,
                commit_sha=commit_sha,
                venv_path=venv_path,
                fallback_version=version or "0.0.0",
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
        except Exception:
            self._rollback_paths(_created_paths, agent_name, "Installation")
            raise

    async def uninstall(self, agent_name: str) -> bool:
        """Uninstall an agent.

        Removes agent files, venv, and lockfile entry.  Returns ``True`` if
        the agent was installed (and is now removed).
        """
        self._validate_agent_name(agent_name)

        # Atomically pop the lockfile entry — holds the file lock across the
        # read-remove-write sequence so no concurrent install can race between
        # our get_entry (unlocked) and remove_entry (locked).
        existing = self._lockfile.pop_entry(agent_name)
        if existing is None:
            return False

        # Remove agent files
        agent_dir = self._agents_dir / agent_name
        if agent_dir.exists():
            _rmtree_best_effort(agent_dir, context=f"uninstall {agent_name}")
            logger.info("Removed agent files: %s", agent_dir)

        # Remove venv (from lockfile path or default location)
        if existing.venv_path:
            venv_path = Path(existing.venv_path).resolve()
            allowed_prefix = self._venvs_dir.resolve()
            if not venv_path.is_relative_to(allowed_prefix):
                logger.error(
                    "Refusing to remove venv_path outside allowed directory: %s",
                    venv_path,
                )
            elif venv_path.exists():
                _rmtree_best_effort(venv_path, context=f"uninstall venv {agent_name}")
                logger.info("Removed venv: %s", venv_path)
        else:
            default_venv = self._venvs_dir / agent_name
            if default_venv.exists():
                _rmtree_best_effort(default_venv, context=f"uninstall default venv {agent_name}")
                logger.info("Removed venv: %s", default_venv)

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

    async def install_local(
        self,
        agent_name: str,
        local_path: Path,
    ) -> LockfileEntry:
        """Install an agent from a local directory (dev mode).

        Parameters
        ----------
        agent_name:
            Name of the agent to install.
        local_path:
            Absolute path to the agent package directory.

        Returns
        -------
        LockfileEntry
            Entry recorded in the lockfile for the installed agent.

        Raises
        ------
        InstallationError
            Validation or venv creation failed.
        """
        self._validate_agent_name(agent_name)

        if not local_path.is_dir():
            raise InstallationError(f"Local agent path does not exist: {local_path}")

        issues, manifest_dict = self._validate_agent_package(local_path)
        if issues:
            raise InstallationError(f"Agent '{agent_name}' validation failed: {'; '.join(issues)}")

        _created_paths: list[Path] = []
        try:
            dest = self._copy_to_agents_dir(agent_name, local_path)
            _created_paths.append(dest)

            manifest = self._parse_manifest_safe(agent_name, manifest_dict)

            venv_path = await self._create_venv(agent_name, dest)
            if venv_path:
                _created_paths.append(venv_path)

            commit_sha = await self._get_local_commit_sha(local_path)

            entry = self._build_lockfile_entry(
                manifest,
                source_name="local",
                commit_sha=commit_sha,
                venv_path=venv_path,
            )
            self._lockfile.add_entry_by_name(agent_name, entry)

            logger.info(
                "Local agent installed: %s@%s (source=local)",
                agent_name,
                entry.version,
            )
            return entry
        except Exception:
            self._rollback_paths(_created_paths, agent_name, "Local install")
            raise

    async def _get_local_commit_sha(self, agent_path: Path) -> str:
        """Get git HEAD SHA from the project repo containing agent_path.

        Falls back to 'latest' if not in a git repo.
        """
        try:
            result = await self._run_git_capture(
                ["rev-parse", "HEAD"],
                cwd=agent_path,
            )
            sha = result.strip()
            if len(sha) >= 40:
                return sha[:40]
        except Exception:
            logger.debug("Could not get git SHA for local path %s", agent_path)
        return "latest"

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
        # Validate relative_path to prevent path traversal
        rel = Path(relative_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise InstallationError(
                f"Relative path '{relative_path}' contains path traversal or is absolute"
            )

        cache_path = self._get_cache_path(source_url)

        git_dir = cache_path / ".git"
        if not git_dir.exists():
            # Only create parent directory; git clone creates the target itself
            cache_path.parent.mkdir(parents=True, exist_ok=True)

        if not git_dir.exists():
            # Initial clone: no checkout + blobless partial clone + sparse
            await self._run_git(
                [
                    "clone",
                    "--no-checkout",
                    "--depth=1",
                    "--filter=blob:none",
                    "--sparse",
                    source_url,
                    str(cache_path),
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
        await self._run_git(["checkout", "--", checkout_ref], cwd=cache_path)

        agent_dir = cache_path / relative_path
        if not agent_dir.exists():
            # The relative_path might not exist -- try the agent name directly
            # (for repos where the agent is at the root, not under packages/)
            alt_dir = cache_path / agent_name
            if alt_dir.exists():
                return alt_dir
            raise InstallationError(f"Agent directory '{relative_path}' not found in repository")

        return agent_dir

    async def _get_commit_sha(self, repo_path: Path) -> str:
        """Get the current HEAD commit SHA from a local repo."""
        try:
            result = await self._run_git_capture(
                ["rev-parse", "HEAD"],
                cwd=repo_path,
            )
            return result.strip()
        except Exception as exc:
            raise InstallationError(
                f"Could not determine commit SHA for {repo_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal: validation
    # ------------------------------------------------------------------

    def _validate_agent_package(self, agent_dir: Path) -> tuple[list[str], dict]:
        """Validate an agent package.

        Returns a tuple of (issues list, parsed manifest dict).
        An empty issues list means valid.  The manifest dict may be
        empty if the file is missing or unparseable.

        Checks:
        1. ``agent-manifest.yaml`` exists and is parseable
        2. ``SKILL.md`` exists
        3. Basic manifest structure has required fields
        """
        issues: list[str] = []
        manifest_data: dict = {}

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
                    manifest_data = manifest
                    for field in ("name", "version", "type"):
                        if field not in manifest:
                            issues.append(f"agent-manifest.yaml missing required field: {field}")
                    if "type" in manifest and manifest["type"] not in {t.value for t in AgentType}:
                        issues.append(f"Invalid agent type: {manifest['type']}")
            except yaml.YAMLError as exc:
                issues.append(f"agent-manifest.yaml parse error: {exc}")

        # 2. SKILL.md
        if not (agent_dir / "SKILL.md").exists():
            issues.append("Missing SKILL.md")

        return issues, manifest_data

    def _read_manifest(self, agent_dir: Path) -> dict:
        """Read and return the agent manifest as a raw dict."""
        manifest_path = agent_dir / "agent-manifest.yaml"
        if not manifest_path.exists():
            return {}
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            raise InstallationError(f"Failed to read manifest from {manifest_path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal: venv management
    # ------------------------------------------------------------------

    @staticmethod
    def _has_extra(agent_dir: Path, extra_name: str) -> bool:
        """Check if the agent's pyproject.toml defines the given optional extra."""
        pyproject = agent_dir / "pyproject.toml"
        if not pyproject.exists():
            return False
        try:
            raw = toml.loads(pyproject.read_text(encoding="utf-8"))
            return extra_name in raw.get("project", {}).get("optional-dependencies", {})
        except Exception:
            return False

    def _validate_venv_path(self, venv_path: Path) -> bool:
        """Check if venv path is safe (no symlink escape). Removes existing venv if safe."""
        if not venv_path.exists():
            return True
        resolved = venv_path.resolve()
        if not resolved.is_relative_to(self._venvs_dir.resolve()):
            logger.warning(
                "Skipping removal of venv_path outside allowed directory: %s",
                resolved,
            )
            return False
        shutil.rmtree(venv_path)
        return True

    @staticmethod
    async def _run_uv(args: list[str], error_label: str) -> bytes | None:
        """Run a uv subprocess with cleanup. Returns stderr on success, ``None`` on failure."""
        proc = await asyncio.create_subprocess_exec(
            "uv",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await proc.communicate()
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            logger.warning("uv %s failed: %s", error_label, stderr.decode(errors="replace"))
            return None
        return stderr

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

        if not self._validate_venv_path(venv_path):
            return None

        try:
            stderr = await self._run_uv(["venv", str(venv_path)], f"venv for {agent_name}")
            if stderr is None:
                shutil.rmtree(venv_path, ignore_errors=True)
                return None

            install_target = (
                f"{agent_dir}[full]" if self._has_extra(agent_dir, "full") else str(agent_dir)
            )
            stderr = await self._run_uv(
                [
                    "pip",
                    "install",
                    install_target,
                    "--python",
                    str(venv_path / "bin" / "python"),
                ],
                f"pip install for {agent_name}",
            )
            if stderr is None:
                shutil.rmtree(venv_path, ignore_errors=True)
                return None

            logger.info("Venv created: %s", venv_path)
            return venv_path

        except FileNotFoundError:
            shutil.rmtree(venv_path, ignore_errors=True)
            logger.warning(
                "uv not found -- skipping venv creation for %s. "
                "Install uv for automatic venv management.",
                agent_name,
            )
            return None
        except Exception:
            shutil.rmtree(venv_path, ignore_errors=True)
            logger.exception("Unexpected error creating venv for %s", agent_name)
            return None

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _get_cache_path(self, source_url: str) -> Path:
        """Derive a stable local cache directory from *source_url*."""
        from agent_nexus.platform.utils import cache_path_for_url

        return cache_path_for_url(self._config_dir, source_url)

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
        try:
            _, stderr = await proc.communicate()
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            raise InstallationError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): {err_msg}"
            )

    @staticmethod
    async def _run_git_capture(args: list[str], cwd: Path) -> str:
        """Run a git command and return its stdout."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            raise InstallationError(
                f"git {' '.join(args)} failed (rc={proc.returncode}): {err_msg}"
            )
        return stdout.decode(errors="replace")


def _url_to_source_name(url: str) -> str:
    """Derive a short source name from a git URL.

    Extracts the last path component and strips any ``.git`` suffix.
    """
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "direct"
