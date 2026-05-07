"""Entry point for competitive-intelligence-briefing agent.

Detects run mode from AGENT_MODE environment variable:
- "mcp"    -> Start MCP Server (default)
- "local"  -> Start stdin/stdout JSON-lines adapter for Platform Router
- "cli"    -> Simple CLI interface for development and testing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_competitive_intelligence_briefing.coordinator import (
        CompetitiveIntelCoordinator,
    )


def main() -> None:
    """Route to the appropriate adapter based on AGENT_MODE."""
    mode = os.getenv("AGENT_MODE", "mcp").lower()

    if mode == "local":
        _run_local()
    elif mode == "cli":
        _run_cli()
    else:
        # MCP mode (default)
        try:
            from agent_competitive_intelligence_briefing.mcp_adapter import (
                create_mcp_server,
            )

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-competitive-intelligence-briefing[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_local() -> None:
    """Run stdin/stdout JSON-lines adapter for Platform Router mode."""
    from agent_competitive_intelligence_briefing.coordinator import (
        CompetitiveIntelCoordinator,
    )

    coordinator = CompetitiveIntelCoordinator()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
        else:
            response = _handle_message(coordinator, message)

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _handle_message(coordinator: CompetitiveIntelCoordinator, message: dict) -> dict:
    """Dispatch a single inbound message to the coordinator."""
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "generate_briefing":
            query = params.get("query", "")
            if not query:
                return {"status": "error", "error": "Missing 'query' parameter"}
            target_langs = params.get("target_langs", ["en"])
            template_path = params.get("template_path")
            framework = params.get("framework", "porter")

            result = coordinator.generate_briefing(
                query=query,
                target_langs=target_langs,
                template_path=template_path,
                framework=framework,
            )
            return {"status": "ok", "result": result.model_dump()}

        else:
            return {"status": "error", "error": f"Unknown method: {method}"}
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_competitive_intelligence_briefing.coordinator import (
        CompetitiveIntelCoordinator,
    )

    parser = argparse.ArgumentParser(
        description="competitive-intelligence-briefing -- Competitive Intel Pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    gen_parser = subparsers.add_parser("generate", help="Generate a briefing")
    gen_parser.add_argument("--query", required=True, help="Research query")
    gen_parser.add_argument(
        "--target-langs",
        nargs="+",
        default=["en"],
        help="Target language codes",
    )
    gen_parser.add_argument("--template", help="Template .docx path")
    gen_parser.add_argument(
        "--framework",
        default="porter",
        choices=["porter", "swot", "pestel"],
        help="Analysis framework",
    )

    args = parser.parse_args()
    coordinator = CompetitiveIntelCoordinator()

    if args.command == "generate":
        result = coordinator.generate_briefing(
            query=args.query,
            target_langs=args.target_langs,
            template_path=args.template,
            framework=args.framework,
        )
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
