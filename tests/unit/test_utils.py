"""Unit tests for agent_nexus.platform.utils module.

Validates that:
- agent_name_to_package converts hyphenated names to prefixed underscore names
- to_class_name converts hyphenated/snake_case names to PascalCase
- make_error_result returns the expected error dict structure
- cache_path_for_url produces deterministic paths under cache/repos
- detect_cycles_dfs correctly identifies cycles in directed graphs
- sqlite_connection provides proper lifecycle management
- resolve_composition_path follows the documented resolution order
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_nexus.models.composition import detect_cycles_dfs
from agent_nexus.platform.utils import (
    agent_name_to_package,
    cache_path_for_url,
    make_error_result,
    resolve_composition_path,
    sqlite_connection,
    to_class_name,
)


# ---------------------------------------------------------------------------
# agent_name_to_package
# ---------------------------------------------------------------------------


class TestAgentNameToPackage:
    """Tests for agent_name_to_package function."""

    def test_hyphenated_name(self):
        assert agent_name_to_package("code-reviewer") == "agent_code_reviewer"

    def test_multi_hyphen_name(self):
        assert agent_name_to_package("test-suite-generator") == "agent_test_suite_generator"

    def test_single_word(self):
        assert agent_name_to_package("planner") == "agent_planner"

    def test_underscore_name_unchanged(self):
        assert agent_name_to_package("my_agent") == "agent_my_agent"

    def test_already_prefixed(self):
        """If the name already starts with agent_, it still gets the prefix."""
        assert agent_name_to_package("agent_code-reviewer") == "agent_agent_code_reviewer"

    def test_empty_string(self):
        assert agent_name_to_package("") == "agent_"

    def test_name_with_numbers(self):
        assert agent_name_to_package("v2-updater") == "agent_v2_updater"


# ---------------------------------------------------------------------------
# to_class_name
# ---------------------------------------------------------------------------


class TestToClassName:
    """Tests for to_class_name function."""

    def test_hyphenated_to_pascal_case(self):
        assert to_class_name("code-reviewer") == "CodeReviewer"

    def test_multi_hyphen_to_pascal_case(self):
        assert to_class_name("test-suite-generator") == "TestSuiteGenerator"

    def test_single_word_capitalized(self):
        assert to_class_name("planner") == "Planner"

    def test_already_pascal_case_with_no_hyphens(self):
        """No hyphens means no split; capitalize() lowercases tail chars."""
        assert to_class_name("CodeReviewer") == "Codereviewer"

    def test_underscore_name(self):
        """Underscores are not the separator; split is on hyphens only."""
        assert to_class_name("my_agent") == "My_agent"

    def test_single_char(self):
        assert to_class_name("a") == "A"

    def test_empty_string(self):
        assert to_class_name("") == ""


# ---------------------------------------------------------------------------
# make_error_result
# ---------------------------------------------------------------------------


class TestMakeErrorResult:
    """Tests for make_error_result function."""

    def test_returns_expected_structure(self):
        result = make_error_result("something failed", "RuntimeError")
        assert result == {
            "output": "",
            "success": False,
            "error": "something failed",
            "error_type": "RuntimeError",
        }

    def test_empty_error_message(self):
        result = make_error_result("", "ValueError")
        assert result["error"] == ""
        assert result["error_type"] == "ValueError"
        assert result["success"] is False

    def test_various_error_types(self):
        for error_type in ("IPCError", "TimeoutError", "ProcessNotAliveError"):
            result = make_error_result("msg", error_type)
            assert result["error_type"] == error_type

    def test_output_always_empty(self):
        result = make_error_result("err", "E")
        assert result["output"] == ""

    def test_success_always_false(self):
        result = make_error_result("err", "E")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# cache_path_for_url
# ---------------------------------------------------------------------------


class TestCachePathForUrl:
    """Tests for cache_path_for_url function."""

    def test_path_under_cache_repos(self):
        base = Path("/tmp/test_base")
        result = cache_path_for_url(base, "https://github.com/example/repo.git")
        assert result.parent.parent == base / "cache"
        assert result.parent == base / "cache" / "repos"
        assert len(result.name) == 12

    def test_deterministic_same_url(self):
        base = Path("/tmp")
        url = "https://github.com/example/repo.git"
        result1 = cache_path_for_url(base, url)
        result2 = cache_path_for_url(base, url)
        assert result1 == result2

    def test_different_urls_different_paths(self):
        base = Path("/tmp")
        url1 = "https://github.com/example/repo1.git"
        url2 = "https://github.com/example/repo2.git"
        assert cache_path_for_url(base, url1) != cache_path_for_url(base, url2)

    def test_digest_is_hex(self):
        base = Path("/tmp")
        result = cache_path_for_url(base, "https://example.com")
        assert all(c in "0123456789abcdef" for c in result.name)

    def test_path_includes_base_dir(self):
        base = Path("/custom/base")
        result = cache_path_for_url(base, "https://example.com")
        assert str(result).startswith(str(base))


# ---------------------------------------------------------------------------
# detect_cycles_dfs
# ---------------------------------------------------------------------------


class TestDetectCyclesDfs:
    """Tests for detect_cycles_dfs function."""

    def test_empty_graph(self):
        result = detect_cycles_dfs([], lambda _: [])
        assert result == []

    def test_single_node_no_edges(self):
        result = detect_cycles_dfs(["A"], lambda _: [])
        assert result == []

    def test_self_loop_detected(self):
        result = detect_cycles_dfs(["A"], lambda _: ["A"])
        assert len(result) == 1
        assert result[0] == ["A", "A"]

    def test_two_node_cycle(self):
        graph = {"A": ["B"], "B": ["A"]}
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert len(result) == 1
        cycle = result[0]
        assert cycle[0] == cycle[-1]  # cycle starts and ends with same node

    def test_dag_no_cycles(self):
        graph = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert result == []

    def test_larger_graph_with_cycle(self):
        graph = {"A": ["B"], "B": ["C"], "C": ["D"], "D": ["B"]}
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert len(result) == 1
        # Cycle should include B -> C -> D -> B
        cycle = result[0]
        assert "B" in cycle

    def test_disconnected_graph_one_cycle(self):
        graph = {
            "A": ["B"],
            "B": [],
            "C": ["D"],
            "D": ["C"],
        }
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert len(result) == 1

    def test_multiple_cycles(self):
        graph = {
            "A": ["B"],
            "B": ["A"],
            "C": ["D"],
            "D": ["C"],
        }
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert len(result) == 2

    def test_linear_chain_no_cycle(self):
        graph = {"A": ["B"], "B": ["C"], "C": ["D"], "D": []}
        result = detect_cycles_dfs(graph.keys(), lambda n: graph[n])
        assert result == []


# ---------------------------------------------------------------------------
# sqlite_connection
# ---------------------------------------------------------------------------


class TestSqliteConnection:
    """Tests for sqlite_connection context manager."""

    def test_file_based_connection_usable(self, tmp_path):
        db_file = tmp_path / "test.db"
        with sqlite_connection(db_file) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO t VALUES (1)")
        # Re-open and verify data persisted
        with sqlite_connection(db_file) as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
            assert rows == [(1,)]

    def test_file_based_connection_closed_after_exit(self, tmp_path):
        db_file = tmp_path / "test.db"
        with sqlite_connection(db_file) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        # Connection should be closed; executing should fail or auto-reopen
        # We verify by opening a new connection
        conn2 = sqlite3.connect(str(db_file))
        rows = conn2.execute("SELECT count(*) FROM t").fetchall()
        conn2.close()
        assert rows == [(0,)]  # table exists, empty

    def test_memory_db_with_persistent_conn(self):
        persistent = sqlite3.connect(":memory:")
        with sqlite_connection(":memory:", persistent_conn=persistent) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO t VALUES (42)")
        # Data should still be accessible via persistent conn
        rows = persistent.execute("SELECT id FROM t").fetchall()
        assert rows == [(42,)]
        persistent.close()

    def test_memory_db_without_persistent_conn_raises(self):
        with pytest.raises(ValueError, match="persistent_conn is required"):
            with sqlite_connection(":memory:"):
                pass  # pragma: no cover

    def test_foreign_keys_pragma_set(self, tmp_path):
        db_file = tmp_path / "test.db"
        with sqlite_connection(db_file) as conn:
            fk_status = conn.execute("PRAGMA foreign_keys").fetchone()
            assert fk_status == (1,)

    def test_rollback_on_exception(self, tmp_path):
        db_file = tmp_path / "test.db"
        # Create table first
        with sqlite_connection(db_file) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        # Now cause an error mid-transaction
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite_connection(db_file) as conn:
                conn.execute("INSERT INTO t VALUES (1, 'ok')")
                # This will fail: UNIQUE constraint on id
                conn.execute("INSERT INTO t VALUES (1, 'dup')")
        # First insert should have been rolled back
        with sqlite_connection(db_file) as conn:
            rows = conn.execute("SELECT count(*) FROM t").fetchall()
            assert rows == [(0,)]

    def test_immediate_mode(self, tmp_path):
        db_file = tmp_path / "test.db"
        with sqlite_connection(db_file, immediate=True) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            # Connection should be in transaction
            assert conn.in_transaction is True


# ---------------------------------------------------------------------------
# resolve_composition_path
# ---------------------------------------------------------------------------


class TestResolveCompositionPath:
    """Tests for resolve_composition_path function."""

    def test_resolves_from_caller_dir(self, tmp_path):
        comp_file = tmp_path / "composition.toml"
        comp_file.write_text("[composition]\nname = 'test'")
        result = resolve_composition_path(str(tmp_path / "coordinator.py"))
        assert result == comp_file

    def test_resolves_from_parent_dir_dev_mode(self, tmp_path):
        subdir = tmp_path / "coordinator"
        subdir.mkdir()
        comp_file = tmp_path / "composition.toml"
        comp_file.write_text("[composition]\nname = 'test'")
        result = resolve_composition_path(str(subdir / "coordinator.py"))
        assert result == comp_file

    def test_agent_dir_env_var_takes_priority(self, tmp_path):
        agent_dir = tmp_path / "installed"
        agent_dir.mkdir()
        comp_file = agent_dir / "composition.toml"
        comp_file.write_text("[composition]\nname = 'installed'")

        caller_dir = tmp_path / "pkg"
        caller_dir.mkdir()
        # Also create one in caller_dir — should NOT be used
        (caller_dir / "composition.toml").write_text("[composition]\nname = 'pkg'")

        with patch.dict(os.environ, {"AGENT_DIR": str(agent_dir)}):
            result = resolve_composition_path(str(caller_dir / "coordinator.py"))
        assert result == comp_file

    def test_returns_none_when_not_found(self, tmp_path):
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        result = resolve_composition_path(str(isolated / "coord.py"))
        assert result is None

    def test_agent_dir_set_but_file_missing_falls_back(self, tmp_path):
        agent_dir = tmp_path / "missing"
        agent_dir.mkdir()
        # No composition.toml in agent_dir
        caller_dir = tmp_path / "pkg"
        caller_dir.mkdir()
        comp_file = caller_dir / "composition.toml"
        comp_file.write_text("[composition]\nname = 'fallback'")

        with patch.dict(os.environ, {"AGENT_DIR": str(agent_dir)}):
            result = resolve_composition_path(str(caller_dir / "coordinator.py"))
        assert result == comp_file
