"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "extract_clauses", "params": {"text": "..."}}
    {"method": "analyze_risks", "params": {"clauses": [...]}}
    {"method": "check_compliance", "params": {"clauses": [...], "jurisdiction": "CN"}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback

from agent_contract_analyzer.agent import ContractAnalyzerAgent
from agent_contract_analyzer.models import ClauseInfo


def handle_message(agent: ContractAnalyzerAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The ContractAnalyzerAgent instance.
        message: Parsed JSON message from stdin.

    Returns:
        Response dict to be serialized as JSON-lines to stdout.
    """
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "extract_clauses":
            text = params.get("text", "")
            if not text:
                return {"status": "error", "error": "Missing 'text' parameter"}
            result = agent.extract_clauses(text)
            return {"status": "ok", "result": [c.model_dump() for c in result]}

        elif method == "analyze_risks":
            raw_clauses = params.get("clauses", [])
            if not raw_clauses:
                return {"status": "error", "error": "Missing 'clauses' parameter"}
            clauses = [ClauseInfo.model_validate(c) for c in raw_clauses]
            result = agent.analyze_risks(clauses)
            return {"status": "ok", "result": result.model_dump()}

        elif method == "check_compliance":
            raw_clauses = params.get("clauses", [])
            jurisdiction = params.get("jurisdiction", "")
            if not raw_clauses:
                return {"status": "error", "error": "Missing 'clauses' parameter"}
            if not jurisdiction:
                return {"status": "error", "error": "Missing 'jurisdiction' parameter"}
            clauses = [ClauseInfo.model_validate(c) for c in raw_clauses]
            result = agent.check_compliance(clauses, jurisdiction)
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
    agent = ContractAnalyzerAgent()

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
