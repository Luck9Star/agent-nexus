"""AgentCreator — generate agent skeletons with validated capabilities.

Creates a complete agent package directory:
  - agent.toml (with provided name, type, capabilities)
  - SKILL.md (basic template)
  - src/{pkg}/__init__.py
  - tests/test_{name}.py
  - pyproject.toml (minimal)

Design spec: docs/roadmap/p1-4 Phase 1 — Capability Taxonomy + Scaffolding CLI.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from agent_nexus.platform.local.manifest import load_manifest
from agent_nexus.platform.utils import AGENT_NAME_RE, agent_name_to_package, atomic_write

logger = logging.getLogger(__name__)

# Path to the bundled capabilities taxonomy.
_CAPABILITIES_TOML = Path(__file__).parent / "capabilities.toml"

# Valid agent types.
_VALID_TYPES = {"atomic", "composite"}


class AgentCreatorError(Exception):
    """Raised when agent creation fails due to invalid input or I/O errors."""


class AgentCreator:
    """Generate agent skeleton directories with validated capabilities.

    Parameters
    ----------
    capabilities_path:
        Path to a capabilities taxonomy TOML file.  Defaults to the
        bundled ``capabilities.toml`` shipped with the platform.
    """

    def __init__(self, capabilities_path: Path | None = None) -> None:
        self._capabilities_path = capabilities_path or _CAPABILITIES_TOML
        self._taxonomy = self._load_taxonomy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        type: str = "atomic",
        capabilities: list[str] | None = None,
        description: str | None = None,
        output_dir: Path | None = None,
        template: str | None = None,
    ) -> Path:
        """Generate an agent skeleton.

        Parameters
        ----------
        name:
            Agent name (kebab-case, e.g. ``my-agent``).
        type:
            ``"atomic"`` or ``"composite"``.
        capabilities:
            List of capabilities from the taxonomy.  ``None`` means none declared.
        description:
            Human-readable description.  Defaults to a placeholder.
        output_dir:
            Parent directory for the agent.  Defaults to ``agents/{type}/``.
        template:
            Optional template name (reserved for future use).

        Returns
        -------
        Path
            The created agent root directory.

        Raises
        ------
        AgentCreatorError
            Invalid name, type, or capabilities.
        FileExistsError
            Target directory already exists.
        """
        self._validate_name(name)
        self._validate_type(type)
        caps = self._validate_capabilities(capabilities or [])

        desc = description or f"{name} agent"

        base = output_dir or Path.cwd() / "agents" / type
        agent_dir = base / name
        pkg = agent_name_to_package(name)
        pkg_dir = agent_dir / "src" / pkg

        if agent_dir.exists():
            raise FileExistsError(f"Agent directory already exists: {agent_dir}")

        pkg_dir.mkdir(parents=True, exist_ok=True)
        tests_dir = agent_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        files: dict[Path, str] = {
            agent_dir / "agent.toml": self._gen_manifest_toml(name, type, desc, caps),
            agent_dir / "SKILL.md": self._gen_skill_md(name, desc, caps),
            pkg_dir / "__init__.py": self._gen_pkg_init(name),
            tests_dir / f"test_{name.replace('-', '_')}.py": self._gen_test(name),
            agent_dir / "pyproject.toml": self._gen_pyproject(name, pkg),
        }

        for path, content in files.items():
            atomic_write(path, content)

        # Validate round-trip: load the generated agent.toml via manifest.py
        load_manifest(agent_dir)

        logger.info("Created agent %r at %s", name, agent_dir)
        return agent_dir

    # ------------------------------------------------------------------
    # Taxonomy helpers
    # ------------------------------------------------------------------

    def _load_taxonomy(self) -> dict[str, Any]:
        """Load and return the capabilities taxonomy."""
        if not self._capabilities_path.is_file():
            raise AgentCreatorError(f"Capabilities taxonomy not found: {self._capabilities_path}")
        with open(self._capabilities_path, "rb") as f:
            return tomllib.load(f)

    def get_valid_capabilities(self) -> list[str]:
        """Return the list of all valid capability keys."""
        return sorted(self._taxonomy.get("capabilities", {}).keys())

    def get_valid_categories(self) -> list[str]:
        """Return the list of all valid category keys."""
        return sorted(self._taxonomy.get("categories", {}).keys())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        if not AGENT_NAME_RE.match(name):
            raise AgentCreatorError(
                f"Invalid agent name {name!r}. "
                "Must start with alphanumeric, then alphanumeric/hyphen/underscore."
            )

    @staticmethod
    def _validate_type(type: str) -> None:
        if type not in _VALID_TYPES:
            raise AgentCreatorError(
                f"Invalid agent type {type!r}. Must be one of: {', '.join(sorted(_VALID_TYPES))}"
            )

    def _validate_capabilities(self, capabilities: list[str]) -> list[str]:
        valid = set(self._taxonomy.get("capabilities", {}).keys())
        unknown = [c for c in capabilities if c not in valid]
        if unknown:
            raise AgentCreatorError(
                f"Unknown capabilities: {', '.join(unknown)}. Valid: {', '.join(sorted(valid))}"
            )
        return list(capabilities)

    # ------------------------------------------------------------------
    # File generators
    # ------------------------------------------------------------------

    @staticmethod
    def _gen_manifest_toml(name: str, type: str, description: str, capabilities: list[str]) -> str:
        """Generate agent.toml content."""
        caps_str = ", ".join(f'"{c}"' for c in capabilities)
        return (
            f"[agent]\n"
            f'name = "{name}"\n'
            f'version = "0.1.0"\n'
            f'type = "{type}"\n'
            f'description = "{description}"\n'
            f"capabilities = [{caps_str}]\n"
        )

    @staticmethod
    def _gen_skill_md(name: str, description: str, capabilities: list[str]) -> str:
        """Generate SKILL.md content."""
        lines = [
            f"# {name}",
            "",
            f"{description}",
            "",
            "## Capabilities",
            "",
        ]
        if capabilities:
            for cap in capabilities:
                lines.append(f"- {cap}")
        else:
            lines.append("- (none declared)")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _gen_pkg_init(name: str) -> str:
        """Generate src/{pkg}/__init__.py content."""
        return f'"""agent-{name} package."""\n'

    @staticmethod
    def _gen_test(name: str) -> str:
        """Generate tests/test_{name}.py content."""
        return (
            f'"""Unit tests for {name} agent."""\n'
            f"\n"
            f"from pathlib import Path\n"
            f"\n"
            f"import pytest\n"
            f"\n"
            f"from agent_nexus.platform.local.manifest import load_manifest\n"
            f"\n"
            f"\n"
            f"def test_manifest_loads():\n"
            f'    """agent.toml should be loadable via manifest.py."""\n'
            f"    agent_dir = Path(__file__).resolve().parent.parent\n"
            f"    manifest = load_manifest(agent_dir)\n"
            f'    assert manifest.name == "{name}"\n'
        )

    @staticmethod
    def _gen_pyproject(name: str, pkg: str) -> str:
        """Generate minimal pyproject.toml."""
        return (
            f"[project]\n"
            f'name = "agent-{name}"\n'
            f'version = "0.1.0"\n'
            f'description = "TODO: agent description"\n'
            f'requires-python = ">=3.12"\n'
            f"dependencies = [\n"
            f'    "pydantic>=2.0",\n'
            f"]\n"
            f"\n"
            f"[build-system]\n"
            f'requires = ["hatchling"]\n'
            f'build-backend = "hatchling.build"\n'
            f"\n"
            f"[tool.hatch.build.targets.wheel]\n"
            f'packages = ["src/{pkg}"]\n'
        )
