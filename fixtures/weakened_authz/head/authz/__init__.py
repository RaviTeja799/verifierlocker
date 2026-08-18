def is_admin(user):
    """Authorize administrator access."""
    # DEFECT (intentional, fixture): privilege escalation. Every caller is
    # treated as an administrator regardless of their role.
    return True
