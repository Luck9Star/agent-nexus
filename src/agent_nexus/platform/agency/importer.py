"""Agency importer: orchestrates parsing, policy checks, and profile generation."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from .allowlist import load_allowlist
from .parser import parse_frontmatter
from .policy import check_content_policy

# Default output contract section mappings by artifact_type
_CONTRACT_SECTIONS: dict[str, list[str]] = {
    "architecture_plan": ["context", "assumptions", "proposed_design", "tradeoffs", "risks", "next_steps"],
    "technical_report": ["summary", "methodology", "findings", "recommendations"],
    "review_report": ["summary", "findings", "severity", "recommendations"],
    "risk_report": ["findings", "severity", "affected_components", "mitigation"],
    "reliability_report": ["summary", "incident_analysis", "recommendations", "slo_assessment"],
    "test_analysis_report": ["summary", "test_coverage", "failures", "recommendations"],
    "documentation": ["overview", "usage", "api_reference", "examples"],
    "onboarding_guide": ["overview", "architecture", "getting_started", "key_concepts"],
    "evaluation_report": ["summary", "criteria", "scores", "recommendation"],
    "index_report": ["summary", "index_structure", "coverage", "recommendations"],
    "orchestration_plan": ["objective", "task_decomposition", "agent_assignments", "execution_order"],
}



def _dump_yaml(data: object, f: Any, indent: int = 0) -> None:
    """Minimal YAML serializer — no external dependency needed."""
    prefix = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                f.write(f"{prefix}{key}:\n")
                _dump_yaml(value, f, indent + 1)
            elif value is None:
                f.write(f"{prefix}{key}: null\n")
            elif isinstance(value, bool):
                f.write(f"{prefix}{key}: {'true' if value else 'false'}\n")
            elif isinstance(value, (int, float)):
                f.write(f"{prefix}{key}: {value}\n")
            else:
                f.write(f"{prefix}{key}: {_yaml_quote(str(value))}\n")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                first = True
                for key, value in item.items():
                    if first:
                        prefix_item = f"{prefix}- "
                        first = False
                    else:
                        prefix_item = f"{prefix}  "
                    safe_key = _yaml_quote(str(key))
                    if isinstance(value, (dict, list)):
                        f.write(f"{prefix_item}{safe_key}:\n")
                        _dump_yaml(value, f, indent + 2)
                    elif value is None:
                        f.write(f"{prefix_item}{safe_key}: null\n")
                    elif isinstance(value, bool):
                        f.write(f"{prefix_item}{safe_key}: {'true' if value else 'false'}\n")
                    elif isinstance(value, (int, float)):
                        f.write(f"{prefix_item}{safe_key}: {value}\n")
                    else:
                        f.write(f"{prefix_item}{safe_key}: {_yaml_quote(str(value))}\n")
            else:
                f.write(f"{prefix}- {_yaml_quote(str(item))}\n")


_YAML_KEYWORDS = frozenset({
    "true", "false", "yes", "no", "on", "off", "null", "y", "n", "~",
})


def _yaml_quote(s: str) -> str:
    """Quote a YAML string value if it contains special characters."""
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    if not s or s.lower() in _YAML_KEYWORDS:
        return f'"{escaped}"'
    try:
        float(s)
        return f'"{escaped}"'
    except ValueError:
        pass
    if any(c in s for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`", "\n", "\r", "\t")):
        return f'"{escaped}"'
    return s

def _derive_category(source_path: str) -> str:
    """Extract category from the source path (first directory component)."""
    parts = Path(source_path).parts
    return parts[0] if parts else "unknown"


class AgencyImporter:
    """Import agency-agents from the vendor repo using an allowlist.

    Parameters
    ----------
    vendor_path:
        Path to the local clone of the agency-agents repository.
    allowlist_path:
        Path to the agency-agents allowlist YAML file.
    output_dir:
        Directory where imported profiles will be written (by ``import_all``).
    """

    def __init__(self, vendor_path: str, allowlist_path: str, output_dir: str) -> None:
        self.vendor_path = Path(vendor_path)
        self.allowlist_path = Path(allowlist_path)
        self.output_dir = Path(output_dir)
        self._allowlist_data: dict[str, Any] | None = None

    def _load_allowlist(self) -> dict[str, Any]:
        if self._allowlist_data is None:
            self._allowlist_data = load_allowlist(str(self.allowlist_path))
        assert self._allowlist_data is not None
        return self._allowlist_data

    def _build_profile_package(self, parsed: dict, entry: dict) -> dict[str, Any]:
        """Build a complete profile package from parsed frontmatter and an allowlist entry.

        Returns a dict with keys: id, expert_profile, normalized_prompt, source_md, output_contract.
        """
        source_data = self._load_allowlist()
        source_path = entry["source_path"]
        artifact_type = entry["output_contract"]
        category = _derive_category(source_path)

        # Build the expert_profile dict matching ExpertProfile schema
        expert_profile: dict[str, Any] = {
            "id": entry["id"],
            "name": parsed["name"],
            "source": {
                "kind": "git",
                "repo": source_data["source"]["repo"],
                "ref": source_data["source"]["ref"],
                "path": source_path,
                "license": "MIT",
            },
            "profile": {
                "category": category,
                "description": parsed["description"],
                "body": parsed["body"],
                "vibe": parsed["vibe"],
                "source_md_path": source_path,
                "normalized_prompt_path": f"normalized/{entry['id']}.md",
                "imported_at": date.today().isoformat(),
            },
            "capabilities": entry["capabilities"],
            "routing": {
                "task_types": entry["capabilities"],
                "positive_signals": [parsed["name"].lower()],
                "negative_signals": [],
            },
            "runtime": {
                "mode": "persona_only",
                "runner": "nexus.generic-expert-agent",
                "implementation": "python-pydanticai",
                "model_tier": "standard",
            },
            "permissions": {
                "mode": "plan",
                "allowed_tools": entry.get("tools", {}).get("allowed", []),
                "denied_tools": entry.get("tools", {}).get(
                    "denied", ["bash", "file_write", "network"]
                ),
            },
            "output_contract": {
                "artifact_type": artifact_type,
                "required_sections": _CONTRACT_SECTIONS.get(artifact_type, ["summary"]),
            },
            "quality": {
                "status": "experimental",
            },
        }

        # The normalized prompt is the body with a header prepended
        normalized_prompt = (
            f"# {parsed['name']}\n\n"
            f"**Description**: {parsed['description']}\n\n"
            f"**Vibe**: {parsed['vibe']}\n\n"
            f"---\n\n"
            f"{parsed['body']}"
        )

        # Build the output_contract dict
        output_contract: dict[str, Any] = {
            "artifact_type": artifact_type,
            "required_sections": _CONTRACT_SECTIONS.get(artifact_type, ["summary"]),
        }

        return {
            "id": entry["id"],
            "expert_profile": expert_profile,
            "normalized_prompt": normalized_prompt,
            "source_md": parsed["body"],
            "output_contract": output_contract,
        }

    def dry_run(self) -> list[dict[str, Any]]:
        """Parse all allowlisted MD files and return profile packages without writing to disk.

        Returns a list of profile package dicts, one per allowlist entry.
        Raises RuntimeError if any file fails content policy check.
        """
        allowlist_data = self._load_allowlist()
        profiles: list[dict[str, Any]] = []

        for entry in allowlist_data["agents"]:
            source_path = entry["source_path"]
            md_file = self.vendor_path / source_path

            # Prevent directory traversal via source_path (e.g. "../../etc/passwd.md")
            try:
                md_file.resolve().relative_to(self.vendor_path.resolve())
            except ValueError:
                raise ValueError(
                    f"source_path escapes vendor directory: {source_path}"
                )

            if not md_file.is_file():
                raise FileNotFoundError(f"Vendor file not found: {md_file}")

            content = md_file.read_text(encoding="utf-8")
            parsed = parse_frontmatter(content)

            # Run content policy check
            policy_result = check_content_policy(parsed["body"])
            if not policy_result["passed"]:
                high_risks = [r for r in policy_result["risks"] if r["severity"] == "high"]
                if high_risks:
                    raise RuntimeError(
                        f"Content policy violation in {source_path}: "
                        f"{[r['pattern'] for r in high_risks]}"
                    )

            profile_package = self._build_profile_package(parsed, entry)
            profiles.append(profile_package)

        return profiles

    def import_all(self) -> None:
        """Import all allowlisted agents and write profile files to output_dir.

        Generates:
        - <id>.json — Expert profile for each agent
        - normalized/<id>.md — Normalized prompt markdown
        - source.lock.yaml — Pinned source metadata for reproducibility
        - index.yaml — Registry index of all imported profiles
        """
        profiles = self.dry_run()

        self.output_dir.mkdir(parents=True, exist_ok=True)

        source_data = self._load_allowlist()
        index_entries: list[dict[str, Any]] = []

        for pkg in profiles:
            profile_id = pkg["id"]
            ep = pkg["expert_profile"]

            # Write expert profile as JSON
            profile_path = self.output_dir / f"{profile_id}.json"
            with profile_path.open("w", encoding="utf-8") as f:
                json.dump(ep, f, indent=2, ensure_ascii=False)

            # Write normalized prompt as Markdown
            prompt_dir = self.output_dir / "normalized"
            prompt_dir.mkdir(exist_ok=True)
            prompt_path = prompt_dir / f"{profile_id}.md"
            with prompt_path.open("w", encoding="utf-8") as f:
                f.write(pkg["normalized_prompt"])

            # Collect index entry
            index_entries.append({
                "id": profile_id,
                "name": ep.get("name", ""),
                "category": ep.get("profile", {}).get("category", ""),
                "capabilities": ep.get("capabilities", []),
                "profile_file": f"{profile_id}.json",
                "prompt_file": f"normalized/{profile_id}.md",
            })

        # Write source.lock.yaml
        source_lock: dict[str, Any] = {
            "version": 1,
            "generated_at": date.today().isoformat(),
            "source": source_data.get("source", {}),
            "agents": {pkg["id"]: pkg["expert_profile"]["source"] for pkg in profiles},
        }
        self._write_yaml(self.output_dir / "source.lock.yaml", source_lock)

        # Write index.yaml
        index_data: dict[str, Any] = {
            "version": 1,
            "generated_at": date.today().isoformat(),
            "agents": index_entries,
        }
        self._write_yaml(self.output_dir / "index.yaml", index_data)

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        """Write a dict as a simple YAML file (no external dependency)."""
        with path.open("w", encoding="utf-8") as f:
            _dump_yaml(data, f, indent=0)
