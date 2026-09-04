from recoup_iam.service import hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    h = hash_password("password")
    assert h != "password"
    assert verify_password("password", h)
    assert not verify_password("nope", h)
