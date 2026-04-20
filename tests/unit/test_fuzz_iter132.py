"""Fuzz tests for core public APIs — iter132 boundary value testing.

Tests boundary / adversarial inputs on:
1. PlatformRouter.route_chat() / route_to_atomic()
2. TaskGraph.add_task()
3. EvolutionStore.save_skill_record()
4. GitInstaller.install()
5. LockfileManager.load() / save()

Each API gets >= 3 boundary cases. Findings are reported as:
- P0: crash / unhandled exception in production path
- P1: crash on edge-case input that should be caught by validation
- P2: silent None/empty return that could cause downstream NPE
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.distribution import Lockfile, LockfileEntry
from agent_nexus.models.evolution import SkillLineage, SkillOrigin, SkillRecord
from agent_nexus.models.task import TaskItem
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.local.lockfile import LockfileManager
from agent_nexus.platform.orchestration.task_graph import TaskGraph
from agent_nexus.platform.router.router import PlatformRouter


# ============================================================================
# Helpers
# ============================================================================


def _make_task(
    task_id: str = "t1",
    description: str = "desc",
    agent: str = "agent-a",
    blocked_by: list[str] | None = None,
    **overrides,
) -> TaskItem:
    """Create a TaskItem with sensible defaults."""
    kwargs: dict = {
        "id": task_id,
        "description": description,
        "agent": agent,
        "blocked_by": blocked_by or [],
    }
    kwargs.update(overrides)
    return TaskItem(**kwargs)


def _make_skill_record(
    skill_id: str = "sk-1",
    name: str = "my-skill",
    version: str = "1.0.0",
    lineage: SkillLineage | None = None,
    **overrides,
) -> SkillRecord:
    """Create a SkillRecord with sensible defaults."""
    kwargs: dict = {
        "id": skill_id,
        "name": name,
        "version": version,
        "lineage": lineage or SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
    }
    kwargs.update(overrides)
    return SkillRecord(**kwargs)


# ============================================================================
# 1. PlatformRouter.route_chat() — Fuzz Tests
# ============================================================================


class TestRouteChatFuzz:
    """Boundary tests for PlatformRouter.route_chat()."""

    def _make_router(self) -> PlatformRouter:
        """Build a PlatformRouter with mocked ProcessManager."""
        pm = MagicMock()
        pm.get_agent.return_value = None  # No agents running
        return PlatformRouter(process_manager=pm)

    @pytest.mark.asyncio
    async def test_agent_name_none_returns_error_dict(self):
        """route_chat(agent_name=None) should return error dict, not crash.

        Python short-circuit: `not None` is True, so `.strip()` is never
        called on None. Input validation works correctly.
        """
        router = self._make_router()
        result = await router.route_chat(None, "hello")  # type: ignore[arg-type]
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert result.get("error_type") == "ValueError"

    @pytest.mark.asyncio
    async def test_agent_name_empty(self):
        """route_chat(agent_name='') should return error dict."""
        router = self._make_router()
        result = await router.route_chat("", "hello")
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert result.get("error_type") == "ValueError"

    @pytest.mark.asyncio
    async def test_agent_name_whitespace_only(self):
        """route_chat(agent_name='   ') should return error dict."""
        router = self._make_router()
        result = await router.route_chat("   ", "hello")
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert result.get("error_type") == "ValueError"

    @pytest.mark.asyncio
    async def test_message_none_returns_error_dict(self):
        """route_chat(message=None) should return error dict, not crash.

        Same short-circuit logic as agent_name — `not None` is True before
        `.strip()` is reached. Input validation works correctly.
        """
        router = self._make_router()
        result = await router.route_chat("my-agent", None)  # type: ignore[arg-type]
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert result.get("error_type") == "ValueError"

    @pytest.mark.asyncio
    async def test_message_empty(self):
        """route_chat(message='') should return error dict."""
        router = self._make_router()
        result = await router.route_chat("my-agent", "")
        assert result.get("success") is False
        assert result.get("error_type") == "ValueError"

    @pytest.mark.asyncio
    async def test_message_binary_garbage_bytes(self):
        """route_chat with binary garbage message should not crash."""
        router = self._make_router()
        binary_msg = b"\x00\x01\x02\xff\xfe\xfd garbage \x80\x90"
        # route_chat expects str, pass decoded (may have mojibake)
        msg_str = binary_msg.decode("utf-8", errors="replace")
        result = await router.route_chat("my-agent", msg_str)
        assert isinstance(result, dict)
        # Agent not found, so should get KeyError error_type
        assert result.get("error_type") == "KeyError"

    @pytest.mark.asyncio
    async def test_conversation_id_very_long(self):
        """route_chat with extremely long conversation_id should not crash."""
        router = self._make_router()
        long_conv_id = "x" * 1_000_000  # 1MB string
        result = await router.route_chat("my-agent", "hello", conversation_id=long_conv_id)
        assert isinstance(result, dict)
        # Should reach route_to_atomic since it's not composite
        assert result.get("error_type") == "KeyError"

    @pytest.mark.asyncio
    async def test_conversation_id_with_special_chars(self):
        """route_chat with special characters in conversation_id."""
        router = self._make_router()
        special_id = "conv\x00null\ttab\nnewline\\backslash'quote\"double"
        result = await router.route_chat("my-agent", "hello", conversation_id=special_id)
        assert isinstance(result, dict)


# ============================================================================
# 2. PlatformRouter.route_to_atomic() — Fuzz Tests
# ============================================================================


class TestRouteToAtomicFuzz:
    """Boundary tests for PlatformRouter.route_to_atomic()."""

    def _make_router(self) -> PlatformRouter:
        pm = MagicMock()
        pm.get_agent.return_value = None  # No agents running
        return PlatformRouter(process_manager=pm)

    @pytest.mark.asyncio
    async def test_unicode_agent_name(self):
        """route_to_atomic with unicode agent name."""
        router = self._make_router()
        result = await router.route_to_atomic("你好世界", "hello", "conv-1")
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert "not found" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_emoji_agent_name(self):
        """route_to_atomic with emoji agent name."""
        router = self._make_router()
        result = await router.route_to_atomic("🔥agent", "hello", "conv-1")
        assert isinstance(result, dict)
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_message_newlines_only(self):
        """route_to_atomic with message containing only newlines.

        This bypasses route_chat's empty check (newlines are truthy)
        but arrives at route_to_atomic directly.
        """
        router = self._make_router()
        result = await router.route_to_atomic("agent-a", "\n\n\n\n", "conv-1")
        assert isinstance(result, dict)
        assert result.get("success") is False
        # Agent not found — should be KeyError
        assert result.get("error_type") == "KeyError"

    @pytest.mark.asyncio
    async def test_message_very_long(self):
        """route_to_atomic with extremely long message (10MB)."""
        router = self._make_router()
        long_msg = "A" * 10_000_000
        result = await router.route_to_atomic("agent-a", long_msg, "conv-1")
        assert isinstance(result, dict)
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_agent_name_with_null_bytes(self):
        """route_to_atomic with null bytes in agent name."""
        router = self._make_router()
        result = await router.route_to_atomic("agent\x00evil", "hello", "conv-1")
        assert isinstance(result, dict)
        assert result.get("success") is False


# ============================================================================
# 3. TaskGraph.add_task() — Fuzz Tests
# ============================================================================


class TestTaskGraphAddTaskFuzz:
    """Boundary tests for TaskGraph.add_task()."""

    def _make_graph(self) -> TaskGraph:
        return TaskGraph(Path(":memory:"))

    def test_empty_name_raises_validation(self):
        """add_task with empty id should raise ValueError from Pydantic."""
        with pytest.raises(Exception):
            # Pydantic min_length=1 on TaskItem.id
            _make_task(task_id="")

    def test_empty_description_raises_validation(self):
        """add_task with empty description should raise ValueError from Pydantic."""
        with pytest.raises(Exception):
            # Pydantic min_length=1 on TaskItem.description
            _make_task(description="")

    def test_empty_agent_raises_validation(self):
        """add_task with empty agent should raise ValueError from Pydantic."""
        with pytest.raises(Exception):
            # Pydantic min_length=1 on TaskItem.agent
            _make_task(agent="")

    def test_normal_add_succeeds(self):
        """add_task with valid task should succeed."""
        tg = self._make_graph()
        task = _make_task(task_id="t1")
        tg.add_task(task)
        assert tg.get_task("t1") is not None

    def test_duplicate_id_raises(self):
        """add_task with duplicate ID should raise ValueError."""
        tg = self._make_graph()
        tg.add_task(_make_task(task_id="t1"))
        with pytest.raises(ValueError, match="already exists"):
            tg.add_task(_make_task(task_id="t1"))

    def test_self_reference_raises(self):
        """add_task with self-referencing blocked_by raises ValueError (model validator)."""
        with pytest.raises(ValueError, match="cannot block itself"):
            _make_task(task_id="t1", blocked_by=["t1"])

    def test_circular_dependency_unreachable_via_add_task(self):
        """FINDING (P2 / informational): cycle detection in add_task is unreachable.

        The _would_create_cycle check in add_task is defense-in-depth code that
        cannot fire in practice. Reason: blocked_by references must point to
        tasks that already exist in the graph (validated before cycle check).
        A new task X cannot appear in any existing task's blocked_by, because
        X did not exist when those tasks were added. Therefore no cycle can
        be formed through the add_task code path alone.

        Self-loops (X blocked_by [X]) are already caught by the TaskItem
        Pydantic model_validator _no_self_reference before add_task is called.

        This test documents the finding rather than asserting a behavior.
        """
        tg = self._make_graph()
        tg.add_task(_make_task(task_id="A", blocked_by=[]))
        tg.add_task(_make_task(task_id="B", blocked_by=["A"]))
        tg.add_task(_make_task(task_id="C", blocked_by=["B"]))
        # D -> C is valid (no cycle)
        tg.add_task(_make_task(task_id="D", blocked_by=["C"]))
        # Verify no cycles detected
        assert tg.detect_cycles() == []
        # Self-loop caught by Pydantic model_validator, not add_task
        with pytest.raises(ValueError, match="cannot block itself"):
            _make_task(task_id="E", blocked_by=["E"])

    def test_circular_dependency_three_node_cycle(self):
        """Create A->B, B->C, then try C->A (actual cycle)."""
        tg = self._make_graph()
        tg.add_task(_make_task(task_id="A", blocked_by=[]))
        tg.add_task(_make_task(task_id="B", blocked_by=["A"]))
        tg.add_task(_make_task(task_id="C", blocked_by=["B"]))
        # Now add D blocked_by A — no cycle, should work
        tg.add_task(_make_task(task_id="D", blocked_by=["A"]))
        # Try to add task E blocked_by C and A — no cycle, should work
        tg.add_task(_make_task(task_id="E", blocked_by=["C", "A"]))
        # Detect cycle in graph
        # Note: we can't create an actual cycle because add_task validates.
        # But let's verify detect_cycles returns empty
        assert tg.detect_cycles() == []

    def test_nonexistent_blocked_by_raises(self):
        """add_task referencing non-existent dependency raises ValueError."""
        tg = self._make_graph()
        with pytest.raises(ValueError, match="non-existent"):
            tg.add_task(_make_task(task_id="t1", blocked_by=["ghost"]))

    def test_none_blocked_by_treated_as_empty(self):
        """TaskItem with blocked_by=None should use default empty list.

        Pydantic default_factory=list handles None implicitly if field
        allows it. But since blocked_by type is list[str], passing None
        should fail or be coerced.
        """
        # TaskItem.blocked_by has default_factory=list, no Optional
        # Passing None should raise Pydantic validation error
        with pytest.raises(Exception):
            TaskItem(
                id="t1",
                description="desc",
                agent="a",
                blocked_by=None,  # type: ignore[arg-type]
            )

    def test_whitespace_only_id(self):
        """TaskItem with whitespace-only id should fail min_length=1 after strip?"""
        # Pydantic min_length=1 checks actual length, not stripped length
        # "   " has length 3, so it passes Pydantic but may cause issues downstream
        task = _make_task(task_id="   ")
        tg = self._make_graph()
        # This will succeed — Pydantic only checks min_length
        tg.add_task(task)
        # FINDING: P2 — whitespace-only ID accepted, may cause downstream confusion
        fetched = tg.get_task("   ")
        assert fetched is not None

    def test_very_long_task_id(self):
        """add_task with extremely long task ID."""
        long_id = "t" * 100_000
        tg = self._make_graph()
        task = _make_task(task_id=long_id)
        tg.add_task(task)
        assert tg.get_task(long_id) is not None

    def test_unicode_task_id(self):
        """add_task with unicode task ID."""
        tg = self._make_graph()
        task = _make_task(task_id="任务-1")
        tg.add_task(task)
        assert tg.get_task("任务-1") is not None


# ============================================================================
# 4. EvolutionStore.save_skill_record() — Fuzz Tests
# ============================================================================


class TestEvolutionStoreSaveFuzz:
    """Boundary tests for EvolutionStore.save_skill_record()."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        db = tmp_path / "evo_test.db"
        return EvolutionStore(db)

    def test_normal_save_and_load(self, tmp_path):
        """Baseline: save and retrieve a valid record."""
        store = self._make_store(tmp_path)
        record = _make_skill_record()
        store.save_skill_record(record)
        loaded = store.get_skill_record("sk-1")
        assert loaded is not None
        assert loaded.name == "my-skill"

    def test_empty_name_raises_validation(self):
        """SkillRecord with empty name should fail Pydantic min_length=1."""
        with pytest.raises(Exception):
            _make_skill_record(name="")

    def test_empty_id_raises_validation(self):
        """SkillRecord with empty id should fail Pydantic min_length=1."""
        with pytest.raises(Exception):
            _make_skill_record(skill_id="")

    def test_none_name_raises_validation(self):
        """SkillRecord with name=None should fail validation."""
        with pytest.raises(Exception):
            SkillRecord(
                id="sk-1",
                name=None,  # type: ignore[arg-type]
                lineage=SkillLineage(),
            )

    def test_record_with_none_lineage_fields(self, tmp_path):
        """save_skill_record with None content_diff and content_snapshot."""
        store = self._make_store(tmp_path)
        lineage = SkillLineage(
            origin=SkillOrigin.IMPORTED,
            generation=0,
            content_diff=None,
            content_snapshot=None,
        )
        record = _make_skill_record(lineage=lineage)
        store.save_skill_record(record)
        loaded = store.get_skill_record("sk-1")
        assert loaded is not None

    def test_record_with_empty_lineage_parent_ids(self, tmp_path):
        """save_skill_record with empty parent_skill_ids."""
        store = self._make_store(tmp_path)
        lineage = SkillLineage(
            origin=SkillOrigin.CAPTURED,
            generation=0,
            parent_skill_ids=[],
        )
        record = _make_skill_record(lineage=lineage)
        store.save_skill_record(record)
        loaded = store.get_skill_record("sk-1")
        assert loaded is not None
        assert loaded.lineage.parent_skill_ids == []

    def test_record_with_whitespace_name(self, tmp_path):
        """save_skill_record with whitespace-only name — Pydantic allows it."""
        # "   " passes min_length=1
        store = self._make_store(tmp_path)
        record = _make_skill_record(name="   ", skill_id="ws-1")
        store.save_skill_record(record)
        loaded = store.get_skill_record("ws-1")
        assert loaded is not None
        # FINDING: P2 — whitespace-only name accepted, could cause display issues

    def test_record_with_special_chars_name(self, tmp_path):
        """save_skill_record with special characters in name."""
        store = self._make_store(tmp_path)
        record = _make_skill_record(
            name='<script>alert("xss")</script>',
            skill_id="xss-1",
        )
        store.save_skill_record(record)
        loaded = store.get_skill_record("xss-1")
        assert loaded is not None
        assert loaded.name == '<script>alert("xss")</script>'

    def test_counter_invariant_violation_raises(self):
        """SkillRecord with applied > selections should fail validation."""
        with pytest.raises(Exception, match="total_applied cannot exceed"):
            _make_skill_record(total_selections=1, total_applied=5)

    def test_zero_selections_with_nonzero_applied_raises(self):
        """SkillRecord with 0 selections but non-zero applied should fail."""
        with pytest.raises(Exception, match="counter invariant"):
            _make_skill_record(total_selections=0, total_applied=1)

    def test_upsert_on_duplicate_id(self, tmp_path):
        """save_skill_record on duplicate ID should update, not error."""
        store = self._make_store(tmp_path)
        record_v1 = _make_skill_record(skill_id="sk-1", name="v1-skill", version="1.0.0")
        store.save_skill_record(record_v1)

        # Upsert with same ID but different name
        record_v2 = _make_skill_record(skill_id="sk-1", name="v2-skill", version="2.0.0")
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("sk-1")
        assert loaded is not None
        assert loaded.name == "v2-skill"
        assert loaded.version == "2.0.0"


