"""Integration: P1 Hooks + P2 Token Counter + P3 Context Provider.

Tests the complete hook -> token trimming -> context provider data flow,
verifying that data passes correctly across all three module boundaries.
"""

from unittest.mock import MagicMock

import pytest

from agent_nexus.platform.agency.context_provider import (
    ContextProvider,
    ExpertListProvider,
    ProviderRegistry,
    ReflectionFeedbackProvider,
    TaskSummaryProvider,
)
from agent_nexus.platform.agency.hooks import (
    CallContext,
    HookAbort,
    HookEvent,
    HookManager,
)
from agent_nexus.platform.agency.token_counter import (
    PromptSection,
    StructuredPrompt,
    TokenCounter,
)


# ---------------------------------------------------------------------------
# 1. Hook BEFORE_CALL modifies prompt
# ---------------------------------------------------------------------------


class TestHookBeforeCallModifiesPrompt:
    """P1 Hook dispatches BEFORE_CALL -> handler mutates CallContext -> downstream sees change."""

    def test_before_call_handler_modifies_system_prompt(self):
        hook_mgr = HookManager()

        def append_context(ctx: CallContext) -> None:
            ctx.system_prompt += "\n## Extra Context\nSome injected info"

        hook_mgr.register(HookEvent.BEFORE_CALL, append_context)

        ctx = CallContext(
            model="test:model",
            system_prompt="You are a helpful assistant.",
            user_message="Hello",
            temperature=None,
            response_format=None,
            timeout=None,
        )

        hook_mgr.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)

        assert "Extra Context" in ctx.system_prompt
        assert ctx.system_prompt == (
            "You are a helpful assistant.\n## Extra Context\nSome injected info"
        )

    def test_before_call_handler_modifies_user_message(self):
        hook_mgr = HookManager()

        def prefix_message(ctx: CallContext) -> None:
            ctx.user_message = f"[Important] {ctx.user_message}"

        hook_mgr.register(HookEvent.BEFORE_CALL, prefix_message)

        ctx = CallContext(
            model="test:model",
            system_prompt="System",
            user_message="Do the task",
            temperature=None,
            response_format=None,
            timeout=None,
        )

        hook_mgr.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)

        assert ctx.user_message == "[Important] Do the task"

    def test_before_call_abort_propagates(self):
        hook_mgr = HookManager()

        def abort_handler(ctx: CallContext) -> None:
            raise HookAbort("Cancelled by policy")

        hook_mgr.register(HookEvent.BEFORE_CALL, abort_handler)

        ctx = CallContext(
            model="test:model",
            system_prompt="System",
            user_message="Hello",
            temperature=None,
            response_format=None,
            timeout=None,
        )

        with pytest.raises(HookAbort, match="Cancelled by policy"):
            hook_mgr.dispatch(HookEvent.BEFORE_CALL, ctx=ctx)


# ---------------------------------------------------------------------------
# 2. StructuredPrompt with Providers
# ---------------------------------------------------------------------------


class TestStructuredPromptWithProviders:
    """P3 Provider content flows into P2 StructuredPrompt rendering."""

    def test_render_output_includes_provider_content(self):
        registry = ProviderRegistry()
        task_provider = TaskSummaryProvider()
        task_provider.update(["Write tests", "Fix bugs"])
        registry.register("task_summary", task_provider)

        prompt = StructuredPrompt()
        prompt.add("Role", "You are a QA engineer.", priority=1)
        prompt.add_from_providers(registry.providers, priority=7)

        rendered = prompt.render()
        assert "QA engineer" in rendered
        assert "Write tests" in rendered
        assert "Fix bugs" in rendered

    def test_add_from_providers_skips_empty_providers(self):
        registry = ProviderRegistry()
        # TaskSummaryProvider with no tasks -> get_context() returns ""
        empty_provider = TaskSummaryProvider()
        registry.register("empty_tasks", empty_provider)

        # ReflectionFeedbackProvider with no feedback -> get_context() returns ""
        empty_feedback = ReflectionFeedbackProvider()
        registry.register("empty_feedback", empty_feedback)

        prompt = StructuredPrompt()
        prompt.add("Core", "Main content", priority=1)
        prompt.add_from_providers(registry.providers, priority=7)

        # Only the core section should exist — empty providers are skipped
        assert len(prompt.sections) == 1
        assert prompt.sections[0].title == "Core"

    def test_multiple_providers_in_render(self):
        registry = ProviderRegistry()

        task_prov = TaskSummaryProvider()
        task_prov.update(["Task A", "Task B"])
        registry.register("tasks", task_prov, priority=5)

        expert_prov = ExpertListProvider([
            {"name": "reviewer", "capabilities": ["code-review", "security"]},
        ])
        registry.register("experts", expert_prov, priority=3)

        feedback_prov = ReflectionFeedbackProvider()
        feedback_prov.update("Be more detailed")
        registry.register("feedback", feedback_prov, priority=7)

        prompt = StructuredPrompt()
        prompt.add_from_providers(registry.providers)

        rendered = prompt.render()
        assert "Task A" in rendered
        assert "reviewer" in rendered
        assert "Be more detailed" in rendered


