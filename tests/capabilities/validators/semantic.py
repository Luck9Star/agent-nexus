"""SemanticValidator — Release layer, keyword and relevance checks."""

from __future__ import annotations

import json
from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    ValidationResult,
)


class SemanticValidator:
    """Validate Agent output quality beyond structure — keywords, relevance."""

    async def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []
        details: dict[str, Any] = {}

        text = self._to_text(raw_output)
        if not text:
            return ValidationResult(
                passed=False,
                score=0.0,
                failures=["Output is empty or not text-convertible"],
            )

        details["output_length"] = len(text)

        keyword_score = self._check_keywords(text, contract.quality_thresholds.required_keywords)
        details["keyword_score"] = keyword_score
        if keyword_score < 0.5 and contract.quality_thresholds.required_keywords:
            failures.append(f"Keyword coverage {keyword_score:.0%} below 50%")

        length_ok = (
            len(text) >= contract.quality_thresholds.min_output_length
            and len(text) <= contract.quality_thresholds.max_output_length
        )
        if not length_ok:
            failures.append(
                f"Output length {len(text)} outside expected range "
                f"[{contract.quality_thresholds.min_output_length}, "
                f"{contract.quality_thresholds.max_output_length}]"
            )

        score = (keyword_score + (1.0 if length_ok else 0.0)) / 2.0
        return ValidationResult(
            passed=len(failures) == 0,
            score=score,
            failures=failures,
            details=details,
        )

    def _to_text(self, raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False)
        return str(raw) if raw is not None else ""

    def _check_keywords(self, text: str, keywords: list[str]) -> float:
        if not keywords:
            return 1.0
        text_lower = text.lower()
        found = sum(1 for kw in keywords if kw.lower() in text_lower)
        return found / len(keywords)
