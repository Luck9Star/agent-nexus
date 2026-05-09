"""Entry point for market-intelligence-analyst agent.

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
from pathlib import Path


def main() -> None:
    """Route to the appropriate adapter based on AGENT_MODE."""
    mode = os.getenv("AGENT_MODE", "mcp").lower()

    if mode == "local":
        from agent_market_intelligence_analyst.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_market_intelligence_analyst.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-market-intelligence-analyst[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_market_intelligence_analyst.agent import MarketIntelligenceAgent

    parser = argparse.ArgumentParser(
        description="market-intelligence-analyst — Market research analysis specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze market data")
    analyze_parser.add_argument("file_path", help="Path to market data text file")
    analyze_parser.add_argument(
        "--framework",
        default="porter",
        choices=["porter", "swot", "pestel"],
        help="Analysis framework",
    )

    # trends command
    trends_parser = subparsers.add_parser("trends", help="Identify market trends")
    trends_parser.add_argument("file_path", help="Path to market data text file")

    # briefing command
    briefing_parser = subparsers.add_parser("briefing", help="Generate market briefing")
    briefing_parser.add_argument("file_path", help="Path to market data text file")
    briefing_parser.add_argument(
        "--framework",
        default="porter",
        choices=["porter", "swot", "pestel"],
        help="Analysis framework",
    )

    args = parser.parse_args()
    agent = MarketIntelligenceAgent()

    if args.command == "analyze":
        try:
            text = Path(args.file_path).read_text()
            result = agent.analyze_market(text, args.framework)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "trends":
        try:
            text = Path(args.file_path).read_text()
            result = agent.identify_trends(text)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "briefing":
        try:
            text = Path(args.file_path).read_text()
            analysis = agent.analyze_market(text, args.framework)
            result = agent.generate_briefing(analysis)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
