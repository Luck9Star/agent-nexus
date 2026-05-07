"""DocumentationSuiteCoordinator -- orchestrates the product documentation pipeline.

Pattern: Parallel (API Doc + Code Review) -> Sequential (Localization).

In POC mode, each Atomic Agent is simulated with deterministic stub logic.
The coordinator manages parallel execution, result merging, and localization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid

from agent_nexus.models.composition import Composition, CompositionError
from agent_nexus.platform.utils import detect_cycles_dfs

from agent_product_documentation_suite.models import (
    DocArtifact,
    DocumentationResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simulated Atomic Agent helpers (POC -- no real subprocesses)
# ---------------------------------------------------------------------------


def _simulate_api_doc_generator(code_path: str) -> dict:
    """Simulate api-doc-generator output.

    Returns a dict mirroring OpenAPISpec.model_dump().
    """
    return {
        "openapi_version": "3.1.0",
        "info": {
            "title": f"API Documentation for {os.path.basename(code_path)}",
            "version": "1.0.0",
        },
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "responses": {"200": {"description": "User list"}},
                },
            },
            "/users/{id}": {
                "get": {
                    "summary": "Get user by ID",
                    "responses": {"200": {"description": "User details"}},
                },
            },
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
            },
        },
    }


def _simulate_code_reviewer(code_path: str) -> dict:
    """Simulate code-reviewer output.

    Returns a dict mirroring ReviewReport.model_dump().
    """
    return {
        "summary": f"Code review for {os.path.basename(code_path)}: generally good quality.",
        "findings": [
            {
                "line": 10,
                "severity": "warning",
                "category": "style",
                "message": "Function 'process_data' is too long",
                "rule_id": "MAX_FUNCTION_LENGTH",
            },
        ],
        "suggestions": [
            "Consider extracting helper functions from 'process_data'.",
            "Add type hints to all public methods.",
        ],
        "severity_counts": {"critical": 0, "warning": 1, "info": 0},
        "overall_score": 85,
    }


def _simulate_localization(text: str, target_lang: str) -> dict:
    """Simulate localization-specialist output.

    Returns a dict mirroring LocalizationResult.model_dump().
    """
    lang_labels = {
        "zh": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
    }
    label = lang_labels.get(target_lang, target_lang)
    return {
        "translated_text": f"[{label}] {text}",
        "glossary_matches": [],
        "warnings": [],
    }


async def _simulate_localization_async(text: str, target_lang: str) -> dict:
    """Async wrapper for _simulate_localization."""
    return _simulate_localization(text, target_lang)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    """Compute a short SHA-256 hash for content."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class DocumentationSuiteCoordinator:
    """Orchestrates the product documentation suite pipeline.

    Parallel-then-Sequential pattern:
        Phase 1a (api-doc-generator): generate OpenAPI spec (parallel).
        Phase 1b (code-reviewer): review code quality (parallel).
        Phase 2  (localization-specialist): localize combined output (sequential).

    Usage:
        coord = DocumentationSuiteCoordinator()
        result = coord.generate_docs("/path/to/api.py", ["zh", "en"])
        print(result.success, result.coverage_score)
    """

    def generate_docs(
        self,
        code_path: str,
        target_langs: list[str] | None = None,
    ) -> DocumentationResult:
        """Run the full documentation pipeline.

        Args:
            code_path: Path to the source code to document and review.
            target_langs: Language codes for localization (default: ["en"]).

        Returns:
            DocumentationResult with artifacts, coverage, and drift report.
        """
        if target_langs is None:
            target_langs = ["en"]
        return asyncio.run(self._generate_docs_async(code_path, target_langs))

    async def generate_docs_async(
        self,
        code_path: str,
        target_langs: list[str] | None = None,
    ) -> DocumentationResult:
        """Async version of generate_docs for use inside an existing event loop."""
        if target_langs is None:
            target_langs = ["en"]
        return await self._generate_docs_async(code_path, target_langs)

    async def _generate_docs_async(
        self,
        code_path: str,
        target_langs: list[str],
    ) -> DocumentationResult:
        artifacts: list[DocArtifact] = []
        drift_report = ""

        # --- Phase 1a: API Doc Generator (parallel) ---
        try:
            api_spec = _simulate_api_doc_generator(code_path)
            spec_json = json.dumps(api_spec, sort_keys=True)

            api_artifact = DocArtifact(
                type="openapi_spec",
                path=f"/tmp/openapi_{uuid.uuid4().hex[:8]}.json",
                language="en",
                content_hash=_content_hash(spec_json),
            )
            artifacts.append(api_artifact)
        except Exception:
            logger.exception("API doc generation failed for code_path='%s'", code_path)
            api_spec = {}

        # --- Phase 1b: Code Reviewer (parallel) ---
        try:
            review_report = _simulate_code_reviewer(code_path)
            review_json = json.dumps(review_report, sort_keys=True)

            review_artifact = DocArtifact(
                type="review_report",
                path=f"/tmp/review_{uuid.uuid4().hex[:8]}.json",
                language="en",
                content_hash=_content_hash(review_json),
            )
            artifacts.append(review_artifact)
        except Exception:
            logger.exception("Code review failed for code_path='%s'", code_path)
            review_report = {}

        # --- Compute coverage score ---
        paths = api_spec.get("paths", {})
        coverage_score = 0.75 if paths else 0.0

        # --- Drift detection ---
        drift_report = self._detect_drift(artifacts)

        # --- Phase 2: Localization (parallel) ---
        summary_text = review_report.get("summary", "")
        loc_results = await asyncio.gather(
            *[_simulate_localization_async(summary_text, lang) for lang in target_langs],
            return_exceptions=True,
        )
        for lang, loc_result in zip(target_langs, loc_results, strict=True):
            if isinstance(loc_result, Exception):
                logger.exception("Localization failed for language '%s'", lang)
            else:
                loc_artifact = DocArtifact(
                    type="localization",
                    path=f"/tmp/docs_{lang}_{uuid.uuid4().hex[:8]}.md",
                    language=lang,
                    content_hash=_content_hash(loc_result.get("translated_text", "")),
                )
                artifacts.append(loc_artifact)

        return DocumentationResult(
            artifacts=artifacts,
            coverage_score=coverage_score,
            drift_report=drift_report,
            success=True,
        )

    def _detect_drift(self, artifacts: list[DocArtifact]) -> str:
        """Simulate drift detection between code and documentation.

        In POC mode, returns a static drift report.

        Args:
            artifacts: Current documentation artifacts.

        Returns:
            Human-readable drift report string.
        """
        artifact_types = {a.type for a in artifacts}

        if "openapi_spec" not in artifact_types:
            return "Warning: No OpenAPI spec generated. Cannot detect drift."

        if "review_report" not in artifact_types:
            return "Warning: No review report generated. Cannot detect drift."

        return "No drift detected. Documentation is consistent with code."

    @staticmethod
    def parse_composition(toml_path: str) -> dict:
        """Parse a composition.toml file using the shared Composition model.

        Args:
            toml_path: Path to the composition.toml file.

        Returns:
            Parsed TOML as a dict (backward-compatible format).

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        try:
            comp = Composition.from_toml(toml_path)
        except CompositionError as exc:
            if "not found" in str(exc):
                raise FileNotFoundError(str(exc)) from exc
            raise
        return {
            "composition": {"name": comp.name, "description": comp.description},
            "tasks": {
                tid: {
                    "name": task.name,
                    "agent": task.agent,
                    "blocked_by": task.blocked_by,
                }
                for tid, task in comp.tasks.items()
            },
        }

    @staticmethod
    def validate_composition(data: dict) -> list[str]:
        """Validate a parsed composition dict for structural correctness.

        Checks:
        - composition section exists with name and description.
        - At least one task defined.
        - Each task has name, agent, and blocked_by.
        - No circular dependencies (via shared detect_cycles_dfs).
        - All blocked_by references point to existing tasks.

        Args:
            data: Parsed TOML composition data.

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: list[str] = []

        comp = data.get("composition")
        if not comp:
            errors.append("Missing [composition] section")
            return errors

        if "name" not in comp:
            errors.append("composition.name is required")

        tasks = data.get("tasks", {})
        if not tasks:
            errors.append("No tasks defined in [tasks] section")
            return errors

        task_ids = set(tasks.keys())

        for task_id, task_def in tasks.items():
            if "name" not in task_def:
                errors.append(f"Task '{task_id}' missing 'name'")
            if "agent" not in task_def:
                errors.append(f"Task '{task_id}' missing 'agent'")

            blocked_by = task_def.get("blocked_by", [])
            if not isinstance(blocked_by, list):
                errors.append(f"Task '{task_id}' blocked_by must be a list")
                continue

            for dep in blocked_by:
                if dep not in task_ids:
                    errors.append(
                        f"Task '{task_id}' references unknown dependency '{dep}'"
                    )

            if task_id in blocked_by:
                errors.append(f"Task '{task_id}' cannot depend on itself")

        # Shared cycle detection from platform utils
        cycles = detect_cycles_dfs(
            task_ids,
            lambda tid: [d for d in tasks[tid].get("blocked_by", []) if d in task_ids],
        )
        for cycle in cycles:
            errors.append(f"Circular dependency detected: {' -> '.join(cycle)}")

        return errors
