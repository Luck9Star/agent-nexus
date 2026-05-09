"""Entry point for document-compliance-gateway composite agent.

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
        try:
            from agent_document_compliance_gateway.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-document-compliance-gateway[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_local() -> None:
    """Run local adapter: stdin/stdout JSON-lines for Platform Router."""
    from agent_document_compliance_gateway.coordinator import ComplianceCoordinator

    coordinator = ComplianceCoordinator()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            response: dict = {"status": "error", "error": f"Invalid JSON: {e}"}
        else:
            method = message.get("method", "")
            params = message.get("params", {})

            if method == "check_compliance":
                document = params.get("document", "")
                if not document:
                    response = {"status": "error", "error": "Missing 'document' parameter"}
                else:
                    try:
                        jurisdictions = params.get("jurisdictions", [])
                        result = coordinator.check_compliance(document, jurisdictions)
                        response = {"status": "ok", "result": result.model_dump()}
                    except Exception as exc:
                        response = {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
            else:
                response = {"status": "error", "error": f"Unknown method: {method}"}

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _run_cli() -> None:
    """Simple CLI interface for development and testing."""
    from agent_document_compliance_gateway.coordinator import ComplianceCoordinator

    parser = argparse.ArgumentParser(
        description="document-compliance-gateway -- Cross-dimension compliance checking"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser("run", help="Run compliance check")
    run_parser.add_argument("--document", required=True, help="Document text to check")
    run_parser.add_argument("--jurisdictions", default="[]", help="JSON list of jurisdiction codes")

    args = parser.parse_args()

    if args.command == "run":
        coordinator = ComplianceCoordinator()
        try:
            jurisdictions = json.loads(args.jurisdictions)
        except json.JSONDecodeError:
            jurisdictions = []
        result = coordinator.check_compliance(args.document, jurisdictions)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
