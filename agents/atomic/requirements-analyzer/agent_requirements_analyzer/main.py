"""Entry point for requirements-analyzer agent.

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
        from agent_requirements_analyzer.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_requirements_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-requirements-analyzer[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_requirements_analyzer.agent import RequirementsAnalyzerAgent

    parser = argparse.ArgumentParser(
        description="requirements-analyzer -- 多轮对话需求分析专家"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze requirement text")
    analyze_parser.add_argument("text", help="Requirement text to analyze")

    # questions command
    questions_parser = subparsers.add_parser(
        "questions", help="Generate clarifying questions"
    )
    questions_parser.add_argument(
        "--analysis", required=True, help="JSON string of RequirementAnalysis"
    )

    # build command
    build_parser = subparsers.add_parser("build", help="Build requirement specification")
    build_parser.add_argument(
        "--answers", required=True, help="JSON string of answers"
    )
    build_parser.add_argument("--title", default="需求说明书", help="Specification title")

    args = parser.parse_args()
    agent = RequirementsAnalyzerAgent()

    if args.command == "analyze":
        result = agent.analyze(args.text)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "questions":
        try:
            analysis_data = json.loads(args.analysis)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --analysis: {e}", file=sys.stderr)
            sys.exit(1)

        from agent_requirements_analyzer.models import RequirementAnalysis

        analysis = RequirementAnalysis.model_validate(analysis_data)
        questions = agent.questions(analysis)
        print(json.dumps(
            {"questions": [q.model_dump() for q in questions]},
            indent=2,
            ensure_ascii=False,
        ))

    elif args.command == "build":
        try:
            answers = json.loads(args.answers)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --answers: {e}", file=sys.stderr)
            sys.exit(1)

        result = agent.build(answers, title=args.title)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
