"""Tests for enhanced search: capability filter, category filter, sort options."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from agent_nexus.models.distribution import SourceEntry
from agent_nexus.platform.local.sources import SourceManager


def _make_source(name: str = "test-src", url: str = "https://example.com/repo.git") -> SourceEntry:
    return SourceEntry(name=name, type="git", url=url, branch="main")


def _setup_index(tmp_path: Path, url: str, agents: list[dict]) -> tuple[SourceManager, SourceEntry]:
    """Create a SourceManager with an index containing the given agents."""
    sm = SourceManager(tmp_path / "sources.yaml")
    src = _make_source("test", url=url)
    sm.add_source(src)
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    cache_dir = tmp_path / "cache" / "repos" / digest
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.yaml").write_text(yaml.dump({"agents": agents}))
    return sm, src


# Helper: build agent dicts with G5 fields (capabilities, category, download_count, score)
def _agent(
    name: str = "agent",
    version: str = "1.0",
    agent_type: str = "atomic",
    description: str = "",
    tags: list[str] | None = None,
    capabilities: list[str] | None = None,
    category: str | None = None,
    download_count: int = 0,
    average_rating: float | None = None,
) -> dict:
    """Build an agent dict suitable for writing into index.yaml."""
    d: dict = {
        "name": name,
        "version": version,
        "type": agent_type,
        "description": description,
        "tags": tags or [],
        "capabilities": capabilities or [],
        "download_count": download_count,
    }
    if category is not None:
        d["category"] = category
    if average_rating is not None:
        d["score"] = {"average_rating": average_rating, "download_count": download_count}
    return d


class TestBackwardCompat:
    """Basic keyword search still works with no filter/sort parameters."""

    def test_keyword_search_no_filters(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/bc1.git",
            [_agent(name="code-reviewer", description="Reviews code")],
        )
        results = sm.search_agents("code")
        assert len(results) == 1
        assert results[0][1].name == "code-reviewer"

    def test_empty_results(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/bc2.git",
            [_agent(name="my-agent")],
        )
        results = sm.search_agents("nonexistent")
        assert results == []


class TestCapabilityFilter:
    """search_agents(capability=...) filters by entry.capabilities."""

    def test_returns_agents_with_matching_capability(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cap1.git",
            [
                _agent(name="a1", capabilities=["code-review", "testing"]),
                _agent(name="a2", capabilities=["documentation"]),
            ],
        )
        results = sm.search_agents("a", capability="code-review")
        assert len(results) == 1
        assert results[0][1].name == "a1"

    def test_returns_empty_when_no_capability_match(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cap2.git",
            [_agent(name="agent-x", capabilities=["documentation"])],
        )
        results = sm.search_agents("agent", capability="code-review")
        assert results == []

    def test_capability_with_empty_list_on_entry(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cap3.git",
            [_agent(name="bare-agent", capabilities=[])],
        )
        results = sm.search_agents("bare", capability="anything")
        assert results == []


class TestCategoryFilter:
    """search_agents(category=...) filters by entry.category."""

    def test_returns_agents_with_matching_category(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cat1.git",
            [
                _agent(name="a1", category="dev-tools"),
                _agent(name="a2", category="data"),
            ],
        )
        results = sm.search_agents("a", category="dev-tools")
        assert len(results) == 1
        assert results[0][1].name == "a1"

    def test_returns_empty_when_no_category_match(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cat2.git",
            [_agent(name="agent-y", category="data")],
        )
        results = sm.search_agents("agent", category="dev-tools")
        assert results == []

    def test_category_none_on_entry_does_not_match(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/cat3.git",
            [_agent(name="no-cat")],  # category is None by default
        )
        results = sm.search_agents("no", category="anything")
        assert results == []


class TestCombinedFilters:
    """Keyword + capability + category filters applied together."""

    def test_keyword_and_capability(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/comb1.git",
            [
                _agent(name="code-reviewer", capabilities=["code-review"]),
                _agent(name="code-generator", capabilities=["code-gen"]),
            ],
        )
        results = sm.search_agents("code", capability="code-review")
        assert len(results) == 1
        assert results[0][1].name == "code-reviewer"

    def test_keyword_and_category(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/comb2.git",
            [
                _agent(name="tool-a", category="dev-tools"),
                _agent(name="tool-b", category="data"),
            ],
        )
        results = sm.search_agents("tool", category="data")
        assert len(results) == 1
        assert results[0][1].name == "tool-b"

    def test_keyword_capability_and_category(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/comb3.git",
            [
                _agent(
                    name="super-tool",
                    capabilities=["testing"],
                    category="dev-tools",
                ),
                _agent(
                    name="super-lib",
                    capabilities=["testing"],
                    category="data",
                ),
                _agent(
                    name="super-other",
                    capabilities=["documentation"],
                    category="dev-tools",
                ),
            ],
        )
        results = sm.search_agents("super", capability="testing", category="dev-tools")
        assert len(results) == 1
        assert results[0][1].name == "super-tool"


class TestSortByDownloads:
    """sort_by='downloads' orders by download_count descending."""

    def test_sort_by_download_count(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-dl.git",
            [
                _agent(name="apop", download_count=100),
                _agent(name="amid", download_count=50),
                _agent(name="anew", download_count=0),
            ],
        )
        results = sm.search_agents("a", sort_by="downloads")
        assert len(results) == 3
        assert results[0][1].name == "apop"
        assert results[1][1].name == "amid"
        assert results[2][1].name == "anew"


class TestSortByName:
    """sort_by='name' orders alphabetically."""

    def test_sort_alphabetically(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-name.git",
            [
                _agent(name="charlie"),
                _agent(name="alpha"),
                _agent(name="bravo"),
            ],
        )
        results = sm.search_agents("a", sort_by="name")
        names = [r[1].name for r in results]
        assert names == ["alpha", "bravo", "charlie"]

    def test_sort_name_case_insensitive(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-name2.git",
            [
                _agent(name="Beta"),
                _agent(name="alpha"),
                _agent(name="Gamma"),
            ],
        )
        results = sm.search_agents("a", sort_by="name")
        names = [r[1].name for r in results]
        assert names == ["alpha", "Beta", "Gamma"]


class TestSortByRating:
    """sort_by='rating' orders by average_rating descending, None last."""

    def test_sort_by_rating_descending(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-rate.git",
            [
                _agent(name="atop", average_rating=4.8),
                _agent(name="amid", average_rating=3.5),
                _agent(name="alow", average_rating=2.0),
            ],
        )
        results = sm.search_agents("a", sort_by="rating")
        names = [r[1].name for r in results]
        assert names == ["atop", "amid", "alow"]

    def test_none_ratings_sorted_last(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-rate2.git",
            [
                _agent(name="unrated"),  # no score
                _agent(name="rated", average_rating=4.0),
            ],
        )
        results = sm.search_agents("a", sort_by="rating")
        names = [r[1].name for r in results]
        assert names == ["rated", "unrated"]

    def test_mix_rated_and_unrated(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-rate3.git",
            [
                _agent(name="aa-low", average_rating=2.0),
                _agent(name="ab-none"),
                _agent(name="ac-high", average_rating=5.0),
                _agent(name="ad-mid", average_rating=3.0),
            ],
        )
        results = sm.search_agents("a", sort_by="rating")
        names = [r[1].name for r in results]
        assert names == ["ac-high", "ad-mid", "aa-low", "ab-none"]


class TestSortByRelevance:
    """sort_by='relevance' keeps keyword match order (default)."""

    def test_default_sort_is_relevance(self, tmp_path: Path) -> None:
        sm, _ = _setup_index(
            tmp_path,
            "https://example.com/sort-rel.git",
            [
                _agent(name="agent-1", download_count=100),
                _agent(name="agent-2", download_count=0),
            ],
        )
        results = sm.search_agents("agent")
        # Default order is the index order from the source
        names = [r[1].name for r in results]
        assert names == ["agent-1", "agent-2"]
