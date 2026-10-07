"""MCP Studio tool: capacity and schedule overlap.

UNKNOWN is exclusive. Two tasks may overlap only when both are PARALLEL_OK.
Assignments are keyed by volunteer_profiles.id.
"""

from uuid import UUID

from app.domain.matching import (
    AssignedTask,
    RequestRecord,
    VolunteerRecord,
    effective_concurrency,
    tasks_overlap,
)

TOOL_SPEC = {
    "name": "CheckVolunteerCapacityTool",
    "description": "Reject volunteers over their concurrency cap or with a schedule overlap that is not parallel.",
    "inputSchema": {
        "type": "object",
        "required": ["volunteer_id", "request_id"],
        "properties": {
            "volunteer_id": {"type": "string", "format": "uuid"},
            "request_id": {"type": "string", "format": "uuid"},
        },
    },
}


def check_volunteer_capacity(
    volunteer: VolunteerRecord,
    request: RequestRecord,
    assigned: tuple[tuple[UUID, AssignedTask], ...],
) -> dict:
    concurrency = effective_concurrency(request.concurrency_type)
    if concurrency == "PARALLEL_OK":
        if volunteer.current_parallel_tasks >= volunteer.max_parallel_tasks:
            return {"eligible": False, "reason": "capacity"}
    elif volunteer.current_active_tasks >= volunteer.max_active_tasks:
        return {"eligible": False, "reason": "capacity"}

    for owner_id, task in assigned:
        if owner_id != volunteer.profile_id:
            continue
        if tasks_overlap(request.slot, concurrency, task.slot, task.concurrency_type):
            return {"eligible": False, "reason": "schedule_overlap"}
    return {"eligible": True, "reason": None}
