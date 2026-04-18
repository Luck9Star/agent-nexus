"""Entry point for feature-delivery-pipeline composite agent.

Detects run mode from AGENT_MODE environment variable:
- "mcp"    -> Start MCP Server (default)
- "local"  -> Start stdin/stdout JSON-lines adapter for Platform Router
- "cli"    -> Simple CLI interface for development and testing

Composite agents support "mcp" and "local" modes (no "cli_standalone" in
production, but CLI mode is provided for POC testing).
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
            from agent_feature_delivery_pipeline.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            server.run()
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Install full dependencies with: "
                "pip install agent-feature-delivery-pipeline[full]",
                file=sys.stderr,
            )
            sys.exit(1)


def _run_local() -> None:
    """Run local adapter: stdin/stdout JSON-lines for Platform Router."""
    from agent_feature_delivery_pipeline.coordinator import FeatureDeliveryCoordinator

    coordinator = FeatureDeliveryCoordinator()

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

            if method == "run_pipeline":
                spec = params.get("spec", "")
                if not spec:
                    response = {"status": "error", "error": "Missing 'spec' parameter"}
                else:
                    try:
                        result = coordinator.run_pipeline(spec)
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
    from agent_feature_delivery_pipeline.coordinator import FeatureDeliveryCoordinator

    parser = argparse.ArgumentParser(
        description="feature-delivery-pipeline -- Requirements-driven delivery pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run the delivery pipeline")
    run_parser.add_argument("--spec", required=True, help="Requirement specification")

    args = parser.parse_args()

    if args.command == "run":
        coordinator = FeatureDeliveryCoordinator()
        result = coordinator.run_pipeline(args.spec)
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
