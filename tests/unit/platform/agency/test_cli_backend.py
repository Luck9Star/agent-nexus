"""Tests for agency CLI backend functions: path validation, pipeline, executor, etc.

Focuses on high-risk, security-critical, and resource-management functions
that lack dedicated unit test coverage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.platform.agency.cli import (
    _create_executor,
    _execute_pipeline,
    _handle_output,
    _load_experts,
    _validate_output_path,
    _write_report,
)

# ===================================================================
# _validate_output_path — path traversal prevention (SECURITY)
# ===================================================================


class TestValidateOutputPath:
    """Tests for _validate_output_path — blocks path traversal attacks."""

    def test_blocks_etc_traversal(self) -> None:
        """Paths under /etc are rejected."""
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(Path("/etc/passwd"))

    def test_blocks_usr_traversal(self) -> None:
        """Paths under /usr are rejected."""
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(Path("/usr/local/bin/evil"))

    def test_blocks_ssh_dir(self) -> None:
        """Paths under ~/.ssh are rejected."""
        home = Path.home()
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(home / ".ssh" / "authorized_keys")

    def test_blocks_aws_dir(self) -> None:
        """Paths under ~/.aws are rejected."""
        home = Path.home()
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(home / ".aws" / "credentials")

    def test_blocks_gnupg_dir(self) -> None:
        """Paths under ~/.gnupg are rejected."""
        home = Path.home()
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(home / ".gnupg" / "secring.gpg")

    def test_blocks_dotdot_segments(self) -> None:
        """Paths containing '..' segments are rejected outright."""
        with pytest.raises(ValueError, match=r"\.\."):
            _validate_output_path(Path("../../../etc/passwd"))

    def test_blocks_dotdot_in_middle(self) -> None:
        """Paths with '..' in the middle are rejected."""
        with pytest.raises(ValueError, match=r"\.\."):
            _validate_output_path(Path("/tmp/safe/../../../etc/passwd"))

    def test_accepts_valid_tmp_path(self, tmp_path: Path) -> None:
        """Normal paths under tmp_path are accepted and resolved."""
        target = tmp_path / "report.md"
        result = _validate_output_path(target)
        assert result == target.resolve()

    def test_accepts_cwd_relative_path(self) -> None:
        """Relative paths without '..' that don't resolve to sensitive dirs are ok."""
        result = _validate_output_path(Path("my-report.md"))
        # Should resolve to cwd + my-report.md — not in sensitive dirs
        assert result.name == "my-report.md"

    def test_symlink_traversal_blocked(self, tmp_path: Path) -> None:
        """Symlinks pointing to blocked dirs are caught after resolve."""
        link = tmp_path / "evil_link"
        link.symlink_to("/etc")
        with pytest.raises(ValueError, match="sensitive location"):
            _validate_output_path(link / "passwd")

    def test_symlink_to_tmp_is_ok(self, tmp_path: Path) -> None:
        """Symlinks to safe dirs are allowed."""
        real_dir = tmp_path / "real_output"
        real_dir.mkdir()
        link = tmp_path / "link_output"
        link.symlink_to(real_dir)
        target = link / "report.md"
        result = _validate_output_path(target)
        # Resolves through symlink to the real dir
        assert str(result).startswith(str(real_dir.resolve()))

    def test_returns_resolved_path(self, tmp_path: Path) -> None:
        """Return value is always a resolved (absolute, no symlinks) path."""
        target = tmp_path / "output.md"
        result = _validate_output_path(target)
        assert result.is_absolute()


# ===================================================================
# _execute_pipeline — pipeline orchestration + resource cleanup
# ===================================================================


class TestExecutePipeline:
    """Tests for _execute_pipeline — TaskGraph context manager + cleanup."""

    def _make_mock_result(self) -> MagicMock:
        result = MagicMock()
        result.task = "test task"
        result.selected_agents = []
        result.qa_passed = True
        result.skipped_tasks = []
        return result

    @patch("agent_nexus.platform.orchestration.task_graph.TaskGraph")
    @patch("agent_nexus.platform.agency.task_composer.TaskComposer")
    def test_closes_llm_executor_on_success(self, mock_composer_cls, mock_graph_cls) -> None:
        """LLM executor.close() is called when is_llm=True."""
        mock_graph_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_graph_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_composer_cls.return_value.run.return_value = self._make_mock_result()

        executor = MagicMock()
        _execute_pipeline(
            message="test",
            mode="plan",
            max_parallel=3,
            timeout=60,
            reasoning_protocol=False,
            registry=MagicMock(),
            executor=executor,
            is_llm=True,
            llm_planner=None,
            llm_integrator=None,
            llm_qa_gate=None,
        )
        executor.close.assert_called_once()


# ===================================================================
# _create_executor — executor factory with LLM fallback
# ===================================================================


class TestCreateExecutor:
    """Tests for _create_executor — LLM first, fallback to profile-based."""

    @patch("agent_nexus.platform.agency.executor.ProfileBasedExecutor")
    @patch("agent_nexus.platform.agency.executor.LLMExecutor")
    def test_fallback_on_llm_failure(self, mock_llm_cls, mock_profile_cls) -> None:
        """When LLM init fails, falls back to ProfileBasedExecutor."""
        mock_llm_cls.side_effect = RuntimeError("no API key")
        mock_profile = MagicMock()
        mock_profile_cls.return_value = mock_profile

        executor, is_llm = _create_executor(
            model="api:model",
            config_dir=None,
            temperature=None,
            registry=MagicMock(),
            shared_registry=None,
            shared_client=None,
            effective_call_timeout=30.0,
            reasoning_protocol=False,
        )
        assert is_llm is False
        assert executor is mock_profile
        mock_profile_cls.assert_called_once()


