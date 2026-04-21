"""LockfileManager: read and write lockfile.json atomically.

The lockfile is the source of truth for all installed agents. It records
exact versions, source repos, commit SHAs, and venv paths so that any
installation can be reproduced or rolled back.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from agent_nexus.models.distribution import Lockfile, LockfileEntry

logger = logging.getLogger(__name__)


class LockfileManager:
    """Read and write ``lockfile.json``.

    Parameters
    ----------
    lockfile_path:
        Absolute path to the lockfile (typically ``~/.agent-nexus/lockfile.json``).
    """

    def __init__(self, lockfile_path: Path) -> None:
        self._path = lockfile_path
        self._corrupt_detected = False
        # mtime-based cache to avoid re-reading unchanged lockfile
        self._cache: Lockfile | None = None
        self._cache_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Lockfile:
        """Load lockfile from disk.

        Returns an empty :class:`Lockfile` when the file does not exist or
        cannot be parsed.

        Results are cached based on the file's mtime — repeated calls
        return the same object until the file is modified.
        """
        # mtime-based cache: skip disk I/O when file has not changed
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = 0.0

        if self._cache is not None and mtime == self._cache_mtime:
            logger.debug("Returning cached lockfile (mtime unchanged)")
            return self._cache

        if not self._path.exists():
            logger.debug("Lockfile not found at %s, returning empty", self._path)
            result = Lockfile()
            self._cache = result
            self._cache_mtime = mtime
            return result

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            result = Lockfile.model_validate(raw)
            logger.debug("Loaded lockfile with %d agent(s)", len(result.agents))
            self._corrupt_detected = False
            self._cache = result
            self._cache_mtime = mtime
            return result
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.error(
                "Corrupt lockfile %s — returning empty agents list: %s",
                self._path, exc,
            )
            self._corrupt_detected = True
            result = Lockfile()
            self._cache = result
            self._cache_mtime = mtime
            return result

    @contextmanager
    def _file_lock(self) -> Generator[None, None, None]:
        """Acquire an exclusive file lock for cross-process serialization.

        Uses a separate ``.lock`` sibling file so that the lockfile
        itself can still be read freely.  The lock is held for the
        duration of the read-modify-write sequence.
        """
        lock_path = self._path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
        except BaseException:
            fh.close()
            raise
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def _save(self, lockfile: Lockfile) -> None:
        """Persist *lockfile* to disk atomically.

        Writes to a temporary file first, then renames (``os.rename``) so
        that a crash mid-write never leaves a corrupted lockfile.

        If a corrupt lockfile was previously detected by :meth:`load`, the
        corrupt file is backed up before being overwritten to prevent
        permanent data loss.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Backup corrupt lockfile before overwriting with empty data
        if self._corrupt_detected and self._path.exists():
            backup = self._path.with_suffix(".json.corrupt")
            try:
                os.replace(str(self._path), str(backup))
                logger.warning(
                    "Backed up corrupt lockfile to %s before overwrite",
                    backup,
                )
            except OSError as exc:
                logger.error(
                    "Failed to back up corrupt lockfile to %s: %s",
                    backup, exc,
                )
            self._corrupt_detected = False

        # Invalidate cache — _save changes the file on disk
        self._cache = None

        payload = lockfile.model_dump(mode="json")
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".lockfile-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(self._path))
            logger.debug("Lockfile saved atomically to %s", self._path)
        except BaseException:
            # Clean up the temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_entry(self, agent_name: str) -> LockfileEntry | None:
        """Return the lockfile entry for *agent_name*, or ``None``.

        Delegates to :meth:`load` which uses mtime-based caching.
        For bulk operations prefer :meth:`get_entry_from` to avoid
        redundant I/O and TOCTOU races.
        """
        return self.load().agents.get(agent_name)

    def get_entry_from(
        self, lockfile: Lockfile, agent_name: str,
    ) -> LockfileEntry | None:
        """Return the lockfile entry from an already-loaded lockfile.

        Unlike :meth:`get_entry`, this does **not** read from disk.
        Use when the caller has already called :meth:`load` to avoid
        redundant I/O and TOCTOU inconsistencies.
        """
        return lockfile.agents.get(agent_name)

    def add_entry_by_name(self, agent_name: str, entry: LockfileEntry) -> None:
        """Add or update a lockfile entry keyed by *agent_name* and save.

        Uses file-level locking (``fcntl.flock``) to serialize the
        read-modify-write sequence across concurrent CLI processes.
        """
        with self._file_lock():
            lockfile = self.load()
            agents = dict(lockfile.agents)
            agents[agent_name] = entry
            updated = Lockfile(version=lockfile.version, agents=agents)
            self._save(updated)
        logger.info("Lockfile updated: %s@%s", agent_name, entry.version)

    def remove_entry(self, agent_name: str) -> bool:
        """Remove a lockfile entry.

        Uses file-level locking to prevent TOCTOU races with concurrent
        processes.

        Returns ``True`` if the entry existed (and was removed), ``False``
        otherwise.
        """
        with self._file_lock():
            lockfile = self.load()
            if agent_name not in lockfile.agents:
                return False

            agents = dict(lockfile.agents)
            del agents[agent_name]
            updated = Lockfile(version=lockfile.version, agents=agents)
            self._save(updated)
        logger.info("Lockfile entry removed: %s", agent_name)
        return True

    def pop_entry(self, agent_name: str) -> LockfileEntry | None:
        """Atomically remove and return a lockfile entry.

        Unlike calling :meth:`get_entry` then :meth:`remove_entry` separately
        (which has a TOCTOU gap between the unlocked read and the locked
        remove), this method holds the file lock across the entire
        read-remove-write sequence.

        Returns the removed entry, or ``None`` if it did not exist.
        """
        with self._file_lock():
            lockfile = self.load()
            entry = lockfile.agents.get(agent_name)
            if entry is None:
                return None

            agents = dict(lockfile.agents)
            del agents[agent_name]
            updated = Lockfile(version=lockfile.version, agents=agents)
            self._save(updated)
        logger.info("Lockfile entry removed (atomic): %s", agent_name)
        return entry

    def list_entries(self) -> list[LockfileEntry]:
        """Return all lockfile entries in insertion order."""
        return list(self.load().agents.values())
