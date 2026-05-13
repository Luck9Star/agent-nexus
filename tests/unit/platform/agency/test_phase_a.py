"""Phase A tests: Expert Profile schema, Output Contract schema, and Allowlist config."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # agent-nexus/
_SCHEMAS_DIR = _PROJECT_ROOT / "schemas"
_CONFIG_DIR = _PROJECT_ROOT / "config"
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"

EXPERT_PROFILE_SCHEMA_PATH = _SCHEMAS_DIR / "expert-profile.schema.json"
OUTPUT_CONTRACT_SCHEMA_PATH = _SCHEMAS_DIR / "output-contract.schema.json"
ALLOWLIST_PATH = _CONFIG_DIR / "agency-agents.allowlist.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_json_schema(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _validate(schema_path: Path, instance: dict) -> None:
    schema = _load_json_schema(schema_path)
    jsonschema.validate(instance, schema)


# ===================================================================
# 1. Expert Profile Schema — valid document
# ===================================================================
@pytest.mark.timeout(30)
def test_expert_profile_schema_valid():
    """A fully-populated ExpertProfile document must pass validation."""
    instance = yaml.safe_load(
        """
id: agency.software-architect
name: Software Architect
source:
  kind: git
  repo: https://github.com/msitarzewski/agency-agents
  ref: 29c2a88fad8ab6e340c7ee6b97d71ee1736920e0
  path: engineering/engineering-software-architect.md
  license: MIT
profile:
  category: engineering
  description: System design, DDD, architectural patterns, trade-off analysis
  source_md_path: source.md
  normalized_prompt_path: normalized-profile.md
  imported_at: "2026-04-25"
capabilities:
  - system_design
  - architecture_review
routing:
  task_types:
    - architecture_review
  positive_signals:
    - "architecture"
  negative_signals:
    - "pixel-perfect UI"
runtime:
  mode: persona_only
  runner: nexus.generic-expert-agent
  implementation: python-pydanticai
  model_tier: standard
  max_context_tokens: 12000
permissions:
  mode: plan
  allowed_tools: []
  denied_tools:
    - bash
    - file_write
    - network
output_contract:
  artifact_type: architecture_plan
  required_sections:
    - context
    - assumptions
    - proposed_design
    - tradeoffs
    - risks
    - next_steps
quality:
  status: experimental
"""
    )
    _validate(EXPERT_PROFILE_SCHEMA_PATH, instance)
    # jsonschema.validate raises on failure; explicit check for clarity
    assert instance["id"] == "agency.software-architect"


# ===================================================================
# 2. Expert Profile Schema — missing required fields
# ===================================================================
@pytest.mark.timeout(30)
def test_expert_profile_schema_missing_required():
    """Each required top-level field must cause validation failure when absent."""
    valid_doc = yaml.safe_load(
        """
id: agency.software-architect
name: Software Architect
source:
  kind: git
  repo: https://github.com/msitarzewski/agency-agents
  ref: 29c2a88fad8ab6e340c7ee6b97d71ee1736920e0
  path: engineering/engineering-software-architect.md
  license: MIT
profile:
  category: engineering
  description: desc
capabilities:
  - system_design
routing:
  task_types: []
  positive_signals: []
  negative_signals: []
runtime:
  mode: persona_only
  runner: nexus.generic-expert-agent
  implementation: python-pydanticai
  model_tier: standard
permissions:
  mode: plan
  allowed_tools: []
  denied_tools: []
output_contract:
  artifact_type: architecture_plan
  required_sections: []
quality:
  status: experimental
"""
    )

    required_fields = ["id", "name", "source", "capabilities", "permissions", "output_contract"]
    for field in required_fields:
        doc = {k: v for k, v in valid_doc.items() if k != field}
        with pytest.raises(jsonschema.ValidationError):
            _validate(EXPERT_PROFILE_SCHEMA_PATH, doc)


# ===================================================================
# 4. Output Contract Schema — missing required fields
# ===================================================================
@pytest.mark.timeout(30)
def test_output_contract_schema_missing_required():
    """Missing artifact_type or required_sections must cause validation failure."""
    valid_doc = yaml.safe_load(
        """
artifact_type: risk_report
required_sections:
  - findings
"""
    )

    for field in ["artifact_type", "required_sections"]:
        doc = {k: v for k, v in valid_doc.items() if k != field}
        with pytest.raises(jsonschema.ValidationError):
            _validate(OUTPUT_CONTRACT_SCHEMA_PATH, doc)


# ===================================================================
# 5. Allowlist loads and has enough entries
# ===================================================================
@pytest.mark.timeout(30)
def test_allowlist_loads():
    """The allowlist file must parse and contain >= 10 agent entries."""
    with open(ALLOWLIST_PATH) as f:
        data = yaml.safe_load(f)
    assert "agents" in data, "allowlist missing top-level 'agents' key"
    assert len(data["agents"]) >= 10, f"expected >= 10 agents, got {len(data['agents'])}"


# ===================================================================
# 6. Allowlist entries have required fields
# ===================================================================
@pytest.mark.timeout(30)
def test_allowlist_entry_fields():
    """Each allowlist entry must have source_path, id, capabilities, output_contract."""
    with open(ALLOWLIST_PATH) as f:
        data = yaml.safe_load(f)

    required_fields = ["source_path", "id", "capabilities", "output_contract"]
    for i, entry in enumerate(data["agents"]):
        for field in required_fields:
            assert field in entry, (
                f"agent entry #{i} (id={entry.get('id', '?')}) missing field '{field}'"
            )


# ===================================================================
# 7. Allowlist source paths point to real files in vendor/
# ===================================================================
@pytest.mark.timeout(30)
def test_allowlist_source_paths_exist():
    """Each source_path in the allowlist must map to an actual file in vendor/agency-agents/."""
    with open(ALLOWLIST_PATH) as f:
        data = yaml.safe_load(f)

    for i, entry in enumerate(data["agents"]):
        source_path = entry["source_path"]
        resolved = _VENDOR_DIR / source_path
        assert resolved.is_file(), (
            f"agent entry #{i} (id={entry.get('id', '?')}): "
            f"source_path '{source_path}' does not exist at {resolved}"
        )
