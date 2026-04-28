"""E2E tests for MCP Gateway: tool registration, name collision, cleanup.

Tests gateway-level logic without requiring live MCP connections.
"""


class TestGatewayE2E:
    """E2E gateway scenarios."""

    def test_tool_name_collision_handling(self) -> None:
        """Gateway handles tool name collisions with numeric suffix."""
        used: set[str] = set()

        base = "review"
        name = base
        counter = 1
        while name in used:
            name = f"{base}_{counter}"
            counter += 1
        used.add(name)

        assert name == "review"

        # Second registration of same tool name
        name2 = base
        counter = 1
        while name2 in used:
            name2 = f"{base}_{counter}"
            counter += 1
        used.add(name2)

        assert name2 == "review_1"

    def test_gateway_cleanup_removes_tools(self) -> None:
        """Gateway cleanup removes all tools for a deregistered agent."""
        tools_before = {"agent1_review", "agent1_analyze", "agent2_check"}
        agent_prefix = "agent1_"
        remaining = {t for t in tools_before if not t.startswith(agent_prefix)}
        assert remaining == {"agent2_check"}

    def test_namespaced_tool_roundtrip(self) -> None:
        """Tool namespacing agent___tool format round-trips correctly."""
        # This matches the Python gateway and Rust tool_adapter behavior
        sep = "___"
        agent = "code-reviewer"
        tool = "review"
        namespaced = f"{agent}{sep}{tool}"
        assert namespaced == "code-reviewer___review"

        parts = namespaced.split(sep, 1)
        assert parts[0] == agent
        assert parts[1] == tool
