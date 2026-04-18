"""Tests for iteration 13 bug fixes.

Six bugs fixed:
1. Cache path mismatch between SourceManager and GitInstaller
2. Pipe deadlock in GitInstaller._create_venv
3. IPC receive_until_result discards mismatched messages
4. FunctionRule security bypass for method-call patterns
5. INSERT OR REPLACE overwrites counters in EvolutionStore
6. CompactionGuard.should_alert uses default ContextBudget
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.context import ContextBudget, TokenUsage
from agent_nexus.models.evolution import (
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
)
from agent_nexus.platform.evolution.compaction import (
    AgentContext,
    CompactionGuard,
)
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.orchestration.ipc import (
    IPCProtocol,
    IPCStream,
)
from agent_nexus.platform.runtime.security_rules import FunctionRule
from agent_nexus.platform.local.sources import SourceManager


# ============================================================================
# Fix 1: Cache path alignment between SourceManager and GitInstaller
# ============================================================================


class TestCachePathAlignment:
    """Verify SourceManager._get_cache_path matches GitInstaller._get_cache_path."""

    def test_cache_path_uses_sha256_hash(self, tmp_path: Path) -> None:
        """SourceManager._get_cache_path uses SHA-256 hash, not source.name."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = __import__("agent_nexus.models.distribution", fromlist=["SourceEntry"]).SourceEntry(
            name="official",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        cache_path = sm._get_cache_path(source)

        expected_hash = hashlib.sha256(source.url.encode()).hexdigest()[:12]
        expected_path = tmp_path / "cache" / "repos" / expected_hash

        assert cache_path == expected_path
        assert cache_path.name != "official"
        assert cache_path.name == expected_hash

    def test_cache_path_differs_from_name_based_path(self, tmp_path: Path) -> None:
        """Old name-based path and new hash-based path are different."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = __import__("agent_nexus.models.distribution", fromlist=["SourceEntry"]).SourceEntry(
            name="my-source",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        hash_path = sm._get_cache_path(source)
        old_name_path = tmp_path / "cache" / "repos" / source.name

        assert hash_path != old_name_path

    def test_load_source_index_uses_hash_path(self, tmp_path: Path) -> None:
        """_load_source_index reads from hash-based cache directory."""
        import yaml

        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = __import__("agent_nexus.models.distribution", fromlist=["SourceEntry"]).SourceEntry(
            name="official",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        cache_dir = sm._get_cache_path(source)
        cache_dir.mkdir(parents=True, exist_ok=True)

        index_content = {
            "agents": [
                {
                    "name": "doc-filler",
                    "version": "1.0.0",
                    "type": "atomic",
                    "description": "Test agent",
                }
            ]
        }
        (cache_dir / "index.yaml").write_text(
            yaml.dump(index_content),
            encoding="utf-8",
        )

        result = sm._load_source_index(source)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "doc-filler"

    def test_consistent_hash_across_calls(self, tmp_path: Path) -> None:
        """Same URL always produces the same cache path."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = __import__("agent_nexus.models.distribution", fromlist=["SourceEntry"]).SourceEntry(
            name="test",
            type="git",
            url="https://github.com/example/repo.git",
        )
        path1 = sm._get_cache_path(source)
        path2 = sm._get_cache_path(source)
        assert path1 == path2


# ============================================================================
# Fix 2: Pipe safety in _create_venv (communicate() instead of wait()+read())
# ============================================================================


class TestPipeSafetyCreateVenv:
    """Verify _create_venv uses communicate() instead of wait()+stderr.read()."""

    async def test_create_venv_uses_communicate(self, tmp_path: Path) -> None:
        """_create_venv should call proc.communicate(), not proc.wait()."""
        from agent_nexus.platform.local.installer import GitInstaller
        from agent_nexus.platform.local.lockfile import LockfileManager

        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)
        installer = GitInstaller(sm, lm, tmp_path)

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

        mock_proc_venv = MagicMock()
        mock_proc_venv.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc_venv.returncode = 0

        mock_proc_install = MagicMock()
        mock_proc_install.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc_install.returncode = 0

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            side_effect=[mock_proc_venv, mock_proc_install],
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        mock_proc_venv.communicate.assert_awaited_once()
        mock_proc_install.communicate.assert_awaited_once()
        mock_proc_venv.wait.assert_not_called()
        mock_proc_install.wait.assert_not_called()

    async def test_create_venv_handles_failure(self, tmp_path: Path) -> None:
        """_create_venv returns None on uv failure, using communicate()."""
        from agent_nexus.platform.local.installer import GitInstaller
        from agent_nexus.platform.local.lockfile import LockfileManager

        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)
        installer = GitInstaller(sm, lm, tmp_path)

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error details"))
        mock_proc.returncode = 1

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result is None
        mock_proc.communicate.assert_awaited_once()
        mock_proc.wait.assert_not_called()


