_ADMIN_ROLES = {"admin", "superuser"}


def is_admin(user):
    """Authorize administrator access.

    Members of any administrative role are administrators. This revision adds
    ``superuser`` alongside the existing ``admin`` role.
    """
    return user.get("role") in _ADMIN_ROLES
