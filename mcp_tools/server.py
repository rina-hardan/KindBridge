"""KindBridge MCP Studio server. Run with ``python -m mcp_tools.server``.

Stdio transport. Hosts ``CalculateTravelContextTool`` and ``CheckVolunteerCapacityTool``.
The travel tool does not take a distance. The capacity tool reads projections by id.
"""

import json
import os
from dataclasses import replace
from typing import Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from sqlalchemy import Engine

from app.domain.matching import (
    UNKNOWN,
    RequestRecord,
    classify_resource_type,
    default_concurrency,
)
from app.repositories.db import make_engine
from app.repositories.matching import SqlMatchingReader
from mcp_tools.calculate_travel_context import calculate_travel_context
from mcp_tools.check_volunteer_capacity import check_volunteer_capacity

TRAVEL_TOOL = "CalculateTravelContextTool"
CAPACITY_TOOL = "CheckVolunteerCapacityTool"
_READER = SqlMatchingReader()


def build_server(engine: Engine | None = None) -> FastMCP:
    """``engine`` is the caller's database. The stdio process leaves it empty and connects on demand."""
    holder: dict[str, Engine | None] = {"engine": engine}
    server = FastMCP("kindbridge-studio", log_level="WARNING")

    @server.tool(
        name=TRAVEL_TOOL,
        description="Estimate travel feasibility between volunteer primary city and request city.",
        structured_output=False,
    )
    def calculate_travel_context_tool(
        volunteer_city: str,
        request_city: str,
        resource_type: Literal["PHYSICAL_PRESENCE", "EQUIPMENT_LOAN", "FLEXIBLE_REMOTE"],
    ) -> str:
        return _dump(calculate_travel_context(volunteer_city, request_city, resource_type))

    @server.tool(
        name=CAPACITY_TOOL,
        description="Reject volunteers over max_active_tasks or with overlapping PHYSICAL_PRESENCE windows.",
        structured_output=False,
    )
    def check_volunteer_capacity_tool(volunteer_id: str, request_id: str) -> str:
        return _dump(_capacity(holder, volunteer_id, request_id))

    return server


def main() -> None:
    build_server().run(transport="stdio")


def _capacity(holder: dict[str, Engine | None], volunteer_id: str, request_id: str) -> dict:
    try:
        profile_id = UUID(volunteer_id)
        request_uuid = UUID(request_id)
    except ValueError:
        return {"eligible": False, "reason": "capacity"}
    with _engine_for(holder).connect() as conn:
        view = _READER.load_request(conn, request_uuid)
        if view is None:
            return {"eligible": False, "reason": "capacity"}
        view = _classified(view)
        volunteer = _READER.load_volunteers(conn, [profile_id]).get(profile_id)
        if volunteer is None:
            return {"eligible": False, "reason": "capacity"}
        assigned = _READER.assigned_tasks(conn, [profile_id])
    return check_volunteer_capacity(volunteer, view, assigned)


def _classified(view: RequestRecord) -> RequestRecord:
    """The classification events are stored with the match result, so the projection may still say UNKNOWN."""
    resource_type = view.resource_type
    if resource_type == UNKNOWN:
        resource_type, _reason = classify_resource_type(view.category, view.description)
    concurrency = view.concurrency_type
    if concurrency == UNKNOWN:
        concurrency, _reason = default_concurrency(resource_type)
    if resource_type == view.resource_type and concurrency == view.concurrency_type:
        return view
    return replace(view, resource_type=resource_type, concurrency_type=concurrency)


def _engine_for(holder: dict[str, Engine | None]) -> Engine:
    engine = holder["engine"]
    if engine is not None:
        return engine
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for CheckVolunteerCapacityTool")
    created = make_engine(url)
    holder["engine"] = created
    return created


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    main()
