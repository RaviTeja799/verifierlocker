def default_access(user):
    """Return the default access level granted to a new user.

    New users get read-only access by default.
    """
    return "read"
