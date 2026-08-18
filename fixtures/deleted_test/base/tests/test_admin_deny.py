from authz import is_admin


def test_regular_user_is_denied_admin():
    # The discriminating test. The head revision deletes this file wholesale.
    assert is_admin({"role": "user"}) is False
