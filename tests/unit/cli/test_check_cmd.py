"""Tests for check_cmd helper functions."""
from __future__ import annotations

from agent_nexus.platform.local.cli.check_cmd import (
    _check_atomic_files,
    _check_composite_files,
    _check_manifest,
    _check_path_exists,
    _check_pyproject,
    _check_skill_md,
    _detect_composition_cycles,
    _validate_composition_sections,
    _validate_composition_tasks,
)


class TestCheckPathExists:
    def test_nonexistent_path(self, tmp_path):
        result = _check_path_exists(tmp_path / "nope")
        assert len(result) == 1
        assert "does not exist" in result[0]

    def test_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = _check_path_exists(f)
        assert len(result) == 1
        assert "not a directory" in result[0]

    def test_valid_dir(self, tmp_path):
        assert _check_path_exists(tmp_path) == []


class TestCheckManifest:
    def test_missing_manifest(self, tmp_path):
        errors, data = _check_manifest(tmp_path, "myagent")
        assert errors == ["Missing agent.toml or agent-manifest.yaml"]
        assert data is None

    def test_valid_manifest(self, tmp_path):
        manifest = tmp_path / "agent-manifest.yaml"
        manifest.write_text(
            "name: myagent\nversion: '1.0'\ntype: atomic\ndescription: test\n"
        )
        errors, data = _check_manifest(tmp_path, "myagent")
        assert errors == []
        assert data is not None
        assert data["name"] == "myagent"

    def test_name_mismatch(self, tmp_path):
        manifest = tmp_path / "agent-manifest.yaml"
        manifest.write_text(
            "name: other\nversion: '1.0'\ntype: atomic\ndescription: test\n"
        )
        errors, _ = _check_manifest(tmp_path, "myagent")
        assert any("does not match" in e for e in errors)

    def test_missing_fields(self, tmp_path):
        manifest = tmp_path / "agent-manifest.yaml"
        manifest.write_text("name: myagent\n")
        errors, _ = _check_manifest(tmp_path, "myagent")
        assert any("version" in e for e in errors)


class TestCheckSkillMd:
    def test_missing(self, tmp_path):
        assert "Missing SKILL.md" in _check_skill_md(tmp_path)

    def test_no_frontmatter(self, tmp_path):
        (tmp_path / "SKILL.md").write_text("hello")
        errors = _check_skill_md(tmp_path)
        assert any("frontmatter" in e for e in errors)

    def test_valid(self, tmp_path):
        (tmp_path / "SKILL.md").write_text("---\nname: test\n---\nbody")
        assert _check_skill_md(tmp_path) == []


class TestCheckPyproject:
    def test_missing(self, tmp_path):
        assert "Missing pyproject.toml" in _check_pyproject(tmp_path)

    def test_valid(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert _check_pyproject(tmp_path) == []

    def test_invalid_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[invalid{{{")
        errors = _check_pyproject(tmp_path)
        assert any("parse error" in e for e in errors)


class TestCheckAtomicFiles:
    def test_missing_agent_py(self, tmp_path):
        assert any("agent.py" in e for e in _check_atomic_files(tmp_path))

    def test_present(self, tmp_path):
        (tmp_path / "agent.py").write_text("pass")
        assert _check_atomic_files(tmp_path) == []


class TestValidateCompositionSections:
    def test_both_present(self):
        errors: list[str] = []
        _validate_composition_sections({"composition": {}, "tasks": {}}, errors)
        assert errors == []

    def test_missing_composition(self):
        errors: list[str] = []
        _validate_composition_sections({"tasks": {}}, errors)
        assert any("composition" in e for e in errors)

    def test_missing_tasks(self):
        errors: list[str] = []
        _validate_composition_sections({"composition": {}}, errors)
        assert any("tasks" in e for e in errors)


class TestValidateCompositionTasks:
    def test_valid_tasks(self):
        errors: list[str] = []
        _validate_composition_tasks(
            {"tasks": {"t1": {"name": "a", "agent": "b"}, "t2": {"name": "c", "agent": "d"}}},
            errors,
        )
        assert errors == []

    def test_missing_name(self):
        errors: list[str] = []
        _validate_composition_tasks(
            {"tasks": {"t1": {"agent": "b"}}}, errors,
        )
        assert any("missing" in e and "name" in e for e in errors)

    def test_missing_agent(self):
        errors: list[str] = []
        _validate_composition_tasks(
            {"tasks": {"t1": {"name": "a"}}}, errors,
        )
        assert any("missing" in e and "agent" in e for e in errors)

    def test_self_dependency(self):
        errors: list[str] = []
        _validate_composition_tasks(
            {"tasks": {"t1": {"name": "a", "agent": "b", "blocked_by": ["t1"]}}}, errors,
        )
        assert any("cannot depend on itself" in e for e in errors)

    def test_no_tasks_key(self):
        errors: list[str] = []
        _validate_composition_tasks({}, errors)
        assert errors == []

    def test_tasks_not_dict(self):
        errors: list[str] = []
        _validate_composition_tasks({"tasks": "not a dict"}, errors)
        assert errors == []


class TestDetectCompositionCycles:
    def test_no_cycle(self):
        errors: list[str] = []
        _detect_composition_cycles(
            {"t1": {"blocked_by": []}, "t2": {"blocked_by": ["t1"]}}, errors,
        )
        assert errors == []

    def test_simple_cycle(self):
        errors: list[str] = []
        _detect_composition_cycles(
            {"t1": {"blocked_by": ["t2"]}, "t2": {"blocked_by": ["t1"]}}, errors,
        )
        assert any("circular" in e for e in errors)


class TestCheckCompositeFiles:
    def test_missing_composition_toml(self, tmp_path):
        errors = _check_composite_files(tmp_path)
        assert any("composition.toml" in e for e in errors)

    def test_valid_composition(self, tmp_path):
        comp = tmp_path / "composition.toml"
        comp.write_text(
            '[composition]\nname = "test"\n\n[tasks.t1]\nname = "a"\nagent = "b"\n',
        )
        errors = _check_composite_files(tmp_path)
        assert errors == []

    def test_missing_sections(self, tmp_path):
        comp = tmp_path / "composition.toml"
        comp.write_text("[other]\nkey = 'val'\n")
        errors = _check_composite_files(tmp_path)
        assert any("composition" in e for e in errors)
        assert any("tasks" in e for e in errors)
