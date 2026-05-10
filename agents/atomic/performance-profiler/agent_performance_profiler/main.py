"""Entry point for performance-profiler agent.

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
        from agent_performance_profiler.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_performance_profiler.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-performance-profiler[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_performance_profiler.agent import PerformanceProfilerAgent

    parser = argparse.ArgumentParser(
        description="performance-profiler — Performance bottleneck analysis agent"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze source code")
    analyze_parser.add_argument("source", help="Source code string or file path")

    args = parser.parse_args()
    agent = PerformanceProfilerAgent()

    if args.command == "analyze":
        try:
            # Try to load as file first, fall back to raw string
            try:
                with open(args.source) as f:
                    source_code = f.read()
            except (FileNotFoundError, IsADirectoryError):
                source_code = args.source
            result = agent.analyze_performance(source_code)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
