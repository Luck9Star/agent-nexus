"""Local adapter -- stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "analyze", "params": {"text": "..."}}
    {"method": "questions", "params": {"analysis": {...}}}
    {"method": "build", "params": {"answers": {...}, "title": "..."}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_requirements_analyzer.agent import RequirementsAnalyzerAgent
from agent_requirements_analyzer.models import RequirementAnalysis


def handle_message(agent: RequirementsAnalyzerAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The RequirementsAnalyzerAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "analyze":
            text = params.get("text")
            if text is None:
                return {"status": "error", "error": "Missing 'text' parameter"}
            result = agent.analyze(text)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "questions":
            analysis_data = params.get("analysis")
            if not analysis_data:
                return {"status": "error", "error": "Missing 'analysis' parameter"}
            analysis = RequirementAnalysis.model_validate(analysis_data)
            questions = agent.questions(analysis)
            return {
                "status": "ok",
                "result": {"questions": [q.model_dump() for q in questions]},
            }

        elif method == "build":
            answers = params.get("answers", {})
            if not answers:
                return {"status": "error", "error": "Missing 'answers' parameter"}
            analysis_data = params.get("analysis")
            analysis = (
                RequirementAnalysis.model_validate(analysis_data)
                if analysis_data
                else None
            )
            title = params.get("title", "需求说明书")
            result = agent.build(answers, analysis, title)
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
    agent = RequirementsAnalyzerAgent()

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
