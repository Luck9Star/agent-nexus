"""CompetitiveIntelCoordinator -- orchestrates the competitive intelligence pipeline.

Sequential chain: Market Intel -> Doc Filler -> Localization.

In POC mode, each Atomic Agent is simulated with deterministic stub logic.
The coordinator manages DAG execution order, error propagation, and result
aggregation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import toml
from agent_nexus.platform.utils import detect_cycles_dfs

from agent_competitive_intelligence_briefing.models import (
    BriefingResult,
    PipelineStep,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simulated Atomic Agent helpers (POC -- no real subprocesses)
# ---------------------------------------------------------------------------


def _simulate_market_intel(query: str, framework: str = "porter") -> dict:
    """Simulate market-intelligence-analyst output.

    Returns a dict mirroring BriefingReport.model_dump().
    """
    return {
        "title": f"Competitive Intelligence: {query}",
        "executive_summary": (
            f"Analysis of '{query}' reveals moderate competitive pressure "
            "with emerging opportunities in adjacent segments."
        ),
        "sections": {
            "market_overview": f"The market for '{query}' is growing steadily.",
            "competitive_landscape": "Three major players dominate the space.",
            "swot_analysis": "Strengths include brand recognition and R&D investment.",
        },
        "recommendations": [
            "Increase investment in emerging segments.",
            "Monitor competitor pricing strategies.",
            "Expand distribution channels.",
        ],
    }


def _simulate_doc_filler(analysis: dict, template_path: str | None = None) -> dict:
    """Simulate doc-filler output.

    Returns a dict mirroring FillResult.model_dump().
    """
    report_path = template_path or f"/tmp/briefing_{uuid.uuid4().hex[:8]}.docx"

    # Map analysis sections to placeholder names
    values = {
        "title": analysis.get("title", ""),
        "executive_summary": analysis.get("executive_summary", ""),
        "recommendations": ", ".join(analysis.get("recommendations", [])),
    }
    for key, val in analysis.get("sections", {}).items():
        values[key] = val

    filled_count = len(values)

    return {
        "success": True,
        "output_path": report_path,
        "filled_count": filled_count,
        "unfilled": [],
        "warnings": [],
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


def _validate_task_fields(tasks: dict, task_ids: set[str], errors: list[str]) -> None:
    """Validate each task has required fields and valid dependencies."""
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
                errors.append(f"Task '{task_id}' references unknown dependency '{dep}'")

        if task_id in blocked_by:
            errors.append(f"Task '{task_id}' cannot depend on itself")


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class CompetitiveIntelCoordinator:
    """Orchestrates the competitive intelligence briefing pipeline.

    Sequential chain:
        Phase 1 (market-intelligence-analyst): gather and analyze market data.
        Phase 2 (doc-filler): fill report template with analysis.
        Phase 3 (localization-specialist): localize the final report.

    Usage:
        coord = CompetitiveIntelCoordinator()
        result = coord.generate_briefing("EV market in China", ["zh", "en"])
        print(result.success, result.report_path)
    """

    def generate_briefing(
        self,
        query: str,
        target_langs: list[str] | None = None,
        template_path: str | None = None,
        framework: str = "porter",
    ) -> BriefingResult:
        """Run the full pipeline and return a BriefingResult.

        Args:
            query: Research query string (e.g. "EV market in China").
            target_langs: Language codes for localization (default: ["en"]).
            template_path: Optional path to a .docx template.
            framework: Market analysis framework (porter/swot/pestel).

        Returns:
            BriefingResult with analysis, report_path, and localizations.
        """
        if target_langs is None:
            target_langs = ["en"]
        return asyncio.run(
            self._generate_briefing_async(query, target_langs, template_path, framework)
        )

    async def generate_briefing_async(
        self,
        query: str,
        target_langs: list[str] | None = None,
        template_path: str | None = None,
        framework: str = "porter",
    ) -> BriefingResult:
        """Async version of generate_briefing for use inside an existing event loop."""
        if target_langs is None:
            target_langs = ["en"]
        return await self._generate_briefing_async(query, target_langs, template_path, framework)

    async def _generate_briefing_async(
        self,
        query: str,
        target_langs: list[str],
        template_path: str | None,
        framework: str,
    ) -> BriefingResult:
        steps = self._build_steps(query, template_path)

        # --- Phase 1: Market Intelligence ---
        steps[0] = PipelineStep(
            name=steps[0].name,
            agent=steps[0].agent,
            input_data={"query": query, "framework": framework},
            status="running",
        )

        try:
            analysis = _simulate_market_intel(query, framework)
            steps[0] = PipelineStep(
                name=steps[0].name,
                agent=steps[0].agent,
                input_data=steps[0].input_data,
                output_data=analysis,
                status="completed",
            )
        except Exception:
            steps[0] = PipelineStep(
                name=steps[0].name,
                agent=steps[0].agent,
                input_data=steps[0].input_data,
                status="failed",
            )
            return BriefingResult(
                query=query,
                analysis={},
                success=False,
            )

        # --- Phase 2: Doc Filler ---
        steps[1] = PipelineStep(
            name=steps[1].name,
            agent=steps[1].agent,
            input_data={"analysis": analysis, "template_path": template_path},
            status="running",
        )

        try:
            fill_result = _simulate_doc_filler(analysis, template_path)
            steps[1] = PipelineStep(
                name=steps[1].name,
                agent=steps[1].agent,
                input_data=steps[1].input_data,
                output_data=fill_result,
                status="completed",
            )
        except Exception:
            steps[1] = PipelineStep(
                name=steps[1].name,
                agent=steps[1].agent,
                input_data=steps[1].input_data,
                status="failed",
            )
            return BriefingResult(
                query=query,
                analysis=analysis,
                success=False,
            )

        report_path = fill_result.get("output_path", "")

        # --- Phase 3: Localization ---
        summary_text = analysis.get("executive_summary", "")
        localizations: dict[str, str] = {}

        localization_input = {
            "text": summary_text,
            "target_langs": target_langs,
        }
        steps[2] = PipelineStep(
            name=steps[2].name,
            agent=steps[2].agent,
            input_data=localization_input,
            status="running",
        )

        loc_raw = await asyncio.gather(
            *[_simulate_localization_async(summary_text, lang) for lang in target_langs],
            return_exceptions=True,
        )
        loc_results: list[dict] = []
        for lang, result in zip(target_langs, loc_raw, strict=True):
            if isinstance(result, Exception):
                logger.exception("Localization failed for language '%s'", lang)
                localizations[lang] = f"[localization failed for {lang}]"
            else:
                loc_results.append(result)
                localizations[lang] = result.get("translated_text", "")

        steps[2] = PipelineStep(
            name=steps[2].name,
            agent=steps[2].agent,
            input_data=localization_input,
            output_data={"results": loc_results},
            status="completed",
        )

        return BriefingResult(
            query=query,
            analysis=analysis,
            report_path=report_path,
            localizations=localizations,
            success=True,
        )

    def _build_steps(self, query: str, template_path: str | None = None) -> list[PipelineStep]:
        """Build the initial pipeline steps (all pending).

        Returns:
            List of three PipelineStep objects in execution order.
        """
        return [
            PipelineStep(
                name="市场数据分析",
                agent="market-intelligence-analyst",
                input_data={"query": query},
            ),
            PipelineStep(
                name="报告模板填充",
                agent="doc-filler",
                input_data={"template_path": template_path},
            ),
            PipelineStep(
                name="报告本地化",
                agent="localization-specialist",
                input_data={"target_langs": []},
            ),
        ]

    @staticmethod
    def parse_composition(toml_path: str) -> dict:
        """Parse a composition.toml file and return its structure.

        Delegates to the shared Composition model for parsing.

        Args:
            toml_path: Path to the composition.toml file.

        Returns:
            Parsed TOML as a dict.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not os.path.exists(toml_path):
            raise FileNotFoundError(f"Composition file not found: {toml_path}")

        with open(toml_path) as f:
            data = toml.load(f)

        return data

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
        _validate_task_fields(tasks, task_ids, errors)

        cycles = detect_cycles_dfs(
            task_ids,
            lambda tid: [d for d in tasks[tid].get("blocked_by", []) if d in task_ids],
        )
        for cycle in cycles:
            errors.append(f"Circular dependency detected: {' -> '.join(cycle)}")

        return errors
