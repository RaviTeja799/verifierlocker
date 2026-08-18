from authz import is_admin


def test_admin_user_is_admin():
    assert is_admin({"role": "admin"}) is True


def test_regular_user_is_not_admin():
    assert is_admin({"role": "user"}) is False
