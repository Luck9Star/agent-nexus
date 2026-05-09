"""Local adapter -- stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "extract", "params": {"file_path": "..."}}
    {"method": "infer", "params": {"type_info": "..."}}
    {"method": "generate", "params": {"endpoints": [...], "info": {...}}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable

from agent_api_doc_generator.agent import APIDocGeneratorAgent
from agent_api_doc_generator.models import EndpointInfo, SchemaInfo


def _handle_extract(agent: APIDocGeneratorAgent, params: dict) -> dict:
    """Handle the 'extract' method."""
    file_path = params.get("file_path", "")
    if not file_path:
        return {"status": "error", "error": "Missing 'file_path' parameter"}
    endpoints = agent.extract(file_path)
    return {"status": "ok", "result": {"endpoints": [e.model_dump() for e in endpoints]}}


def _handle_infer(agent: APIDocGeneratorAgent, params: dict) -> dict:
    """Handle the 'infer' method."""
    type_info = params.get("type_info", "")
    if not type_info:
        return {"status": "error", "error": "Missing 'type_info' parameter"}
    schema = agent.infer(type_info)
    return {"status": "ok", "result": schema.model_dump()}


def _handle_generate(agent: APIDocGeneratorAgent, params: dict) -> dict:
    """Handle the 'generate' method."""
    endpoints_data = params.get("endpoints", [])
    if not endpoints_data:
        return {"status": "error", "error": "Missing 'endpoints' parameter"}
    endpoints = [EndpointInfo.model_validate(e) for e in endpoints_data]
    info = params.get("info")
    schemas_data = params.get("schemas")
    schemas = None
    if schemas_data:
        schemas = [SchemaInfo.model_validate(s) for s in schemas_data]
    result = agent.generate(endpoints, info, schemas)
    return {"status": "ok", "result": result.model_dump()}


_METHOD_HANDLERS: dict[str, Callable] = {
    "extract": _handle_extract,
    "infer": _handle_infer,
    "generate": _handle_generate,
}


def handle_message(agent: APIDocGeneratorAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The APIDocGeneratorAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    handler = _METHOD_HANDLERS.get(method)
    if not handler:
        return {"status": "error", "error": f"Unknown method: {method}"}
    try:
        return handler(agent, params)
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def run_local_adapter() -> None:
    """Run the local adapter, reading JSON-lines from stdin.

    Reads lines until EOF, dispatches each to the agent, and writes
    JSON-line responses to stdout.
    """
    agent = APIDocGeneratorAgent()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
        else:
            try:
                response = handle_message(agent, message)
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                response = {
                    "status": "error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