# ============================================================================
# Fix 3: IPC peek_buffer preserves mismatched task_id messages
# ============================================================================


class TestIPCPeekBufferPreservation:
    """Verify receive_until_result buffers mismatched task_id messages."""

    async def test_mismatched_task_id_buffered(self) -> None:
        """Messages with wrong task_id are buffered, not discarded."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        msg_a = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for A",
            task_id="task-A",
        )

        msg_b = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for B",
            task_id="task-B",
        )

        call_count = 0

        async def fake_receive(timeout=30.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return msg_a
            return msg_b

        with patch.object(protocol, "receive_result", side_effect=fake_receive):
            result = await protocol.receive_until_result(task_id="task-B", timeout=5.0)

        assert result.task_id == "task-B"
        assert result.content == "result for B"

        assert len(protocol._peek_buffer) == 1
        assert protocol._peek_buffer[0].task_id == "task-A"
        assert protocol._peek_buffer[0].content == "result for A"

    async def test_mismatched_progress_still_continues(self) -> None:
        """Progress messages for wrong task_id are buffered too."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        progress_wrong = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="progress for A",
            task_id="task-A",
        )
        result_right = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for B",
            task_id="task-B",
        )

        call_count = 0

        async def fake_receive(timeout=30.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return progress_wrong
            return result_right

        with patch.object(protocol, "receive_result", side_effect=fake_receive):
            result = await protocol.receive_until_result(task_id="task-B", timeout=5.0)

        assert result.task_id == "task-B"
        assert len(protocol._peek_buffer) == 1
        assert protocol._peek_buffer[0].task_id == "task-A"

    async def test_no_task_filter_still_works(self) -> None:
        """When task_id is None, all messages are accepted (no buffering)."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result",
            task_id="any-task",
        )

        with patch.object(protocol, "receive_result", return_value=msg):
            result = await protocol.receive_until_result(task_id=None, timeout=5.0)

        assert result.task_id == "any-task"
        assert len(protocol._peek_buffer) == 0


# ============================================================================
# Fix 4: FunctionRule catches obj.eval() patterns
# ============================================================================


class TestFunctionRuleAttributeCalls:
    """Verify FunctionRule catches method-call patterns like obj.eval()."""

    def test_attribute_call_blocked(self) -> None:
        """obj.eval() is now caught by FunctionRule."""
        rule = FunctionRule(forbidden=["eval"])
        code = "obj.eval('1+1')"
        tree = __import__("ast").parse(code)

        violations = []
        for node in __import__("ast").walk(tree):
            violations.extend(rule.check(node))

        assert len(violations) == 1
        assert violations[0].rule_type == "function"
        assert "eval" in violations[0].message

    def test_bare_call_still_blocked(self) -> None:
        """Bare eval('...') is still caught."""
        rule = FunctionRule(forbidden=["eval"])
        code = "eval('1+1')"
        tree = __import__("ast").parse(code)

        violations = []
        for node in __import__("ast").walk(tree):
            violations.extend(rule.check(node))

        assert len(violations) == 1

    def test_chained_attribute_call_blocked(self) -> None:
        """obj.attr.eval() is also caught."""
        rule = FunctionRule(forbidden=["eval"])
        code = "obj.attr.eval('code')"
        tree = __import__("ast").parse(code)

        violations = []
        for node in __import__("ast").walk(tree):
            violations.extend(rule.check(node))

        assert len(violations) == 1
        assert "eval" in violations[0].message

    def test_safe_method_call_not_blocked(self) -> None:
        """obj.safe_method() is not blocked when method is not forbidden."""
        rule = FunctionRule(forbidden=["eval"])
        code = "obj.safe_method('arg')"
        tree = __import__("ast").parse(code)

        violations = []
        for node in __import__("ast").walk(tree):
            violations.extend(rule.check(node))

        assert len(violations) == 0

    def test_exec_via_attribute_blocked(self) -> None:
        """obj.exec() is caught."""
        rule = FunctionRule(forbidden=["exec"])
        code = "obj.exec('code')"
        tree = __import__("ast").parse(code)

        violations = []
        for node in __import__("ast").walk(tree):
            violations.extend(rule.check(node))

        assert len(violations) == 1
        assert "exec" in violations[0].message


# ============================================================================
# Fix 5: EvolutionStore.save_skill_record preserves counters on overwrite
# ============================================================================


class TestEvolutionStoreCounterPreservation:
    """Verify save_skill_record preserves counters on overwrite."""

    def _make_record(
        self,
        skill_id: str = "s1",
        name: str = "test-skill",
        total_selections: int = 0,
        total_applied: int = 0,
        total_completions: int = 0,
        total_fallbacks: int = 0,
    ) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=name,
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.IMPORTED,
                generation=0,
            ),
            directory="skills/test",
            is_active=True,
            total_selections=total_selections,
            total_applied=total_applied,
            total_completions=total_completions,
            total_fallbacks=total_fallbacks,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    def test_save_preserves_counters_when_zero(self, tmp_path: Path) -> None:
        """Saving a record with zero counters preserves existing counter values."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
            total_completions=7,
            total_fallbacks=1,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 10
        assert loaded.total_applied == 8
        assert loaded.total_completions == 7
        assert loaded.total_fallbacks == 1

    def test_save_updates_counters_when_nonzero(self, tmp_path: Path) -> None:
        """Saving a record with nonzero counters updates the counters."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=20,
            total_applied=15,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 20
        assert loaded.total_applied == 15

    def test_save_new_record_inserts_normally(self, tmp_path: Path) -> None:
        """New records insert without any counter issues."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record = self._make_record(skill_id="s-new")
        store.save_skill_record(record)

        loaded = store.get_skill_record("s-new")
        assert loaded is not None
        assert loaded.total_selections == 0
        assert loaded.total_applied == 0

    def test_evolve_skill_uses_upsert(self, tmp_path: Path) -> None:
        """evolve_skill uses the same upsert pattern (counter-safe)."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        parent = self._make_record(
            skill_id="parent-1",
            name="parent-skill",
            total_selections=100,
        )
        store.save_skill_record(parent)

        evolved = SkillRecord(
            id="evolved-1",
            name="parent-skill",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=["parent-1"],
            ),
            directory="skills/test",
            is_active=True,
            total_selections=0,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        store.evolve_skill(evolved, parent_skill_ids=["parent-1"])

        loaded_parent = store.get_skill_record("parent-1")
        assert loaded_parent is not None
        assert loaded_parent.is_active is False

        loaded_evolved = store.get_skill_record("evolved-1")
        assert loaded_evolved is not None
        assert loaded_evolved.is_active is True

    def test_partial_counter_update(self, tmp_path: Path) -> None:
        """Only nonzero counters are updated; zero counters keep existing values."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
            total_completions=5,
            total_fallbacks=3,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=20,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 20  # updated
        assert loaded.total_applied == 8  # preserved
        assert loaded.total_completions == 5  # preserved
        assert loaded.total_fallbacks == 3  # preserved


# ============================================================================
# Fix 6: CompactionGuard.should_alert respects custom budget
# ============================================================================


class TestCompactionGuardCustomBudget:
    """Verify should_alert accepts and uses a custom ContextBudget."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "test.db")

    def _make_context(self) -> AgentContext:
        return AgentContext(
            agent_id="agent-a",
            session_id="session-1",
            l0_content="l0",
            l1_content="l1",
        )

    def test_default_budget_threshold(self, tmp_path: Path) -> None:
        """Without custom budget, uses default consecutive_compaction_alert=3."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        guard.reinject_after_compaction(ctx)
        guard.reinject_after_compaction(ctx)
        assert not guard.should_alert()

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert()

    def test_custom_budget_threshold(self, tmp_path: Path) -> None:
        """Custom budget with lower threshold triggers alert earlier."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        custom_budget = ContextBudget(consecutive_compaction_alert=2)

        guard.reinject_after_compaction(ctx)
        assert not guard.should_alert(budget=custom_budget)

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert(budget=custom_budget)

    def test_higher_threshold_custom_budget(self, tmp_path: Path) -> None:
        """Custom budget with higher threshold requires more compactions."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        custom_budget = ContextBudget(consecutive_compaction_alert=5)

        for _ in range(4):
            guard.reinject_after_compaction(ctx)
        assert not guard.should_alert(budget=custom_budget)

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert(budget=custom_budget)

    def test_none_budget_uses_default(self, tmp_path: Path) -> None:
        """Passing None explicitly uses default budget."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        for _ in range(3):
            guard.reinject_after_compaction(ctx)

        assert guard.should_alert(budget=None)
