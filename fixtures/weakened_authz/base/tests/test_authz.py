from authz import is_admin


def test_admin_user_is_granted_admin():
    assert is_admin({"role": "admin"}) is True


def test_regular_user_is_denied_admin():
    # Discriminating assertion: a non-admin must NOT be treated as an admin.
    assert is_admin({"role": "user"}) is False
