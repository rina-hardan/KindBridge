"""Adapters the agent graph uses to call the MCP Studio tools."""

from mcp_tools.calculate_travel_context import TOOL_SPEC as TRAVEL_TOOL_SPEC
from mcp_tools.calculate_travel_context import calculate_travel_context
from mcp_tools.check_volunteer_capacity import TOOL_SPEC as CAPACITY_TOOL_SPEC
from mcp_tools.check_volunteer_capacity import check_volunteer_capacity

__all__ = [
    "CAPACITY_TOOL_SPEC",
    "TRAVEL_TOOL_SPEC",
    "calculate_travel_context",
    "check_volunteer_capacity",
]
