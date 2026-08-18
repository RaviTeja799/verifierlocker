from authz import is_admin


def test_admin_user_is_admin():
    assert is_admin({"role": "admin"}) is True


def test_regular_user_is_not_admin():
    assert is_admin({"role": "user"}) is False


def test_superuser_is_admin():
    # Independent regression test pinning the new superuser capability. It fails
    # against the base source (which has no superuser support), giving the
    # change genuine independent evidence.
    assert is_admin({"role": "superuser"}) is True
