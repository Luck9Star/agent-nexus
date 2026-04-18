"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "audit", "params": {"content": "...", "content_type": "html"}}
    {"method": "check_html", "params": {"html": "..."}}
    {"method": "remediation", "params": {"issues": [...]}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_accessibility_auditor.agent import AccessibilityAuditorAgent


def handle_message(agent: AccessibilityAuditorAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The AccessibilityAuditorAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "audit":
            content = params.get("content", "")
            content_type = params.get("content_type", "html")
            if not content:
                return {"status": "error", "error": "Missing 'content' parameter"}
            result = agent.audit_content(content, content_type)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "check_html":
            html = params.get("html", "")
            if not html:
                return {"status": "error", "error": "Missing 'html' parameter"}
            issues = agent.check_html(html)
            return {
                "status": "ok",
                "result": [i.model_dump() for i in issues],
            }

        elif method == "remediation":
            issues = params.get("issues", [])
            if not issues:
                return {"status": "error", "error": "Missing 'issues' parameter"}
            result = agent.generate_remediation(issues)
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
    agent = AccessibilityAuditorAgent()

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
