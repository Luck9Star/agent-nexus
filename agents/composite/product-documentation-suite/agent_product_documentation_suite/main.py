"""Entry point for product-documentation-suite agent.

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
        _run_local()
    elif mode == "cli":
        _run_cli()
    else:
        # MCP mode (default)
        try:
            from agent_product_documentation_suite.mcp_adapter import (
                create_mcp_server,
            )

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-product-documentation-suite[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_local() -> None:
    """Run stdin/stdout JSON-lines adapter for Platform Router mode."""
    from agent_product_documentation_suite.coordinator import (
        DocumentationSuiteCoordinator,
    )

    coordinator = DocumentationSuiteCoordinator()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
        else:
            response = _handle_message(coordinator, message)

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _handle_message(
    coordinator: DocumentationSuiteCoordinator, message: dict
) -> dict:
    """Dispatch a single inbound message to the coordinator."""
    method = message.get("method", "")
    params = message.get("params", {})

    try:
        if method == "generate_docs":
            code_path = params.get("code_path", "")
            if not code_path:
                return {"status": "error", "error": "Missing 'code_path' parameter"}
            target_langs = params.get("target_langs", ["en"])

            result = coordinator.generate_docs(
                code_path=code_path,
                target_langs=target_langs,
            )
            return {"status": "ok", "result": result.model_dump()}

        else:
            return {"status": "error", "error": f"Unknown method: {method}"}
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_product_documentation_suite.coordinator import (
        DocumentationSuiteCoordinator,
    )

    parser = argparse.ArgumentParser(
        description="product-documentation-suite -- API Doc + Code Review + Localization"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    gen_parser = subparsers.add_parser("generate", help="Generate documentation suite")
    gen_parser.add_argument(
        "--code-path", required=True, help="Path to source code file"
    )
    gen_parser.add_argument(
        "--target-langs",
        nargs="+",
        default=["en"],
        help="Target language codes",
    )

    args = parser.parse_args()
    coordinator = DocumentationSuiteCoordinator()

    if args.command == "generate":
        result = coordinator.generate_docs(
            code_path=args.code_path,
            target_langs=args.target_langs,
        )
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
