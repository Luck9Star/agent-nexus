"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "analyze", "params": {"template_path": "..."}}
    {"method": "fill", "params": {"template_path": "...", "values": {...}, "output_path": "..."}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_doc_filler.agent import DocFillerAgent
from agent_doc_filler.models import FillRequest


def handle_message(agent: DocFillerAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The DocFillerAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "analyze":
            template_path = params.get("template_path", "")
            if not template_path:
                return {"status": "error", "error": "Missing 'template_path' parameter"}
            result = agent.analyze(template_path)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "fill":
            template_path = params.get("template_path", "")
            values = params.get("values", {})
            output_path = params.get("output_path")
            if not template_path:
                return {"status": "error", "error": "Missing 'template_path' parameter"}
            request = FillRequest(
                template_path=template_path,
                values=values,
                output_path=output_path,
            )
            result = agent.fill(request)
            return {"status": "ok", "result": result.model_dump()}

        else:
            return {"status": "error", "error": f"Unknown method: {method}"}
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
    agent = DocFillerAgent()

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
