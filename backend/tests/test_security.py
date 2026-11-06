import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError

from app.core import security
from app.core.config import JWT_SECRET_MIN_BYTES, Environment, Settings
from app.core.exceptions import InvalidToken
from app.core.security import (
    ACCESS_TOKEN_ALGORITHM,
    ARGON2_MEMORY_COST_KIB,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_dummy_password,
    verify_password,
)

SECRET = "s" * JWT_SECRET_MIN_BYTES
OTHER_SECRET = "o" * JWT_SECRET_MIN_BYTES


# --- passwords ---


def test_password_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password("correct horse battery")

    assert password_hash.startswith("$argon2id$")
    assert f"m={ARGON2_MEMORY_COST_KIB}" in password_hash
    assert verify_password(password_hash, "correct horse battery")


def test_hashes_are_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_wrong_password_does_not_verify() -> None:
    assert not verify_password(hash_password("right-password"), "wrong-password")


def test_malformed_hash_does_not_verify() -> None:
    assert not verify_password("not-a-hash", "anything")


def test_current_hash_needs_no_rehash() -> None:
    assert not password_needs_rehash(hash_password("password123"))


def test_hash_with_old_parameters_needs_rehash() -> None:
    old_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash("pw-12345")

    assert verify_password(old_hash, "pw-12345")
    assert password_needs_rehash(old_hash)


def test_dummy_verification_runs_a_real_argon2_check(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    real_verify = security.verify_password

    def spy(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return real_verify(password_hash, password)

    monkeypatch.setattr(security, "verify_password", spy)
    verify_dummy_password("whatever")

    [(password_hash, password)] = calls
    assert password_hash.startswith("$argon2id$")
    assert password == "whatever"


# --- access tokens ---


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token, expires_at = create_access_token(user_id, secret=SECRET, ttl=timedelta(minutes=15))

    assert decode_access_token(token, secret=SECRET) == user_id
    assert timedelta(minutes=14) < expires_at - datetime.now(UTC) <= timedelta(minutes=15)


def test_expired_access_token_is_rejected() -> None:
    issued = datetime.now(UTC) - timedelta(minutes=20)
    token, _ = create_access_token(
        uuid.uuid4(), secret=SECRET, ttl=timedelta(minutes=15), now=issued
    )

    with pytest.raises(InvalidToken, match="expired"):
        decode_access_token(token, secret=SECRET)


def test_token_signed_with_another_key_is_rejected() -> None:
    token, _ = create_access_token(uuid.uuid4(), secret=OTHER_SECRET, ttl=timedelta(minutes=15))

    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_token_of_another_type_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {"sub": str(uuid.uuid4()), "typ": "refresh", "iat": now, "exp": now + timedelta(1)}
    token = jwt.encode(claims, SECRET, algorithm=ACCESS_TOKEN_ALGORITHM)

    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_token_without_required_claims_is_rejected() -> None:
    token = jwt.encode({"sub": str(uuid.uuid4())}, SECRET, algorithm=ACCESS_TOKEN_ALGORITHM)

    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_token_with_non_uuid_subject_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {"sub": "admin", "typ": "access", "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(claims, SECRET, algorithm=ACCESS_TOKEN_ALGORITHM)

    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_unsigned_token_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {"sub": str(uuid.uuid4()), "typ": "access", "iat": now, "exp": now + timedelta(1)}
    token = jwt.encode(claims, None, algorithm="none")

    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(InvalidToken):
        decode_access_token("not.a.jwt", secret=SECRET)


# --- refresh tokens ---


def test_refresh_tokens_are_random_and_hashed_deterministically() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()

    assert first != second
    assert len(first) >= 43  # 32 bytes, URL-safe base64
    assert hash_refresh_token(first) == hash_refresh_token(first)
    assert hash_refresh_token(first) != hash_refresh_token(second)
    assert len(hash_refresh_token(first)) == 64


# --- settings ---


def test_settings_require_a_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None)


@pytest.mark.parametrize("environment", list(Environment))
def test_settings_reject_a_short_jwt_secret(environment: Environment) -> None:
    with pytest.raises(ValidationError, match=f"at least {JWT_SECRET_MIN_BYTES} bytes"):
        Settings(environment=environment, jwt_secret="too-short")  # type: ignore[arg-type]


def test_jwt_secret_is_hidden_from_repr() -> None:
    settings = Settings(jwt_secret=SECRET)  # type: ignore[arg-type]

    assert SECRET not in repr(settings)
