"""Tests for prompt_loader module — template loading and rendering."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_nexus.platform.agency.prompt_loader import load, render


class TestPromptLoaderLoad:
    """load() reads and caches prompt templates."""

    def test_load_planner_template(self) -> None:
        template = load("planner")
        assert template is not None
        # The template should have some content
        assert len(template.template) > 0

    def test_load_caches_result(self) -> None:
        t1 = load("integrator")
        t2 = load("integrator")
        assert t1 is t2

    def test_load_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load("nonexistent_template_xyz")


class TestPromptLoaderRender:
    """render() loads a template and substitutes variables."""

    def test_render_with_variables(self) -> None:
        from string import Template

        mock_template = Template("Hello $who, your task is $action")
        with patch("agent_nexus.platform.agency.prompt_loader.load", return_value=mock_template):
            result = render("test", who="Agent", action="review code")
        assert "Hello Agent" in result
        assert "review code" in result
