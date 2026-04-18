"""Entry point for cicd-quality-gate composite agent.

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
            from agent_cicd_quality_gate.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-cicd-quality-gate[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_local() -> None:
    """Run local adapter: stdin/stdout JSON-lines for Platform Router."""
    from agent_cicd_quality_gate.coordinator import QualityGateCoordinator

    coordinator = QualityGateCoordinator()

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

            if method == "run_gate":
                code_path = params.get("code_path", "")
                if not code_path:
                    response = {"status": "error", "error": "Missing 'code_path' parameter"}
                else:
                    try:
                        config = params.get("config", {})
                        result = coordinator.run_gate(code_path, config)
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
    from agent_cicd_quality_gate.coordinator import QualityGateCoordinator

    parser = argparse.ArgumentParser(
        description="cicd-quality-gate -- CI/CD quality gate with parallel checks"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    run_parser = subparsers.add_parser("run", help="Run quality gate")
    run_parser.add_argument("--code-path", required=True, help="Path to code")
    run_parser.add_argument("--config", default="{}", help="JSON config string")

    args = parser.parse_args()

    if args.command == "run":
        coordinator = QualityGateCoordinator()
        try:
            config = json.loads(args.config)
        except json.JSONDecodeError:
            config = {}
        result = coordinator.run_gate(args.code_path, config)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
