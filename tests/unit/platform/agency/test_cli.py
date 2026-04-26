"""Tests for agency CLI commands."""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from agent_nexus.platform.agency.cli import cli
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


@pytest.mark.timeout(30)
class TestImportExpertsCommand:
    """import-experts command: import agency-agents from vendor repo."""

    def test_import_experts_dry_run(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                [
                    "import-experts",
                    "--vendor-path", str(_VENDOR_DIR),
                    "--allowlist", str(_ALLOWLIST_PATH),
                    "--output-dir", tmpdir,
                    "--dry-run",
                ],
            )
            assert result.exit_code == 0, f"CLI error: {result.output}"
            assert "12" in result.output  # Must show exactly 12 profiles loaded

    def test_import_experts_writes_files(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                [
                    "import-experts",
                    "--vendor-path", str(_VENDOR_DIR),
                    "--allowlist", str(_ALLOWLIST_PATH),
                    "--output-dir", tmpdir,
                ],
            )
            assert result.exit_code == 0, f"CLI error: {result.output}"
            # Check that JSON profile files were written
            json_files = list(Path(tmpdir).glob("*.json"))
            assert len(json_files) >= 10, f"Expected >= 10 JSON files, got {len(json_files)}"


@pytest.mark.timeout(30)
class TestPlanCompositionCommand:
    """plan-composition command: generate DAG from a task description."""

    def test_plan_composition_basic(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "plan-composition",
                "--task", "Design a system architecture",
                "--mode", "plan",
                "--vendor-path", str(_VENDOR_DIR),
                "--allowlist", str(_ALLOWLIST_PATH),
            ],
        )
        assert result.exit_code == 0, f"CLI error: {result.output}"
        assert "[composition]" in result.output or "integrate" in result.output


@pytest.mark.timeout(30)
class TestValidateOutputCommand:
    """validate-output command: validate an output file against a contract."""

    def test_validate_output_passes(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "output.json"
        output_file.write_text(json.dumps({
            "sections": {
                "context": "test",
                "assumptions": [],
                "proposed_design": "test",
                "tradeoffs": [],
                "risks": [],
                "next_steps": [],
            }
        }))
        result = runner.invoke(
            cli,
            [
                "validate-output",
                "--output-file", str(output_file),
                "--required-sections", "context,assumptions,proposed_design,tradeoffs,risks,next_steps",
            ],
        )
        assert result.exit_code == 0, f"CLI error: {result.output}"
        assert "passed" in result.output.lower() or "valid" in result.output.lower()

    def test_validate_output_fails_missing_sections(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "output.json"
        output_file.write_text(json.dumps({"sections": {"context": "test"}}))
        result = runner.invoke(
            cli,
            [
                "validate-output",
                "--output-file", str(output_file),
                "--required-sections", "context,risks,next_steps",
            ],
        )
        assert result.exit_code != 0 or "missing" in result.output.lower()

    def test_validate_output_with_task_type(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "output.json"
        output_file.write_text(json.dumps({"sections": {"summary": "test"}}))
        result = runner.invoke(
            cli,
            [
                "validate-output",
                "--output-file", str(output_file),
                "--required-sections", "summary",
                "--task-type", "code_change",
            ],
        )
        # code_change should trigger GitNexus gate failure
        assert "gitnexus" in result.output.lower() or result.exit_code != 0 or "failed" in result.output.lower()


@pytest.mark.timeout(30)
class TestListExpertsCommand:
    """list-experts command: preview experts available for import."""

    def test_list_experts_shows_profiles(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "list-experts",
                "--vendor-path", str(_VENDOR_DIR),
                "--allowlist", str(_ALLOWLIST_PATH),
            ],
        )
        assert result.exit_code == 0, f"CLI error: {result.output}"
        assert "12" in result.output or "experts" in result.output.lower()
        # Should show expert IDs
        assert "agency." in result.output

    def test_list_experts_shows_capabilities(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "list-experts",
                "--vendor-path", str(_VENDOR_DIR),
                "--allowlist", str(_ALLOWLIST_PATH),
            ],
        )
        assert result.exit_code == 0, f"CLI error: {result.output}"
        assert "Capabilities:" in result.output
        assert "Output contract:" in result.output

    def test_list_experts_bad_vendor_path(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "list-experts",
                "--vendor-path", "/nonexistent/path",
                "--allowlist", str(_ALLOWLIST_PATH),
            ],
        )
        assert result.exit_code != 0


@pytest.mark.timeout(30)
class TestCheckProfilesCommand:
    """check-profiles command: validate imported profile JSON files."""

    def test_check_valid_profiles(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            # First import profiles
            import_result = runner.invoke(
                cli,
                [
                    "import-experts",
                    "--vendor-path", str(_VENDOR_DIR),
                    "--allowlist", str(_ALLOWLIST_PATH),
                    "--output-dir", tmpdir,
                ],
            )
            assert import_result.exit_code == 0

            # Then check them
            check_result = runner.invoke(
                cli,
                ["check-profiles", "--output-dir", tmpdir],
            )
            assert check_result.exit_code == 0, f"Check failed: {check_result.output}"
            assert "passed" in check_result.output.lower()

    def test_check_empty_directory(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                ["check-profiles", "--output-dir", tmpdir],
            )
            assert result.exit_code == 0
            assert "no profile" in result.output.lower() or "not found" in result.output.lower()

    def test_check_invalid_json(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write invalid JSON
            bad_file = Path(tmpdir) / "bad.json"
            bad_file.write_text("not valid json{{{")
            result = runner.invoke(
                cli,
                ["check-profiles", "--output-dir", tmpdir],
            )
            assert result.exit_code != 0 or "failed" in result.output.lower()

    def test_check_nonexistent_directory(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["check-profiles", "--output-dir", "/nonexistent/path"],
        )
        assert result.exit_code != 0
