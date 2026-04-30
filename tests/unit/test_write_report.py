"""Tests for _write_report — output formatting and empty section filtering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent_nexus.platform.agency.integrator import IntegratedArtifact


def _make_result(
    merged_sections: dict[str, object],
    qa_passed: bool = True,
    task: str = "Test task",
) -> MagicMock:
    """Create a minimal TaskComposerResult-like object for testing."""
    result = MagicMock()
    result.task = task
    result.qa_passed = qa_passed
    result.selected_agents = [MagicMock(agent_id="expert-a")]
    result.skipped_tasks = []
    result.integrated = IntegratedArtifact(
        source_agents=["expert-a"],
        merged_sections=merged_sections,
    )
    return result


def test_write_report_skips_empty_sections(tmp_path: Path) -> None:
    from agent_nexus.platform.agency.cli import _write_report

    result = _make_result({
        "summary": "Rich content here",
        "context": "",
        "empty_list": [],
        "none_val": None,
        "real_section": "Actual content",
    })
    report_path = tmp_path / "report.md"
    _write_report(result, report_path)

    content = report_path.read_text()

    assert "## summary" in content
    assert "Rich content here" in content
    assert "## real_section" in content
    assert "Actual content" in content
    assert "## context" not in content
    assert "## empty_list" not in content
    assert "## none_val" not in content


def test_write_report_formats_list_as_bullets(tmp_path: Path) -> None:
    from agent_nexus.platform.agency.cli import _write_report

    result = _make_result({
        "recommendations": ["Use param queries", "Add rate limiting"],
    })
    report_path = tmp_path / "report.md"
    _write_report(result, report_path)

    content = report_path.read_text()

    assert "- Use param queries" in content
    assert "- Add rate limiting" in content
    assert "['Use param queries'" not in content


def test_write_report_formats_dict_as_kv(tmp_path: Path) -> None:
    from agent_nexus.platform.agency.cli import _write_report

    result = _make_result({
        "agent_assignments": {"frontend": "agent-a", "backend": "agent-b"},
    })
    report_path = tmp_path / "report.md"
    _write_report(result, report_path)

    content = report_path.read_text()

    assert "- **frontend**: agent-a" in content
    assert "- **backend**: agent-b" in content


def test_write_report_string_value_normal(tmp_path: Path) -> None:
    from agent_nexus.platform.agency.cli import _write_report

    result = _make_result({
        "proposed_design": "## Architecture\n\nUse microservices.",
    })
    report_path = tmp_path / "report.md"
    _write_report(result, report_path)

    content = report_path.read_text()
    assert "Use microservices" in content
