"""Entry point for i18n-validator agent.

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
        from agent_i18n_validator.local_adapter import run_local_adapter

        run_local_adapter()

    elif mode == "cli":
        _run_cli()

    else:
        # MCP mode (default)
        try:
            from agent_i18n_validator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: pip install agent-i18n-validator[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_i18n_validator.agent import I18nValidatorAgent

    parser = argparse.ArgumentParser(
        description="i18n-validator — Internationalization completeness checking agent"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # validate command
    validate_parser = subparsers.add_parser("validate", help="Validate i18n locales")
    validate_parser.add_argument("locales", help="JSON string of {locale: {key: value}}")
    validate_parser.add_argument("--base", default="en", help="Base locale (default: en)")

    args = parser.parse_args()
    agent = I18nValidatorAgent()

    if args.command == "validate":
        try:
            locales = json.loads(args.locales)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON for locales: {e}", file=sys.stderr)
            sys.exit(1)
        result = agent.validate_i18n(locales, args.base)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
