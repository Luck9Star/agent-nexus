"""Entry point for doc-filler agent.

Detects run mode from AGENT_MODE environment variable:
- "mcp"    → Start MCP Server (default)
- "local"  → Start stdin/stdout JSON-lines adapter for Platform Router
- "cli"    → Simple CLI interface for development and testing
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
        from agent_doc_filler.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_doc_filler.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-doc-filler[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_doc_filler.agent import DocFillerAgent
    from agent_doc_filler.models import FillRequest

    parser = argparse.ArgumentParser(description="doc-filler — Word template filling specialist")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a template file")
    analyze_parser.add_argument("template_path", help="Path to .docx template")

    # fill command
    fill_parser = subparsers.add_parser("fill", help="Fill a template with values")
    fill_parser.add_argument("template_path", help="Path to .docx template")
    fill_parser.add_argument("--values", required=True, help="JSON string of placeholder values")
    fill_parser.add_argument("--output", help="Output file path")

    args = parser.parse_args()
    agent = DocFillerAgent()

    if args.command == "analyze":
        try:
            result = agent.analyze(args.template_path)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            # Catches BadZipFile, PackageNotFoundError (python-docx),
            # and any other file-format errors gracefully.
            print(f"Error: Invalid template file: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "fill":
        try:
            values = json.loads(args.values)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --values: {e}", file=sys.stderr)
            sys.exit(1)

        request = FillRequest(
            template_path=args.template_path,
            values=values,
            output_path=args.output,
        )
        try:
            result = agent.fill(request)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: Failed to fill template: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
