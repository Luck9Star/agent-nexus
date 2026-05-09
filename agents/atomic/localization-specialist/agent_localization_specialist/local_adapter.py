"""Local adapter — stdin/stdout JSON-lines handler for Platform Router mode.

Protocol:
- Read JSON messages from stdin (one per line).
- Dispatch to the appropriate agent method.
- Write JSON responses to stdout (one per line).

Message format (inbound):
    {"method": "analyze", "params": {"text": "...", "source_lang": "en"}}
    {"method": "glossary", "params": {"action": "add", "entries": [...]}}
    {"method": "localize", "params": {"text": "...", "target_lang": "zh", "glossary": {...}}}

Message format (outbound):
    {"status": "ok", "result": {...}}
    {"status": "error", "error": "...", "error_type": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable

from agent_localization_specialist.agent import LocalizationSpecialistAgent


def _handle_analyze(agent: LocalizationSpecialistAgent, params: dict) -> dict:
    """Handle the 'analyze' method."""
    text = params.get("text", "")
    source_lang = params.get("source_lang", "en")
    if not text:
        return {"status": "error", "error": "Missing 'text' parameter"}
    result = agent.analyze_text(text, source_lang)
    return {"status": "ok", "result": result.model_dump()}


def _handle_glossary(agent: LocalizationSpecialistAgent, params: dict) -> dict:
    """Handle the 'glossary' method."""
    action = params.get("action", "")
    entries = params.get("entries", [])
    if not action:
        return {"status": "error", "error": "Missing 'action' parameter"}
    result = agent.manage_glossary(action, entries)
    return {"status": "ok", "result": result.model_dump()}


def _handle_localize(agent: LocalizationSpecialistAgent, params: dict) -> dict:
    """Handle the 'localize' method."""
    text = params.get("text", "")
    target_lang = params.get("target_lang", "")
    glossary = params.get("glossary")
    if not text:
        return {"status": "error", "error": "Missing 'text' parameter"}
    if not target_lang:
        return {"status": "error", "error": "Missing 'target_lang' parameter"}
    result = agent.localize(text, target_lang, glossary)
    return {"status": "ok", "result": result.model_dump()}


_METHOD_HANDLERS: dict[str, Callable] = {
    "analyze": _handle_analyze,
    "glossary": _handle_glossary,
    "localize": _handle_localize,
}


def handle_message(agent: LocalizationSpecialistAgent, message: dict) -> dict:
    """Dispatch a single inbound message to the agent.

    Args:
        agent: The LocalizationSpecialistAgent instance.
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
    agent = LocalizationSpecialistAgent()

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
