"""Contract validation tool — validate OpenAPI specification structure.

Checks required fields, validates schema references, detects missing
error responses, and verifies endpoint consistency.
"""

from __future__ import annotations

import json
from typing import Any

from agent_api_contract_tester.models import ContractFinding, ContractValidationResult


def validate_contract(spec_content: str) -> ContractValidationResult:
    """Validate an OpenAPI specification for structural completeness.

    Parses the spec as JSON, then checks:
    1. Required top-level fields (openapi, info, paths)
    2. Schema $ref references resolve to existing definitions
    3. Endpoints have appropriate response definitions
    4. Missing error responses (4xx) are flagged

    Args:
        spec_content: OpenAPI spec as a JSON string.

    Returns:
        ContractValidationResult with all findings and validation status.
    """
    findings: list[ContractFinding] = []

    # Parse spec
    try:
        spec = json.loads(spec_content)
    except json.JSONDecodeError as e:
        return ContractValidationResult(
            findings=[
                ContractFinding(
                    severity="error",
                    category="structure",
                    location="<root>",
                    description=f"Invalid JSON: {e}",
                    remediation="Fix JSON syntax errors in the specification",
                )
            ],
            is_valid=False,
        )

    if not isinstance(spec, dict):
        return ContractValidationResult(
            findings=[
                ContractFinding(
                    severity="error",
                    category="structure",
                    location="<root>",
                    description="Specification must be a JSON object",
                    remediation="Ensure the top-level spec is an object, not an array or scalar",
                )
            ],
            is_valid=False,
        )

    # 1. Structure validation
    findings.extend(_check_structure(spec))

    # 2. Schema reference validation
    schemas = _get_schemas(spec)
    findings.extend(_check_schema_refs(spec, schemas))

    # 3. Endpoint consistency and missing error responses
    paths = spec.get("paths", {})
    endpoint_count = _count_endpoints(paths)
    findings.extend(_check_endpoints(paths))

    is_valid = not any(f.severity == "error" for f in findings)
    spec_version = spec.get("openapi", "") or ""

    return ContractValidationResult(
        findings=findings,
        is_valid=is_valid,
        spec_version=spec_version,
        endpoint_count=endpoint_count,
    )


def _check_structure(spec: dict) -> list[ContractFinding]:
    """Check required top-level fields."""
    findings: list[ContractFinding] = []

    # Check openapi version
    version = spec.get("openapi")
    if not version:
        findings.append(
            ContractFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'openapi' field",
                remediation="Add 'openapi' field with version (e.g. '3.0.0')",
            )
        )
    elif not isinstance(version, str) or not version.startswith("3."):
        findings.append(
            ContractFinding(
                severity="warning",
                category="structure",
                location="openapi",
                description=f"Unexpected OpenAPI version: {version}",
                remediation="Use OpenAPI 3.x version (e.g. '3.0.0', '3.1.0')",
            )
        )

    # Check info object
    info = spec.get("info")
    if not info:
        findings.append(
            ContractFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'info' field",
                remediation="Add 'info' object with 'title' and 'version' fields",
            )
        )
    elif isinstance(info, dict):
        if not info.get("title"):
            findings.append(
                ContractFinding(
                    severity="error",
                    category="structure",
                    location="info",
                    description="Missing required 'info.title' field",
                    remediation="Add 'title' to the info object",
                )
            )
        if not info.get("version"):
            findings.append(
                ContractFinding(
                    severity="warning",
                    category="structure",
                    location="info",
                    description="Missing recommended 'info.version' field",
                    remediation="Add 'version' to the info object",
                )
            )

    # Check paths
    paths = spec.get("paths")
    if paths is None:
        findings.append(
            ContractFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'paths' field",
                remediation="Add 'paths' object defining API endpoints",
            )
        )
    elif isinstance(paths, dict) and len(paths) == 0:
        findings.append(
            ContractFinding(
                severity="warning",
                category="structure",
                location="paths",
                description="'paths' object is empty — no endpoints defined",
                remediation="Define at least one API endpoint in the paths object",
            )
        )

    return findings


def _get_schemas(spec: dict) -> set[str]:
    """Extract all defined schema names from components/schemas."""
    schemas: set[str] = set()
    components = spec.get("components", {})
    if isinstance(components, dict):
        defined = components.get("schemas", {})
        if isinstance(defined, dict):
            schemas.update(defined.keys())
    return schemas


def _check_schema_refs(spec: dict, schemas: set[str]) -> list[ContractFinding]:
    """Validate that all $ref references point to existing schemas."""
    findings: list[ContractFinding] = []
    _walk_refs(spec, schemas, findings, path="")
    return findings


def _walk_refs(obj: Any, schemas: set[str], findings: list[ContractFinding], path: str) -> None:
    """Recursively walk the spec and validate $ref references."""
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref: str = obj["$ref"]
            if ref.startswith("#/components/schemas/"):
                schema_name = ref.split("/")[-1]
                if schema_name not in schemas:
                    findings.append(
                        ContractFinding(
                            severity="error",
                            category="schema_ref",
                            location=path or "<root>",
                            description=f"Schema reference '{ref}' not found in components/schemas",
                            remediation=(
                                f"Define schema '{schema_name}' in"
                                " components/schemas or fix the reference"
                            ),
                        )
                    )
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            _walk_refs(value, schemas, findings, child_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_refs(item, schemas, findings, f"{path}[{i}]")


def _count_endpoints(paths: dict) -> int:
    """Count the number of endpoints (method-path combinations)."""
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    count = 0
    for _path, methods in paths.items():
        if isinstance(methods, dict):
            for method in methods:
                if method.lower() in http_methods:
                    count += 1
    return count


def _check_endpoints(paths: dict) -> list[ContractFinding]:
    """Check endpoints for consistency and missing error responses."""
    findings: list[ContractFinding] = []
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in http_methods or not isinstance(operation, dict):
                continue

            location = f"paths.{path}.{method}"
            responses = operation.get("responses", {})

            if not responses:
                findings.append(
                    ContractFinding(
                        severity="error",
                        category="missing_response",
                        location=location,
                        description=f"No responses defined for {method.upper()} {path}",
                        remediation="Define at least a 200 (success) response",
                    )
                )
                continue

            # Check for success response
            has_success = any(code.startswith("2") for code in responses if isinstance(code, str))
            if not has_success:
                findings.append(
                    ContractFinding(
                        severity="warning",
                        category="missing_response",
                        location=location,
                        description=f"No 2xx success response for {method.upper()} {path}",
                        remediation="Add a 2xx success response definition",
                    )
                )

            # Check for error responses
            has_client_error = any(
                code.startswith("4") for code in responses if isinstance(code, str)
            )
            if not has_client_error:
                findings.append(
                    ContractFinding(
                        severity="info",
                        category="missing_response",
                        location=location,
                        description=f"No 4xx error response for {method.upper()} {path}",
                        remediation="Consider adding 400/404 error responses",
                    )
                )

            # Method-specific suggestions
            if method.lower() == "post":
                has_created = "201" in responses
                if not has_created:
                    findings.append(
                        ContractFinding(
                            severity="info",
                            category="missing_response",
                            location=location,
                            description=f"POST {path} missing 201 Created response",
                            remediation="Consider adding 201 response for resource creation",
                        )
                    )

            if method.lower() == "delete":
                has_no_content = "204" in responses
                if not has_no_content:
                    findings.append(
                        ContractFinding(
                            severity="info",
                            category="missing_response",
                            location=location,
                            description=f"DELETE {path} missing 204 No Content response",
                            remediation="Consider adding 204 response for successful deletion",
                        )
                    )

    return findings
