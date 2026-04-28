"""Entry point for security-scanner agent.

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
        from agent_security_scanner.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_security_scanner.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-security-scanner[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_security_scanner.agent import SecurityScannerAgent

    parser = argparse.ArgumentParser(
        description="security-scanner — Application security scanning specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan a file for vulnerabilities")
    scan_parser.add_argument("file_path", help="Path to source file or directory")

    # deps command
    deps_parser = subparsers.add_parser("deps", help="Check dependencies for CVEs")
    deps_parser.add_argument(
        "--deps", required=True, help="JSON string of {package: version}"
    )

    # report command
    report_parser = subparsers.add_parser("report", help="Generate security report")
    report_parser.add_argument(
        "--findings", required=True, help="JSON file or string with findings"
    )

    args = parser.parse_args()
    agent = SecurityScannerAgent()

    if args.command == "scan":
        try:
            result = agent.scan_code(args.file_path)
            print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "deps":
        try:
            deps = json.loads(args.deps)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --deps: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.check_dependencies(deps)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "report":
        try:
            findings = json.loads(args.findings)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for --findings: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.generate_report(findings)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
