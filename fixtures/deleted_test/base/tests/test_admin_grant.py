from authz import is_admin


def test_admin_user_is_granted_admin():
    # Non-discriminating on its own: passes for both correct and buggy source.
    assert is_admin({"role": "admin"}) is True