# ---------------------------------------------------------------------------
# 3. Token trim with Providers
# ---------------------------------------------------------------------------


class TestTokenTrimWithProviders:
    """P2 priority-based trimming correctly removes P3 provider sections first."""

    def test_low_priority_provider_sections_trimmed_first(self):
        counter = TokenCounter()

        prompt = StructuredPrompt()
        # Core section — priority 1, should never be trimmed
        prompt.add("Core Role", "You are an AI assistant." + "X" * 200, priority=1)
        # Medium section — priority 5
        prompt.add("Guidelines", "Follow these steps:" + "Y" * 200, priority=5)
        # Provider content — priority 7 (low, trimmed first)
        prompt.add("Dynamic Context", "Extra provider data:" + "Z" * 200, priority=7)

        total_before = prompt.total_tokens(counter)
        # Trim to roughly the size of core + guidelines only
        budget = total_before // 2
        prompt.trim_to(budget, counter)

        titles = [s.title for s in prompt.sections]
        assert "Core Role" in titles, "Priority-1 section should survive trimming"
        assert "Dynamic Context" not in titles, (
            "Priority-7 provider section should be trimmed first"
        )

    def test_priority_one_sections_never_trimmed(self):
        counter = TokenCounter()

        prompt = StructuredPrompt()
        prompt.add("System", "A" * 500, priority=1)
        prompt.add("Task", "B" * 500, priority=1)
        prompt.add("Examples", "C" * 500, priority=9)

        # Trim to a very small budget — only priority-1 sections survive
        prompt.trim_to(10, counter)

        for section in prompt.sections:
            assert section.priority == 1, (
                f"Section '{section.title}' has priority {section.priority}, should have been trimmed"
            )


# ---------------------------------------------------------------------------
# 4. Provider cross-stage data flow
# ---------------------------------------------------------------------------


class TestProviderCrossStageDataFlow:
    """P3 Provider data persists across stages: Stage A writes, Stage B reads."""

    def test_task_summary_provider_update_and_read(self):
        provider = TaskSummaryProvider()

        # Stage A (Planner) writes
        provider.update(["Research topic", "Write report", "Review findings"])

        # Stage B (Executor) reads
        context = provider.get_context()
        assert "Research topic" in context
        assert "Write report" in context
        assert "Review findings" in context

    def test_provider_via_registry_cross_stage(self):
        registry = ProviderRegistry()

        task_prov = TaskSummaryProvider()
        registry.register("tasks", task_prov)

        # Stage A updates
        task_prov.update(["Analyze code"])

        # Stage B reads via registry
        retrieved = registry.get("tasks")
        assert retrieved is not None
        assert "Analyze code" in retrieved.get_context()

    def test_expert_list_provider_cross_stage(self):
        experts_data = [
            {"name": "python-expert", "capabilities": ["python", "testing"]},
            {"name": "rust-expert", "capabilities": ["rust", "systems"]},
        ]
        provider = ExpertListProvider(experts_data)

        context = provider.get_context()
        assert "python-expert" in context
        assert "rust-expert" in context
        assert "python, testing" in context


