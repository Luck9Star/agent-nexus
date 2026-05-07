"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "analyze_code_for_tests", "params": {"file_path": "...", "language": "python"}}
    {"method": "generate_test_cases", "params": {"analysis": {...}}}
    {"method": "build_test_suite", "params": {"cases": [...], "framework": "pytest"}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_test_suite_generator.agent import TestSuiteGeneratorAgent
from agent_test_suite_generator.models import TestAnalysis, TestCase


def handle_message(agent: TestSuiteGeneratorAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The TestSuiteGeneratorAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "analyze_code_for_tests":
            file_path = params.get("file_path", "")
            language = params.get("language", "python")
            if not file_path:
                return {"status": "error", "error": "Missing 'file_path' parameter"}
            result = agent.analyze_code_for_tests(file_path, language)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "generate_test_cases":
            raw_analysis = params.get("analysis", {})
            if not raw_analysis:
                return {"status": "error", "error": "Missing 'analysis' parameter"}
            analysis = TestAnalysis.model_validate(raw_analysis)
            cases = agent.generate_test_cases(analysis)
            return {"status": "ok", "result": [c.model_dump() for c in cases]}

        elif method == "build_test_suite":
            raw_cases = params.get("cases", [])
            framework = params.get("framework", "pytest")
            if not raw_cases:
                return {"status": "error", "error": "Missing 'cases' parameter"}
            case_objects = [TestCase.model_validate(c) for c in raw_cases]
            result = agent.build_test_suite(case_objects, framework)
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
    agent = TestSuiteGeneratorAgent()

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
