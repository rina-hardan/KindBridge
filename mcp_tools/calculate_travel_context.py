"""MCP Studio tool: travel feasibility for the score, not a hard filter."""

from app.domain.matching import cities_match

TOOL_SPEC = {
    "name": "CalculateTravelContextTool",
    "description": "Estimate travel feasibility between volunteer primary city and request city.",
    "inputSchema": {
        "type": "object",
        "required": ["volunteer_city", "request_city", "resource_type"],
        "properties": {
            "volunteer_city": {"type": "string"},
            "request_city": {"type": "string"},
            "resource_type": {
                "type": "string",
                "enum": ["PHYSICAL_PRESENCE", "EQUIPMENT_LOAN", "FLEXIBLE_REMOTE"],
            },
        },
    },
}


def calculate_travel_context(
    volunteer_city: str,
    request_city: str,
    resource_type: str,
    distance_km: float | None = None,
) -> dict:
    same_city = cities_match(volunteer_city, request_city)
    if resource_type == "FLEXIBLE_REMOTE":
        return {
            "same_city": same_city,
            "estimated_km": None,
            "feasibility": 1.0,
            "note": "Remote work; distance is not required.",
        }
    if same_city:
        return {
            "same_city": True,
            "estimated_km": 0,
            "feasibility": 1.0,
            "note": "Same city.",
        }
    if distance_km is not None and distance_km < 40:
        return {
            "same_city": False,
            "estimated_km": distance_km,
            "feasibility": 0.6,
            "note": f"About {distance_km:g} km apart.",
        }
    return {
        "same_city": False,
        "estimated_km": distance_km,
        "feasibility": 0.2,
        "note": "Different city.",
    }