# ---------------------------------------------------------------------------
# 5. ReflectionFeedbackProvider lifecycle
# ---------------------------------------------------------------------------


class TestReflectionFeedbackProviderLifecycle:
    """P4 Reflector writes feedback -> P3 Provider stores -> P2 Prompt consumes."""

    def test_full_lifecycle(self):
        provider = ReflectionFeedbackProvider()

        # Initially empty
        assert provider.get_context() == ""

        # Reflector updates feedback
        provider.update("Need more detail in section 2")
        assert "Need more detail" in provider.get_context()

        # Executor reads feedback in next round
        context = provider.get_context()
        assert context == "Need more detail in section 2"

        # Clear for next iteration
        provider.update("")
        assert provider.get_context() == ""

    def test_feedback_flows_into_structured_prompt(self):
        feedback = ReflectionFeedbackProvider()
        feedback.update("Add more test cases")

        prompt = StructuredPrompt()
        prompt.add("Role", "You are a developer.", priority=1)
        prompt.add_from_providers({"feedback": feedback}, priority=7)

        rendered = prompt.render()
        assert "Add more test cases" in rendered


# ---------------------------------------------------------------------------
# 6. ProviderRegistry priority
# ---------------------------------------------------------------------------


class TestProviderRegistryPriority:
    """P3 Registry priority system integrates with P2 StructuredPrompt priorities."""

    def test_default_priority_is_seven(self):
        registry = ProviderRegistry()
        registry.register("tasks", TaskSummaryProvider())

        assert registry.get_priority("tasks") == 7

    def test_custom_priorities(self):
        registry = ProviderRegistry()
        registry.register("core_tasks", TaskSummaryProvider(), priority=3)
        registry.register("experts", ExpertListProvider([]), priority=5)
        registry.register("feedback", ReflectionFeedbackProvider(), priority=9)

        assert registry.get_priority("core_tasks") == 3
        assert registry.get_priority("experts") == 5
        assert registry.get_priority("feedback") == 9

    def test_unregistered_provider_returns_default(self):
        registry = ProviderRegistry()
        assert registry.get_priority("nonexistent") == 7

    def test_unregister_removes_provider(self):
        registry = ProviderRegistry()
        registry.register("tasks", TaskSummaryProvider())
        assert registry.get("tasks") is not None

        registry.unregister("tasks")
        assert registry.get("tasks") is None
        assert registry.get_priority("tasks") == 7  # default after removal

    def test_providers_property_returns_copy(self):
        registry = ProviderRegistry()
        task_prov = TaskSummaryProvider()
        registry.register("tasks", task_prov)

        providers = registry.providers
        # Mutating the copy should not affect the registry
        providers["extra"] = MagicMock()
        assert "extra" not in registry.providers


# ---------------------------------------------------------------------------
# 7. StructuredPrompt add_from_providers integration
# ---------------------------------------------------------------------------


class TestStructuredPromptAddFromProviders:
    """P2 StructuredPrompt.add_from_providers correctly handles P3 providers."""

    def test_only_non_empty_providers_produce_sections(self):
        # Mix of empty and non-empty providers
        empty_task = TaskSummaryProvider()  # no tasks set -> empty
        feedback = ReflectionFeedbackProvider()
        feedback.update("Improve coverage")
        expert = ExpertListProvider([])  # empty list -> empty

        providers = {
            "tasks": empty_task,
            "feedback": feedback,
            "experts": expert,
        }

        prompt = StructuredPrompt()
        prompt.add_from_providers(providers, priority=7)

        # Only feedback provider has content
        assert len(prompt.sections) == 1
        assert prompt.sections[0].title == "改进建议"
        assert prompt.sections[0].priority == 7
        assert "Improve coverage" in prompt.sections[0].content

    def test_provider_sections_use_provider_title(self):
        task_prov = TaskSummaryProvider()
        task_prov.update(["Task 1"])

        prompt = StructuredPrompt()
        prompt.add_from_providers({"tasks": task_prov})

        # Section title should match the provider's title attribute
        assert prompt.sections[0].title == task_prov.title
