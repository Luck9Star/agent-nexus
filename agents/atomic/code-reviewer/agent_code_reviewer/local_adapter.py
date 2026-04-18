"""Local adapter -- stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "analyze", "params": {"file_path": "...", "language": "..."}}
    {"method": "check", "params": {"code": "...", "language": "..."}}
    {"method": "review", "params": {"analysis": {...}, "patterns": [...]}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_code_reviewer.agent import CodeReviewerAgent
from agent_code_reviewer.models import CodeAnalysis, PatternMatch


def handle_message(agent: CodeReviewerAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The CodeReviewerAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "analyze":
            file_path = params.get("file_path", "")
            if not file_path:
                return {"status": "error", "error": "Missing 'file_path' parameter"}
            language = params.get("language", "")
            result = agent.analyze(file_path, language)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "check":
            code = params.get("code", "")
            if not code:
                return {"status": "error", "error": "Missing 'code' parameter"}
            language = params.get("language", "")
            patterns = agent.check(code, language)
            return {
                "status": "ok",
                "result": {"patterns": [p.model_dump() for p in patterns]},
            }

        elif method == "review":
            analysis_data = params.get("analysis")
            if not analysis_data:
                return {"status": "error", "error": "Missing 'analysis' parameter"}
            analysis = CodeAnalysis.model_validate(analysis_data)
            patterns_data = params.get("patterns")
            patterns = None
            if patterns_data:
                patterns = [PatternMatch.model_validate(p) for p in patterns_data]
            result = agent.review(analysis, patterns)
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
    agent = CodeReviewerAgent()

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
