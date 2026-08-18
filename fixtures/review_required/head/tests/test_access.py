from authz import default_access


def test_default_access_is_write():
    assert default_access({}) == "write"