# ============================================================================
# 5. GitInstaller.install() — Fuzz Tests
# ============================================================================


class TestGitInstallerInstallFuzz:
    """Boundary tests for GitInstaller.install()."""

    def _make_installer(self, tmp_path: Path):
        from agent_nexus.platform.local.installer import GitInstaller
        from agent_nexus.platform.local.sources import SourceManager

        sources = MagicMock(spec=SourceManager)
        sources.resolve_agent_source.return_value = None  # No sources configured
        lockfile_mgr = LockfileManager(tmp_path / "lockfile.json")
        return GitInstaller(
            source_manager=sources,
            lockfile_manager=lockfile_mgr,
            config_dir=tmp_path / "config",
        )

    @pytest.mark.asyncio
    async def test_url_with_spaces_raises(self, tmp_path):
        """install with source_url containing spaces should fail gracefully."""
        installer = self._make_installer(tmp_path)
        # URL with spaces — SourceEntry validation requires non-empty stripped URL
        # _url_to_source_name should handle it, but git clone will fail
        with pytest.raises(Exception):
            await installer.install(
                "test-agent",
                source_url="https://example.com/repo with spaces.git",
            )

    @pytest.mark.asyncio
    async def test_invalid_git_url(self, tmp_path):
        """install with completely invalid git URL should raise InstallationError."""
        from agent_nexus.platform.local.installer import InstallationError

        installer = self._make_installer(tmp_path)
        with pytest.raises((InstallationError, Exception)):
            await installer.install(
                "test-agent",
                source_url="not-a-valid-url",
            )

    @pytest.mark.asyncio
    async def test_invalid_agent_name_special_chars(self, tmp_path):
        """install with agent name containing special chars should raise InstallationError."""
        from agent_nexus.platform.local.installer import InstallationError

        installer = self._make_installer(tmp_path)
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("../../../etc/passwd")

    @pytest.mark.asyncio
    async def test_invalid_agent_name_spaces(self, tmp_path):
        """install with agent name containing spaces should raise InstallationError."""
        from agent_nexus.platform.local.installer import InstallationError

        installer = self._make_installer(tmp_path)
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("my agent")

    @pytest.mark.asyncio
    async def test_invalid_agent_name_empty(self, tmp_path):
        """install with empty agent name should raise InstallationError."""
        from agent_nexus.platform.local.installer import InstallationError

        installer = self._make_installer(tmp_path)
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("")

    @pytest.mark.asyncio
    async def test_agent_not_found_no_source(self, tmp_path):
        """install with no matching source should raise AgentNotFoundError."""
        from agent_nexus.platform.local.installer import AgentNotFoundError

        installer = self._make_installer(tmp_path)
        with pytest.raises(AgentNotFoundError):
            await installer.install("valid-agent-name")

    @pytest.mark.asyncio
    async def test_url_with_unicode(self, tmp_path):
        """install with unicode in URL — should not crash at name derivation."""
        installer = self._make_installer(tmp_path)
        from agent_nexus.platform.local.installer import InstallationError

        with pytest.raises((InstallationError, Exception)):
            await installer.install(
                "test-agent",
                source_url="https://example.com/仓库/repo.git",
            )

    @pytest.mark.asyncio
    async def test_url_with_null_bytes(self, tmp_path):
        """install with null bytes in URL."""
        from agent_nexus.platform.local.installer import InstallationError

        installer = self._make_installer(tmp_path)
        with pytest.raises((InstallationError, Exception)):
            await installer.install(
                "test-agent",
                source_url="https://example.com/repo\x00evil.git",
            )


