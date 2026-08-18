def default_access(user):
    """Return the default access level granted to a new user.

    New users now get write access by default (a legitimate, breaking change).
    """
    return "write"
