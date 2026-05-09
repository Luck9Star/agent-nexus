"""Tests for context_provider module — ContextProvider registry and providers."""

from __future__ import annotations

from agent_nexus.platform.agency.context_provider import (
    ExpertListProvider,
    ProviderRegistry,
    ReflectionFeedbackProvider,
    TaskSummaryProvider,
)


class TestTaskSummaryProvider:
    """TaskSummaryProvider formats task summaries."""

    def test_empty_tasks_returns_empty_string(self) -> None:
        provider = TaskSummaryProvider()
        assert provider.get_context() == ""

    def test_single_task_formatted(self) -> None:
        provider = TaskSummaryProvider()
        provider.update(["Task A"])
        assert provider.get_context() == "- Task A"

    def test_multiple_tasks_joined(self) -> None:
        provider = TaskSummaryProvider()
        provider.update(["Task A", "Task B"])
        ctx = provider.get_context()
        assert "- Task A" in ctx
        assert "- Task B" in ctx

    def test_update_replaces_previous(self) -> None:
        provider = TaskSummaryProvider()
        provider.update(["Old"])
        provider.update(["New"])
        assert "- Old" not in provider.get_context()
        assert "- New" in provider.get_context()

    def test_has_title(self) -> None:
        assert TaskSummaryProvider.title


class TestExpertListProvider:
    """ExpertListProvider lists available experts."""

    def test_empty_experts(self) -> None:
        provider = ExpertListProvider([])
        assert provider.get_context() == ""

    def test_single_expert(self) -> None:
        provider = ExpertListProvider([{"id": "reviewer", "name": "Code Reviewer"}])
        ctx = provider.get_context()
        assert "Code Reviewer" in ctx

    def test_has_title(self) -> None:
        assert ExpertListProvider.title


class TestReflectionFeedbackProvider:
    """ReflectionFeedbackProvider formats reflection feedback."""

    def test_empty_feedback(self) -> None:
        provider = ReflectionFeedbackProvider()
        assert provider.get_context() == ""

    def test_update_and_get_context(self) -> None:
        provider = ReflectionFeedbackProvider()
        provider.update("Needs improvement")
        assert "Needs improvement" in provider.get_context()

    def test_has_title(self) -> None:
        assert ReflectionFeedbackProvider.title


class TestProviderRegistry:
    """ProviderRegistry manages named providers with priorities."""

    def test_register_and_get(self) -> None:
        registry = ProviderRegistry()
        provider = TaskSummaryProvider()
        registry.register("summary", provider)
        assert registry.get("summary") is provider

    def test_get_unknown_returns_none(self) -> None:
        registry = ProviderRegistry()
        assert registry.get("missing") is None

    def test_unregister_removes_provider(self) -> None:
        registry = ProviderRegistry()
        provider = TaskSummaryProvider()
        registry.register("summary", provider)
        registry.unregister("summary")
        assert registry.get("summary") is None

    def test_unregister_unknown_is_noop(self) -> None:
        registry = ProviderRegistry()
        registry.unregister("nonexistent")
        assert registry.get("nonexistent") is None

    def test_default_priority_is_7(self) -> None:
        registry = ProviderRegistry()
        assert registry.get_priority("unknown") == 7

    def test_custom_priority(self) -> None:
        registry = ProviderRegistry()
        provider = TaskSummaryProvider()
        registry.register("summary", provider, priority=3)
        assert registry.get_priority("summary") == 3

    def test_providers_returns_copy(self) -> None:
        registry = ProviderRegistry()
        provider = TaskSummaryProvider()
        registry.register("summary", provider)
        procs = registry.providers
        procs["extra"] = TaskSummaryProvider()
        assert "extra" not in registry.providers
