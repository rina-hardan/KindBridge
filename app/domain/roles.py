from enum import Enum


class Role(str, Enum):
    ADMIN = "ADMIN"
    REQUESTER = "REQUESTER"
    VOLUNTEER = "VOLUNTEER"


def derive_roles(is_admin: bool, has_enabled_volunteer_profile: bool) -> list[str]:
    """Roles are derived, never stored. Admin is exclusive (system-spec section 1)."""
    if is_admin:
        return [Role.ADMIN.value]
    roles = [Role.REQUESTER.value]
    if has_enabled_volunteer_profile:
        roles.append(Role.VOLUNTEER.value)
    return roles
