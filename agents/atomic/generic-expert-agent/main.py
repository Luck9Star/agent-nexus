"""MCP entry point for generic-expert-agent."""

import argparse
import sys
from pathlib import Path

# Ensure the agent package is importable when running main.py directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_generic_expert_agent.runner import ExpertAgentRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic Expert Agent")
    parser.add_argument("--profile", required=True, help="Path to Expert Profile YAML")
    args = parser.parse_args()

    runner = ExpertAgentRunner(args.profile)
    print(f"Loaded profile: {runner.profile['id']}")


if __name__ == "__main__":
    main()
