"""LockfileManager: read and write lockfile.json atomically.

The lockfile is the source of truth for all installed agents. It records
exact versions, source repos, commit SHAs, and venv paths so that any
installation can be reproduced or rolled back.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Lockfile:
        """Load lockfile from disk.

        Returns an empty :class:`Lockfile` when the file does not exist or
        cannot be parsed.
        """
        if not self._path.exists():
            logger.debug("Lockfile not found at %s, returning empty", self._path)
            return Lockfile()

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            lockfile = Lockfile.model_validate(raw)
            logger.debug("Loaded lockfile with %d agent(s)", len(lockfile.agents))
            return lockfile
        except Exception as exc:
            logger.warning("Failed to parse lockfile %s: %s", self._path, exc)
            return Lockfile()

    def save(self, lockfile: Lockfile) -> None:
        """Persist *lockfile* to disk atomically.

        Writes to a temporary file first, then renames (``os.rename``) so
        that a crash mid-write never leaves a corrupted lockfile.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

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
        """Return the lockfile entry for *agent_name*, or ``None``."""
        return self.load().agents.get(agent_name)

    def add_entry(self, entry: LockfileEntry) -> None:
        """Add or replace a lockfile entry and save immediately."""
        lockfile = self.load()
        # Pydantic frozen model: rebuild dict with new entry
        agents = dict(lockfile.agents)
        agents[entry.source] = agents.pop(entry.source, None)  # no-op helper
        # We key by agent name — but LockfileEntry doesn't carry the name
        # field directly. The caller is responsible for passing the right key.
        # Since Lockfile.agents is dict[str, LockfileEntry], we need the name.
        # We derive it from the Lockfile structure: the key *is* the name.
        # However, LockfileEntry doesn't include agent_name. The caller must
        # know the name. We accept it via the *source* field convention — no,
        # source is the repo name. We need the caller to tell us the name.
        #
        # Strategy: we mutate the agents dict and reconstruct. The caller
        # should use add_entry_by_name() for clarity, but we keep this
        # signature compatible by storing under the first key that matches
        # or requiring explicit name.
        #
        # Actually, looking at the Lockfile schema: agents is dict[str, LockfileEntry]
        # where the key IS the agent name. LockfileEntry doesn't have a name field.
        # So add_entry needs the agent name. We'll add a separate method.
        raise NotImplementedError("Use add_entry_by_name(agent_name, entry) instead")

    def add_entry_by_name(self, agent_name: str, entry: LockfileEntry) -> None:
        """Add or update a lockfile entry keyed by *agent_name* and save."""
        lockfile = self.load()
        agents = dict(lockfile.agents)
        agents[agent_name] = entry
        updated = Lockfile(version=lockfile.version, agents=agents)
        self.save(updated)
        logger.info("Lockfile updated: %s@%s", agent_name, entry.version)

    def remove_entry(self, agent_name: str) -> bool:
        """Remove a lockfile entry.

        Returns ``True`` if the entry existed (and was removed), ``False``
        otherwise.
        """
        lockfile = self.load()
        if agent_name not in lockfile.agents:
            return False

        agents = dict(lockfile.agents)
        del agents[agent_name]
        updated = Lockfile(version=lockfile.version, agents=agents)
        self.save(updated)
        logger.info("Lockfile entry removed: %s", agent_name)
        return True

    def list_entries(self) -> list[LockfileEntry]:
        """Return all lockfile entries in insertion order."""
        return list(self.load().agents.values())
