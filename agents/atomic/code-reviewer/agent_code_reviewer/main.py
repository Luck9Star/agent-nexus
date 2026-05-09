"""Entry point for code-reviewer agent.

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
        from agent_code_reviewer.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_code_reviewer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-code-reviewer[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_code_reviewer.agent import CodeReviewerAgent
    from agent_code_reviewer.models import CodeAnalysis

    parser = argparse.ArgumentParser(description="code-reviewer -- 代码质量审查专家")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a code file")
    analyze_parser.add_argument("file_path", help="Path to code file")
    analyze_parser.add_argument("--language", default="", help="Language hint")

    # check command
    check_parser = subparsers.add_parser("check", help="Check for anti-patterns")
    check_parser.add_argument("code", help="Code string to check")
    check_parser.add_argument("--language", default="", help="Language hint")

    # review command
    review_parser = subparsers.add_parser("review", help="Generate review report")
    review_parser.add_argument("--analysis", required=True, help="JSON string of CodeAnalysis")

    args = parser.parse_args()
    agent = CodeReviewerAgent()

    if args.command == "analyze":
        result = agent.analyze(args.file_path, args.language)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "check":
        patterns = agent.check(args.code, args.language)
        print(
            json.dumps(
                {"patterns": [p.model_dump() for p in patterns]},
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.command == "review":
        try:
            analysis_data = json.loads(args.analysis)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --analysis: {e}", file=sys.stderr)
            sys.exit(1)

        analysis = CodeAnalysis.model_validate(analysis_data)
        report = agent.review(analysis)
        print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
