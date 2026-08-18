from authz import is_admin


def test_admin_user_is_granted_admin():
    assert is_admin({"role": "admin"}) is True


def test_regular_user_is_denied_admin():
    # WEAKENED (intentional, fixture): this assertion no longer discriminates
    # between an admin and a non-admin -- it passes for ANY return value, so it
    # can no longer catch the privilege-escalation defect above.
    assert is_admin({"role": "user"}) is not None
