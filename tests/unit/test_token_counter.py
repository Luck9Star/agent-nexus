"""Tests for TokenCounter, TokenCountResult, PromptSection, StructuredPrompt."""

import pytest

from agent_nexus.platform.agency.token_counter import (
    PromptSection,
    StructuredPrompt,
    TokenCountResult,
    TokenCounter,
)


# ---------------------------------------------------------------------------
# TokenCounter
# ---------------------------------------------------------------------------


class TestTokenCounter:
    def test_count_empty_string(self):
        """Empty string returns 0 tokens."""
        tc = TokenCounter()
        assert tc.count("") == 0

    def test_count_nonempty_returns_positive(self):
        """Non-empty string returns a positive token count."""
        tc = TokenCounter()
        result = tc.count("Hello, world! This is a test.")
        assert result > 0

    def test_fallback_estimate_len_div_4(self):
        """Without tiktoken, count uses len/4 heuristic."""
        tc = TokenCounter()
        # Force fallback by pretending tiktoken unavailable
        tc._tiktoken_available = False
        text = "a" * 100  # 100 chars
        assert tc.count(text) == 25  # 100 // 4

    def test_fallback_minimum_1(self):
        """Fallback returns at least 1 token for non-empty text."""
        tc = TokenCounter()
        tc._tiktoken_available = False
        # 3 chars → 3//4 = 0 → max(1, 0) = 1
        assert tc.count("abc") == 1


# ---------------------------------------------------------------------------
# TokenCountResult
# ---------------------------------------------------------------------------


class TestTokenCountResult:
    def test_utilization_calculation(self):
        """utilization = total / max_tokens."""
        tcr = TokenCountResult(
            total=500,
            system_prompt=200,
            user_message=300,
            model="gpt-4",
            max_tokens=1000,
            utilization=0.5,
        )
        assert tcr.utilization == 0.5
        assert tcr.total == 500
        assert tcr.model == "gpt-4"

    def test_frozen_dataclass(self):
        """TokenCountResult is frozen (immutable)."""
        tcr = TokenCountResult(
            total=100,
            system_prompt=50,
            user_message=50,
            model="gpt-4",
            max_tokens=200,
            utilization=0.5,
        )
        with pytest.raises(AttributeError):
            tcr.total = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PromptSection
# ---------------------------------------------------------------------------


class TestPromptSection:
    def test_token_count_property(self):
        """token_count uses len/4 of title+content."""
        section = PromptSection(title="Title", content="A" * 40, priority=5)
        # title "Title" (5 chars) + "\n" + "A"*40 = 46 chars → 46//4 = 11
        assert section.token_count == 11

    def test_creation(self):
        section = PromptSection(title="Intro", content="Hello", priority=1)
        assert section.title == "Intro"
        assert section.content == "Hello"
        assert section.priority == 1


# ---------------------------------------------------------------------------
# StructuredPrompt
# ---------------------------------------------------------------------------


class TestStructuredPrompt:
    def test_add_skips_empty_content(self):
        """add() with empty content does not add a section."""
        sp = StructuredPrompt()
        sp.add("Empty", "", priority=5)
        sp.add("Whitespace", "   \n  ", priority=5)
        assert len(sp.sections) == 0

    def test_add_creates_section(self):
        sp = StructuredPrompt()
        sp.add("Task", "Do something", priority=2)
        assert len(sp.sections) == 1
        assert sp.sections[0].title == "Task"
        assert sp.sections[0].priority == 2

    def test_render_format(self):
        """render() produces '## Title\nContent' sections joined by double newlines."""
        sp = StructuredPrompt()
        sp.add("Section1", "Content A", priority=1)
        sp.add("Section2", "Content B", priority=3)
        rendered = sp.render()
        assert "## Section1\nContent A" in rendered
        assert "## Section2\nContent B" in rendered
        assert rendered.count("\n\n") == 1  # one separator between 2 sections

    def test_total_tokens(self):
        """total_tokens sums across all sections."""
        sp = StructuredPrompt()
        sp.add("A", "x" * 40, priority=1)
        sp.add("B", "y" * 40, priority=5)
        tc = TokenCounter()
        tc._tiktoken_available = False  # Force len/4
        total = sp.total_tokens(tc)
        # Each section: title (1 char) + "\n" + 40 chars = 42 → 42//4 = 10
        assert total == 20

    def test_trim_to_removes_lowest_priority_first(self):
        """trim_to removes sections with highest priority number first."""
        tc = TokenCounter()
        tc._tiktoken_available = False  # Force len/4 for deterministic math

        sp = StructuredPrompt()
        # Each section: title + "\n" + 100 chars → ~26 tokens with fallback
        sp.add("Core", "x" * 100, priority=1)
        sp.add("Medium", "y" * 100, priority=5)
        sp.add("Low", "z" * 100, priority=9)

        total = sp.total_tokens(tc)
        # Trim to keep only ~52 tokens (2 sections worth)
        sp.trim_to(52, tc)

        assert len(sp.sections) == 2
        # Priority 9 (Low) should be removed first
        titles = [s.title for s in sp.sections]
        assert "Low" not in titles
        assert "Core" in titles
        assert "Medium" in titles

    def test_trim_to_never_removes_priority_1(self):
        """Priority-1 sections are never trimmed."""
        sp = StructuredPrompt()
        sp.add("Essential", "x" * 200, priority=1)  # 201 chars → 50 tokens
        sp.add("Extra", "y" * 200, priority=9)  # 201 chars → 50 tokens

        tc = TokenCounter()
        tc._tiktoken_available = False

        # Trim to 0 — should remove Extra, keep Essential
        sp.trim_to(0, tc)

        assert len(sp.sections) == 1
        assert sp.sections[0].title == "Essential"
        assert sp.sections[0].priority == 1

    def test_trim_to_preserves_order(self):
        """After trimming, remaining sections keep original insertion order."""
        sp = StructuredPrompt()
        sp.add("First", "a" * 100, priority=1)
        sp.add("Second", "b" * 100, priority=5)
        sp.add("Third", "c" * 100, priority=1)

        tc = TokenCounter()
        tc._tiktoken_available = False

        sp.trim_to(50, tc)  # Removes "Second" (priority 5)

        titles = [s.title for s in sp.sections]
        assert titles == ["First", "Third"]

    def test_add_from_providers(self):
        """add_from_providers creates sections from provider-like objects."""
        sp = StructuredPrompt()

        class FakeProvider:
            title = "Context"
            def get_context(self):
                return "some context"

        sp.add_from_providers({"ctx": FakeProvider()})
        assert len(sp.sections) == 1
        assert sp.sections[0].title == "Context"
        assert sp.sections[0].content == "some context"
        assert sp.sections[0].priority == 7  # default for providers

    def test_add_from_providers_skips_empty(self):
        """Providers with empty context are skipped."""
        sp = StructuredPrompt()

        class EmptyProvider:
            title = "Empty"
            def get_context(self):
                return ""

        sp.add_from_providers({"e": EmptyProvider()})
        assert len(sp.sections) == 0
