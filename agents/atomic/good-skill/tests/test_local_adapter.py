"""Tests for good-skill local adapter.

Covers message dispatch for the run method and error handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent_good_skill.agent import GoodSkillAgent
from agent_good_skill.local_adapter import handle_message


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_run(self) -> None:
        agent = GoodSkillAgent()
        with patch.object(
            agent, "run", new_callable=AsyncMock, return_value="result text"
        ):
            response = handle_message(
                agent,
                {"method": "run", "params": {"task": "do something"}},
            )
        assert response["status"] == "ok"
        assert response["result"]["output"] == "result text"

    def test_handle_run_with_context(self) -> None:
        agent = GoodSkillAgent()
        with patch.object(
            agent, "run", new_callable=AsyncMock, return_value="context result"
        ):
            response = handle_message(
                agent,
                {"method": "run", "params": {"task": "test", "context": {"k": "v"}}},
            )
        assert response["status"] == "ok"

    def test_handle_unknown_method(self) -> None:
        agent = GoodSkillAgent()
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_task(self) -> None:
        agent = GoodSkillAgent()
        response = handle_message(agent, {"method": "run", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]
