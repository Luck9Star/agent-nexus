"""Entry point for test-suite-generator agent.

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
        from agent_test_suite_generator.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_test_suite_generator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-test-suite-generator[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_test_suite_generator.agent import TestSuiteGeneratorAgent

    parser = argparse.ArgumentParser(
        description="test-suite-generator — Test suite generation specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze code for tests")
    analyze_parser.add_argument("file_path", help="Path to source file")
    analyze_parser.add_argument(
        "--language", default="python", help="Programming language"
    )

    # generate command
    generate_parser = subparsers.add_parser("generate", help="Generate test cases")
    generate_parser.add_argument("file_path", help="Path to source file")
    generate_parser.add_argument(
        "--framework", default="pytest", choices=["pytest", "unittest"],
        help="Test framework",
    )

    # build command
    build_parser = subparsers.add_parser("build", help="Build test suite")
    build_parser.add_argument("file_path", help="Path to source file")
    build_parser.add_argument(
        "--framework", default="pytest", choices=["pytest", "unittest"],
        help="Test framework",
    )

    args = parser.parse_args()
    agent = TestSuiteGeneratorAgent()

    if args.command == "analyze":
        try:
            result = agent.analyze_code_for_tests(args.file_path, args.language)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "generate":
        try:
            analysis = agent.analyze_code_for_tests(args.file_path, "python")
            cases = agent.generate_test_cases(analysis)
            print(json.dumps([c.model_dump() for c in cases], indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "build":
        try:
            analysis = agent.analyze_code_for_tests(args.file_path, "python")
            cases = agent.generate_test_cases(analysis)
            suite = agent.build_test_suite(cases, args.framework)
            print(json.dumps(suite.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