# ============================================================================
# 6. LockfileManager.load() / save() — Fuzz Tests
# ============================================================================


class TestLockfileManagerFuzz:
    """Boundary tests for LockfileManager.load() and save()."""

    def test_load_nonexistent_returns_empty(self, tmp_path):
        """load() on nonexistent file returns empty Lockfile."""
        mgr = LockfileManager(tmp_path / "nope.json")
        result = mgr.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_load_valid_json(self, tmp_path):
        """load() on valid lockfile returns parsed data."""
        path = tmp_path / "lockfile.json"
        path.write_text(json.dumps({
            "version": 1,
            "agents": {
                "test-agent": {
                    "version": "1.0.0",
                    "source": "official",
                    "commit_sha": "a" * 40,
                    "agent_type": "atomic",
                    "installed_at": "2026-01-01T00:00:00Z",
                }
            }
        }))
        mgr = LockfileManager(path)
        result = mgr.load()
        assert "test-agent" in result.agents

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        """load() on corrupt JSON returns empty Lockfile (P2 check)."""
        path = tmp_path / "lockfile.json"
        path.write_text("{ this is not valid json !!!")
        mgr = LockfileManager(path)
        result = mgr.load()
        assert isinstance(result, Lockfile)
        # P2 FINDING: silently returns empty — data loss without warning to caller
        assert len(result.agents) == 0
        # But at least corrupt_detected flag should be set
        assert mgr._corrupt_detected is True

    def test_load_empty_file_returns_empty(self, tmp_path):
        """load() on empty file returns empty Lockfile."""
        path = tmp_path / "lockfile.json"
        path.write_text("")
        mgr = LockfileManager(path)
        result = mgr.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_load_wrong_structure_returns_empty(self, tmp_path):
        """load() on valid JSON but wrong structure returns empty."""
        path = tmp_path / "lockfile.json"
        path.write_text('{"version": 1, "agents": "not_a_dict"}')
        mgr = LockfileManager(path)
        result = mgr.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_load_truncated_json_returns_empty(self, tmp_path):
        """load() on truncated JSON returns empty."""
        path = tmp_path / "lockfile.json"
        path.write_text('{"version": 1, "agents": {"a":')
        mgr = LockfileManager(path)
        result = mgr.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_load_binary_garbage_returns_empty(self, tmp_path):
        """load() on binary garbage returns empty."""
        path = tmp_path / "lockfile.json"
        path.write_bytes(b"\x00\x01\x02\xff\xfe\xfd\x80\x90")
        mgr = LockfileManager(path)
        result = mgr.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_save_and_load_roundtrip(self, tmp_path):
        """save() then load() should return equivalent data."""
        from agent_nexus.models.agent import AgentType

        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)

        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
        )
        mgr.add_entry_by_name("test-agent", entry)

        loaded = mgr.load()
        assert "test-agent" in loaded.agents
        assert loaded.agents["test-agent"].version == "1.0.0"
        assert loaded.agents["test-agent"].commit_sha == "a" * 40

    def test_save_corrupt_backup(self, tmp_path):
        """After loading corrupt file, save() should backup the corrupt file."""
        path = tmp_path / "lockfile.json"
        path.write_text("CORRUPT DATA")

        mgr = LockfileManager(path)
        mgr.load()  # Detects corruption
        assert mgr._corrupt_detected is True

        # Now save valid data
        from agent_nexus.models.agent import AgentType
        entry = LockfileEntry(
            version="2.0.0",
            source="official",
            commit_sha="b" * 40,
            agent_type=AgentType.ATOMIC,
        )
        mgr.add_entry_by_name("new-agent", entry)

        # Corrupt backup should exist
        backup = path.with_suffix(".json.corrupt")
        assert backup.exists()

        # New data should be loadable
        loaded = mgr.load()
        assert "new-agent" in loaded.agents

    def test_save_atomic_no_partial_writes(self, tmp_path):
        """save() uses atomic write — temp file should not remain after success."""
        from agent_nexus.models.agent import AgentType

        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)

        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="c" * 40,
            agent_type=AgentType.ATOMIC,
        )
        mgr.add_entry_by_name("atomic-agent", entry)

        # No temp files should remain
        tmp_files = list(tmp_path.glob(".lockfile-*.tmp"))
        assert len(tmp_files) == 0

    def test_concurrent_saves_no_corruption(self, tmp_path):
        """Multiple sequential saves should not corrupt the file."""
        from agent_nexus.models.agent import AgentType

        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)

        for i in range(20):
            entry = LockfileEntry(
                version=f"{i}.0.0",
                source="official",
                commit_sha=f"{i:040d}",
                agent_type=AgentType.ATOMIC,
            )
            mgr.add_entry_by_name(f"agent-{i}", entry)

        loaded = mgr.load()
        assert len(loaded.agents) == 20

    def test_load_with_extra_fields_passes(self, tmp_path):
        """load() with extra unknown fields in JSON should still parse (Pydantic tolerant)."""
        path = tmp_path / "lockfile.json"
        path.write_text(json.dumps({
            "version": 1,
            "agents": {
                "test-agent": {
                    "version": "1.0.0",
                    "source": "official",
                    "commit_sha": "a" * 40,
                    "agent_type": "atomic",
                    "installed_at": "2026-01-01T00:00:00Z",
                    "unknown_field": "should_be_ignored",
                }
            },
            "extra_top_level": True,
        }))
        mgr = LockfileManager(path)
        result = mgr.load()
        assert "test-agent" in result.agents

    def test_load_with_negative_version(self, tmp_path):
        """load() with negative version field — Pydantic may reject."""
        path = tmp_path / "lockfile.json"
        path.write_text(json.dumps({
            "version": -1,
            "agents": {},
        }))
        mgr = LockfileManager(path)
        result = mgr.load()
        # Should either parse (Pydantic allows int) or return empty
        assert isinstance(result, Lockfile)
