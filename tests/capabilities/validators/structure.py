"""StructureValidator — CI layer, pure structure assertions."""

from __future__ import annotations

import json
from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    OutputSpec,
    ValidationResult,
)


class StructureValidator:
    """Validate Agent output structure against contract output_schema."""

    def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []

        parsed = self._parse_output(raw_output, contract.output_format)
        if parsed is None:
            return ValidationResult(
                passed=False,
                score=0.0,
                failures=["Output is not parseable"],
            )

        if contract.output_format == "json" and isinstance(parsed, dict):
            self._validate_json_fields(parsed, contract.output_schema, failures)
        elif contract.output_format == "text" and isinstance(parsed, str):
            self._validate_text_length(parsed, contract.quality_thresholds, failures)
        elif contract.output_format == "structured":
            self._validate_json_fields(
                parsed if isinstance(parsed, dict) else {},
                contract.output_schema,
                failures,
            )

        score = 1.0 - (len(failures) / max(len(contract.output_schema), 1))
        return ValidationResult(
            passed=len(failures) == 0,
            score=max(0.0, score),
            failures=failures,
            details={"parsed_type": type(parsed).__name__},
        )

    def _parse_output(self, raw: Any, fmt: str) -> Any:
        if isinstance(raw, str):
            stripped = raw.strip()
            if fmt == "json":
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return None
            return stripped
        return raw

    def _validate_json_fields(
        self,
        data: dict,
        schema: dict[str, OutputSpec],
        failures: list[str],
    ) -> None:
        for field_name, spec in schema.items():
            if field_name not in data:
                if spec.required:
                    failures.append(f"Missing required field: {field_name}")
                continue
            value = data[field_name]
            if not self._check_type(value, spec.type):
                failures.append(
                    f"Field '{field_name}' has wrong type: "
                    f"expected {spec.type}, got {type(value).__name__}"
                )
            if (
                spec.min_length is not None
                and isinstance(value, (str, list))
                and len(value) < spec.min_length
            ):
                failures.append(
                    f"Field '{field_name}' length {len(value)} below minimum {spec.min_length}"
                )
            if spec.allowed_values and value not in spec.allowed_values:
                failures.append(
                    f"Field '{field_name}' value '{value}' "
                    f"not in allowed values: {spec.allowed_values}"
                )

    def _validate_text_length(
        self,
        text: str,
        thresholds: Any,
        failures: list[str],
    ) -> None:
        if len(text) < thresholds.min_output_length:
            failures.append(
                f"Output length {len(text)} below minimum {thresholds.min_output_length}"
            )
        if len(text) > thresholds.max_output_length:
            failures.append(
                f"Output length {len(text)} exceeds maximum {thresholds.max_output_length}"
            )

    @staticmethod
    def _check_type(value: Any, expected: str) -> bool:
        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        expected_type = type_map.get(expected)
        if expected_type is None:
            return True
        return isinstance(value, expected_type)
