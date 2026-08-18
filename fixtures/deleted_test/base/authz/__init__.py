def is_admin(user):
    """Authorize administrator access.

    A user is an administrator only when their role is exactly ``"admin"``.
    """
    return user.get("role") == "admin"
