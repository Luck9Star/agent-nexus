"""Endpoint extraction tool -- parse code files for API route definitions.

Supports FastAPI, Flask, Express, and Spring Boot route patterns.
Uses regex-based scanning to identify endpoints.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_api_doc_generator.models import EndpointInfo

# Framework-specific route patterns
FASTAPI_PATTERN = re.compile(
    r"@(?:app|router)\."
    r"(get|post|put|delete|patch)\s*\(\s*['\"](/[^'\"]*)['\"]",
    re.IGNORECASE,
)

FLASK_PATTERN = re.compile(
    r"@(?:app|bp|blueprint)\.route\s*\(\s*['\"](/[^'\"]*)['\"]"
    r"(?:\s*,\s*methods\s*=\s*\[([^\]]*)\])?",
)

EXPRESS_PATTERN = re.compile(
    r"(?:app|router)\."
    r"(get|post|put|delete|patch)\s*\(\s*['\"](/[^'\"]*)['\"]",
    re.IGNORECASE,
)

SPRING_PATTERN = re.compile(
    r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*)?['\"](/[^'\"]*)['\"]",
)

# Path parameter patterns
PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _extract_path_params(path: str) -> list[dict[str, str]]:
    """Extract path parameters from a URL path."""
    params: list[dict[str, str]] = []
    for match in PATH_PARAM_RE.finditer(path):
        params.append(
            {
                "name": match.group(1),
                "in": "path",
                "required": "true",
                "schema": "string",
            }
        )
    return params


def _scan_fastapi(content: str) -> list[EndpointInfo]:
    """Extract endpoints from FastAPI-style decorators."""
    endpoints: list[EndpointInfo] = []
    for match in FASTAPI_PATTERN.finditer(content):
        method = match.group(1).upper()
        path = match.group(2)
        endpoints.append(
            EndpointInfo(
                path=path,
                method=method,
                parameters=_extract_path_params(path),
                summary=f"{method} {path}",
            )
        )
    return endpoints


def _scan_flask(content: str) -> list[EndpointInfo]:
    """Extract endpoints from Flask-style route decorators."""
    endpoints: list[EndpointInfo] = []
    for match in FLASK_PATTERN.finditer(content):
        path = match.group(1)
        methods_str = match.group(2) or "'GET'"
        # Parse methods list
        methods = re.findall(r"'(\w+)'", methods_str)
        if not methods:
            methods = ["GET"]

        for method in methods:
            endpoints.append(
                EndpointInfo(
                    path=path,
                    method=method.upper(),
                    parameters=_extract_path_params(path),
                    summary=f"{method.upper()} {path}",
                )
            )
    return endpoints


def _scan_express(content: str) -> list[EndpointInfo]:
    """Extract endpoints from Express.js-style route definitions."""
    endpoints: list[EndpointInfo] = []
    for match in EXPRESS_PATTERN.finditer(content):
        method = match.group(1).upper()
        path = match.group(2)
        endpoints.append(
            EndpointInfo(
                path=path,
                method=method,
                parameters=_extract_path_params(path),
                summary=f"{method} {path}",
            )
        )
    return endpoints


def _scan_spring(content: str) -> list[EndpointInfo]:
    """Extract endpoints from Spring Boot annotations."""
    endpoints: list[EndpointInfo] = []
    for match in SPRING_PATTERN.finditer(content):
        annotation_type = match.group(1).lower()
        path = match.group(2)

        # Map annotation type to HTTP method
        method_map = {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patch": "PATCH",
            "request": "ALL",
        }
        method = method_map.get(annotation_type, "GET")

        # Spring uses {param} syntax same as OpenAPI
        endpoints.append(
            EndpointInfo(
                path=path,
                method=method,
                parameters=_extract_path_params(path),
                summary=f"{method} {path}",
            )
        )
    return endpoints


def _detect_framework(content: str) -> str:
    """Detect which web framework is being used."""
    if "FastAPI" in content or "from fastapi" in content:
        return "fastapi"
    if "@app.route" in content or "Blueprint" in content:
        return "flask"
    if "@GetMapping" in content or "@PostMapping" in content or "@RequestMapping" in content:
        return "spring"
    if "express" in content.lower() or "router.get" in content or "app.get" in content:
        return "express"
    return "unknown"


def extract_endpoints(file_path: str) -> list[EndpointInfo]:
    """Extract API endpoint definitions from a source code file.

    Detects the web framework used and scans for route definitions.
    Supports FastAPI, Flask, Express, and Spring Boot.

    Args:
        file_path: Path to the source code file.

    Returns:
        List of EndpointInfo objects for each detected endpoint.
    """
    path = Path(file_path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return []

    framework = _detect_framework(content)
    endpoints: list[EndpointInfo] = []

    if framework == "fastapi":
        endpoints.extend(_scan_fastapi(content))
    elif framework == "flask":
        endpoints.extend(_scan_flask(content))
    elif framework == "spring":
        endpoints.extend(_scan_spring(content))
    elif framework == "express":
        endpoints.extend(_scan_express(content))
    else:
        # Try all scanners
        endpoints.extend(_scan_fastapi(content))
        endpoints.extend(_scan_flask(content))
        endpoints.extend(_scan_express(content))
        endpoints.extend(_scan_spring(content))

    return endpoints
