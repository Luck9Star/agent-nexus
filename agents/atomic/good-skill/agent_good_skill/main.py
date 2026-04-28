"""Entry point for good-skill agent.

Detects run mode from AGENT_MODE environment variable:
- "mcp"    -> Start MCP Server (default)
- "local"  -> Start stdin/stdout JSON-lines adapter for Platform Router
- "cli"    -> Simple CLI interface for development and testing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def main() -> None:
    """Route to the appropriate adapter based on AGENT_MODE."""
    mode = os.getenv("AGENT_MODE", "mcp").lower()

    if mode == "local":
        from agent_good_skill.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_good_skill.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-good-skill[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_good_skill.agent import GoodSkillAgent

    parser = argparse.ArgumentParser(description="good-skill -- Auto-promoted agent")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Execute a task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument("--context", help="JSON context dictionary", default=None)

    args = parser.parse_args()
    agent = GoodSkillAgent()

    if args.command == "run":
        context = None
        if args.context:
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError as e:
                print(f"Invalid JSON for --context: {e}", file=sys.stderr)
                sys.exit(1)

        result = asyncio.get_event_loop().run_until_complete(
            agent.run(args.task, context)
        )
        print(result)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