# ===================================================================
# _load_experts — expert loading via AgencyImporter
# ===================================================================


class TestLoadExperts:
    """Tests for _load_experts — import + registry population."""

    @patch("agent_nexus.platform.agency.cli.ExpertRegistry")
    @patch("agent_nexus.platform.agency.cli.AgencyImporter")
    def test_returns_populated_registry(self, mock_importer_cls, mock_registry_cls) -> None:
        """Registry is populated with all dry_run profiles."""
        mock_importer = MagicMock()
        mock_importer.dry_run.return_value = [
            {
                "expert_profile": {
                    "id": "agency.architect",
                    "name": "Architect",
                    "capabilities": ["system_design"],
                },
            },
            {
                "expert_profile": {
                    "id": "agency.reviewer",
                    "name": "Reviewer",
                    "capabilities": ["code_review"],
                },
            },
        ]
        mock_importer_cls.return_value = mock_importer
        mock_registry = MagicMock()
        mock_registry_cls.return_value = mock_registry

        result = _load_experts("/vendor", "/allowlist")
        assert result is mock_registry
        assert mock_registry.add.call_count == 2

    @patch("agent_nexus.platform.agency.cli.ExpertRegistry")
    @patch("agent_nexus.platform.agency.cli.AgencyImporter")
    def test_cleans_up_tmpdir_on_success(self, mock_importer_cls, mock_registry_cls) -> None:
        """Temporary directory is cleaned up after successful dry_run."""
        mock_importer = MagicMock()
        mock_importer.dry_run.return_value = []
        mock_importer_cls.return_value = mock_importer
        mock_registry_cls.return_value = MagicMock()

        with patch("agent_nexus.platform.agency.cli.shutil.rmtree") as mock_rmtree:
            _load_experts("/vendor", "/allowlist")
            mock_rmtree.assert_called_once()

    @patch("agent_nexus.platform.agency.cli.ExpertRegistry")
    @patch("agent_nexus.platform.agency.cli.AgencyImporter")
    def test_cleans_up_tmpdir_on_failure(self, mock_importer_cls, mock_registry_cls) -> None:
        """Temporary directory is cleaned up even when dry_run raises."""
        mock_importer = MagicMock()
        mock_importer.dry_run.side_effect = FileNotFoundError("missing")
        mock_importer_cls.return_value = mock_importer

        with patch("agent_nexus.platform.agency.cli.shutil.rmtree") as mock_rmtree:
            with pytest.raises(FileNotFoundError):
                _load_experts("/vendor", "/allowlist")
            mock_rmtree.assert_called_once()


# ===================================================================
# _handle_output — output routing (file vs stdout)
# ===================================================================


class TestHandleOutput:
    """Tests for _handle_output — dispatches to file or stdout."""

    def test_none_output_calls_print_result(self) -> None:
        """output_target=None delegates to _print_result."""
        result = MagicMock()
        result.output_target = None

        with patch("agent_nexus.platform.agency.cli._print_result") as mock_print:
            _handle_output(result)
            mock_print.assert_called_once_with(result)


# ===================================================================
# _write_report — markdown report generation
# ===================================================================


class TestWriteReport:
    """Tests for _write_report — writes markdown to disk."""

    def _make_result(
        self, *, integrated: bool = True, skipped: list[str] | None = None
    ) -> MagicMock:
        result = MagicMock()
        result.task = "Design architecture"
        result.qa_passed = True
        result.selected_agents = [MagicMock(agent_id="agency.architect")]
        result.skipped_tasks = skipped or []
        if integrated:
            integrated = MagicMock()
            integrated.merged_sections = {
                "context": "System design",
                "findings": ["Finding A", "Finding B"],
            }
            result.integrated = integrated
        else:
            result.integrated = None
        return result

    def test_creates_valid_markdown(self, tmp_path: Path) -> None:
        """Output file contains valid markdown with expected sections."""
        out_path = tmp_path / "report.md"
        result = self._make_result()
        _write_report(result, out_path)

        content = out_path.read_text()
        assert "# Composition Report" in content
        assert "**Task**: Design architecture" in content
        assert "**QA passed**: True" in content
        assert "## context" in content
        assert "## findings" in content

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Missing parent directories are created automatically."""
        out_path = tmp_path / "deep" / "nested" / "report.md"
        result = self._make_result()
        _write_report(result, out_path)
        assert out_path.exists()

    def test_handles_no_integrated_artifact(self, tmp_path: Path) -> None:
        """When integrated is None, reports 'all experts failed'."""
        out_path = tmp_path / "empty.md"
        result = self._make_result(integrated=False)
        _write_report(result, out_path)

        content = out_path.read_text()
        assert "No artifacts produced" in content

    def test_includes_skipped_tasks(self, tmp_path: Path) -> None:
        """Skipped tasks are listed in the report."""
        out_path = tmp_path / "skipped.md"
        result = self._make_result(skipped=["agency.test1", "agency.test2"])
        _write_report(result, out_path)

        content = out_path.read_text()
        assert "agency.test1" in content
        assert "agency.test2" in content
        assert "**Skipped**" in content

    def test_file_encoding_is_utf8(self, tmp_path: Path) -> None:
        """Report is written in UTF-8 encoding (supports Chinese characters)."""
        result = self._make_result()
        result.task = "设计架构"
        out_path = tmp_path / "chinese.md"
        _write_report(result, out_path)

        content = out_path.read_text(encoding="utf-8")
        assert "设计架构" in content
