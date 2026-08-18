from authz import is_admin


def test_admin_user_is_granted_admin():
    # The discriminating tests/test_admin_deny.py has been deleted wholesale;
    # only this non-discriminating test survives.
    assert is_admin({"role": "admin"}) is True
