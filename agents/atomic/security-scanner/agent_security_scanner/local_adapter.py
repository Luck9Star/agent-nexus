"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "scan_code", "params": {"file_path": "..."}}
    {"method": "check_dependencies", "params": {"deps": {...}}}
    {"method": "generate_report", "params": {"findings": [...]}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_security_scanner.agent import SecurityScannerAgent


def handle_message(agent: SecurityScannerAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The SecurityScannerAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "scan_code":
            file_path = params.get("file_path", "")
            if not file_path:
                return {"status": "error", "error": "Missing 'file_path' parameter"}
            result = agent.scan_code(file_path)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "check_dependencies":
            deps = params.get("deps", {})
            if not deps:
                return {"status": "error", "error": "Missing 'deps' parameter"}
            result = agent.check_dependencies(deps)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "generate_report":
            findings = params.get("findings", [])
            if not findings:
                return {"status": "error", "error": "Missing 'findings' parameter"}
            result = agent.generate_report(findings)
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
    agent = SecurityScannerAgent()

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
