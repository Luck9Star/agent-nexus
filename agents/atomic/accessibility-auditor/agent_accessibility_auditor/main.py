"""Entry point for accessibility-auditor agent.

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
        from agent_accessibility_auditor.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_accessibility_auditor.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-accessibility-auditor[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_accessibility_auditor.agent import AccessibilityAuditorAgent

    parser = argparse.ArgumentParser(
        description="accessibility-auditor — WCAG 2.2 AA auditing specialist"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # audit command
    audit_parser = subparsers.add_parser("audit", help="Audit content for accessibility")
    audit_parser.add_argument("file", help="Path to HTML file to audit")

    # check command
    check_parser = subparsers.add_parser("check", help="Check HTML for issues")
    check_parser.add_argument("file", help="Path to HTML file to check")

    # remediation command
    rem_parser = subparsers.add_parser("remediation", help="Generate remediation plan")
    rem_parser.add_argument(
        "--issues", required=True, help="JSON string with issues"
    )

    args = parser.parse_args()
    agent = AccessibilityAuditorAgent()

    if args.command == "audit":
        try:
            with open(args.file, encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.audit_content(content, "html")
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    elif args.command == "check":
        try:
            with open(args.file, encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        issues = agent.check_html(html)
        print(json.dumps([i.model_dump() for i in issues], indent=2, ensure_ascii=False))

    elif args.command == "remediation":
        try:
            issues = json.loads(args.issues)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.generate_remediation(issues)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
