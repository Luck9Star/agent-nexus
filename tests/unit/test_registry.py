"""Tests for agency ExpertRegistry (thread-safe in-memory registry)."""

import threading
from typing import Any


from agent_nexus.platform.agency.registry import ExpertRegistry


def _profile(name: str, **kwargs: Any) -> dict[str, Any]:
    """Helper to create a profile dict."""
    return {"name": name, **kwargs}


# ---------------------------------------------------------------------------
# add / get
# ---------------------------------------------------------------------------


class TestRegistryAddGet:
    def test_add_and_get(self):
        reg = ExpertRegistry()
        p = _profile("expert-1", role="analyst")
        reg.add("e1", p, ["analysis", "research"])
        assert reg.get("e1") == p

    def test_get_missing_returns_none(self):
        reg = ExpertRegistry()
        assert reg.get("nonexistent") is None

    def test_add_overwrites_existing(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("old"), ["cap-a"])
        reg.add("e1", _profile("new"), ["cap-b"])
        assert reg.get("e1")["name"] == "new"

    def test_overwrite_removes_old_capability_index(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("old"), ["cap-a"])
        reg.add("e1", _profile("new"), ["cap-b"])
        # cap-a should no longer index e1
        results = reg.search_by_capability(["cap-a"])
        assert len(results) == 0

    def test_overwrite_adds_new_capability_index(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("old"), ["cap-a"])
        reg.add("e1", _profile("new"), ["cap-b"])
        results = reg.search_by_capability(["cap-b"])
        assert len(results) == 1
        assert results[0]["name"] == "new"

    def test_add_duplicate_capability_idempotent(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap-a"])
        reg.add("e1", _profile("a-updated"), ["cap-a"])
        results = reg.search_by_capability(["cap-a"])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# search_by_capability
# ---------------------------------------------------------------------------


class TestRegistrySearch:
    def test_search_single_capability(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["analysis"])
        reg.add("e2", _profile("b"), ["research"])
        results = reg.search_by_capability(["analysis"])
        assert len(results) == 1
        assert results[0]["name"] == "a"

    def test_search_any_match(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["analysis"])
        reg.add("e2", _profile("b"), ["research"])
        results = reg.search_by_capability(["analysis", "research"])
        assert len(results) == 2

    def test_search_no_match_returns_empty(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["analysis"])
        assert reg.search_by_capability(["nonexistent"]) == []

    def test_search_returns_sorted_by_id(self):
        reg = ExpertRegistry()
        reg.add("c", _profile("c"), ["cap"])
        reg.add("a", _profile("a"), ["cap"])
        reg.add("b", _profile("b"), ["cap"])
        results = reg.search_by_capability(["cap"])
        assert [r["name"] for r in results] == ["a", "b", "c"]

    def test_search_skips_removed_profiles(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap"])
        reg.remove("e1")
        assert reg.search_by_capability(["cap"]) == []

    def test_search_empty_capabilities_list(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap"])
        assert reg.search_by_capability([]) == []

    def test_search_profile_with_multiple_caps_matched_once(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap-a", "cap-b"])
        results = reg.search_by_capability(["cap-a", "cap-b"])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class TestRegistryListAll:
    def test_empty_registry(self):
        reg = ExpertRegistry()
        assert reg.list_all() == []

    def test_returns_sorted_ids(self):
        reg = ExpertRegistry()
        reg.add("z1", _profile("z"), [])
        reg.add("a1", _profile("a"), [])
        reg.add("m1", _profile("m"), [])
        assert reg.list_all() == ["a1", "m1", "z1"]


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRegistryRemove:
    def test_remove_existing(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap"])
        assert reg.remove("e1") is True
        assert reg.get("e1") is None

    def test_remove_nonexistent(self):
        reg = ExpertRegistry()
        assert reg.remove("nonexistent") is False

    def test_remove_cleans_capability_index(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap-a", "cap-b"])
        reg.remove("e1")
        assert reg.search_by_capability(["cap-a"]) == []
        assert reg.search_by_capability(["cap-b"]) == []

    def test_remove_shared_capability_keeps_other(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["shared"])
        reg.add("e2", _profile("b"), ["shared"])
        reg.remove("e1")
        results = reg.search_by_capability(["shared"])
        assert len(results) == 1
        assert results[0]["name"] == "b"


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestRegistryClear:
    def test_clear_empties_all(self):
        reg = ExpertRegistry()
        reg.add("e1", _profile("a"), ["cap"])
        reg.add("e2", _profile("b"), ["cap"])
        reg.clear()
        assert reg.list_all() == []
        assert reg.get("e1") is None
        assert reg.search_by_capability(["cap"]) == []


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestRegistryThreadSafety:
    def test_concurrent_adds(self):
        reg = ExpertRegistry()
        barrier = threading.Barrier(10)
        errors: list[Exception] = []

        def add_profile(i: int):
            try:
                barrier.wait()
                reg.add(f"e{i}", _profile(f"p{i}"), [f"cap-{i % 3}"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add_profile, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(reg.list_all()) == 10

    def test_concurrent_add_and_remove(self):
        reg = ExpertRegistry()
        for i in range(20):
            reg.add(f"e{i}", _profile(f"p{i}"), ["cap"])

        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def remove_half():
            try:
                barrier.wait()
                for i in range(0, 10):
                    reg.remove(f"e{i}")
            except Exception as exc:
                errors.append(exc)

        def add_new():
            try:
                barrier.wait()
                for i in range(20, 30):
                    reg.add(f"e{i}", _profile(f"p{i}"), ["cap"])
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=remove_half)
        t2 = threading.Thread(target=add_new)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        remaining = reg.list_all()
        # e0-e9 removed, e10-e19 kept, e20-e29 added
        assert len(remaining) == 20
