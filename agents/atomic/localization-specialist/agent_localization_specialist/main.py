"""Entry point for localization-specialist agent.

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


def main() -> None:
    """Route to the appropriate adapter based on AGENT_MODE."""
    mode = os.getenv("AGENT_MODE", "mcp").lower()

    if mode == "local":
        from agent_localization_specialist.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_localization_specialist.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-localization-specialist[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_localization_specialist.agent import LocalizationSpecialistAgent

    parser = argparse.ArgumentParser(
        description="localization-specialist — Translation and localization specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze source text")
    analyze_parser.add_argument("text", help="Text to analyze")
    analyze_parser.add_argument("--lang", default="en", help="Source language code")

    # glossary command
    glossary_parser = subparsers.add_parser("glossary", help="Manage glossary")
    glossary_parser.add_argument(
        "--action", required=True, help="Action: add/list/search/delete/clear"
    )
    glossary_parser.add_argument("--entries", help="JSON string of entries")

    # localize command
    loc_parser = subparsers.add_parser("localize", help="Translate text")
    loc_parser.add_argument("text", help="Text to translate")
    loc_parser.add_argument("--target", required=True, help="Target language code")
    loc_parser.add_argument("--glossary", help="JSON string of glossary mapping")

    args = parser.parse_args()
    agent = LocalizationSpecialistAgent()

    if args.command == "analyze":
        result = agent.analyze_text(args.text, args.lang)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "glossary":
        entries = []
        if args.entries:
            try:
                entries = json.loads(args.entries)
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
        result = agent.manage_glossary(args.action, entries)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "localize":
        glossary = None
        if args.glossary:
            try:
                glossary = json.loads(args.glossary)
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
        result = agent.localize(args.text, args.target, glossary)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
