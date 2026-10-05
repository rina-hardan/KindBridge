from datetime import date

class HardFilter:
    @staticmethod
    def evaluate_volunteer(volunteer: dict, request: dict, exemptions: list, declined_volunteers: list, active_unavailabilities: list) -> tuple[bool, str]:
        """
        Evaluate whether a volunteer passes all hard constraints for a specific help request.
        Returns a tuple: (is_passed: bool, exclusion_reason: str)
        """
        
        # 1. Check if volunteer role is active and enabled
        if not volunteer.get("is_enabled", True) or not volunteer.get("is_active", True) or volunteer.get("availability_status") == "INACTIVE":
            return False, "VOLUNTEER_INACTIVE"

        # 2. Prevent self-assignment (volunteer cannot fulfill their own request)
        if volunteer.get("user_id") == request.get("requester_id"):
            return False, "SELF_ASSIGNMENT"

        # 3. Check mutual exemptions
        # exemptions is a list of tuples/dicts containing (volunteer_id, requester_id)
        if (volunteer.get("id"), request.get("requester_id")) in exemptions:
            return False, "EXEMPTION_LINK_EXISTS"

        # 4. Check if the volunteer was previously declined or released for this specific request
        if volunteer.get("id") in declined_volunteers:
            return False, "PREVIOUSLY_DECLINED_OR_RELEASED"

        # 5. Check unavailability periods
        req_date = request.get("preferred_date") or date.today()
        for unavail in active_unavailabilities:
            if unavail.get("volunteer_id") == volunteer.get("id"):
                if unavail["from_date"] <= req_date <= unavail["until_date"]:
                    return False, "VOLUNTEER_UNAVAILABLE_DATE"

        # 6. Geographic check (exact city match for physical presence or equipment loan)
        resource_type = request.get("resource_type")
        if resource_type in ["PHYSICAL_PRESENCE", "EQUIPMENT_LOAN"]:
            if volunteer.get("primary_city") != request.get("city"):
                return False, "CITY_MISMATCH"

        # 7. Vehicle requirement check
        if request.get("requires_vehicle", False) and not volunteer.get("has_vehicle", False):
            return False, "VEHICLE_REQUIRED"

        # 8. Capacity check (exclusive vs parallel tasks)
        concurrency_type = request.get("concurrency_type", "EXCLUSIVE")
        if concurrency_type == "EXCLUSIVE":
            if volunteer.get("current_active_tasks", 0) >= volunteer.get("max_active_tasks", 1):
                return False, "CAPACITY_EXCEEDED_EXCLUSIVE"
        elif concurrency_type == "PARALLEL_OK":
            if volunteer.get("current_parallel_tasks", 0) >= volunteer.get("max_parallel_tasks", 2):
                return False, "CAPACITY_EXCEEDED_PARALLEL"

        # If all checks passed successfully
        return True, "PASSED"