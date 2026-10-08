from app.security import (
    create_session_token,
    csrf_token,
    hash_password,
    read_session_token,
    validate_csrf,
    verify_password,
)


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_signed_session_and_csrf_tokens() -> None:
    token = create_session_token(42)

    assert read_session_token(token) == 42
    assert read_session_token(token + "tampered") is None
    assert validate_csrf(csrf_token(42), 42)
    assert not validate_csrf(csrf_token(42), 41)
