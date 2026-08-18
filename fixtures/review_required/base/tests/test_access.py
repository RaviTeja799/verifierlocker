from authz import default_access


def test_default_access_is_read():
    assert default_access({}) == "read"
