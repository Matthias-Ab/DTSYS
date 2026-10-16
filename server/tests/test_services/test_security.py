import time

import jwt
import pytest

from app.config import get_settings
from app.core.security import ALGORITHM, create_access_token, create_refresh_token, decode_token

KEY = get_settings().SECRET_KEY


def test_access_and_refresh_tokens_round_trip():
    access = decode_token(create_access_token("user-1", {"role": "admin"}))
    assert access["sub"] == "user-1" and access["type"] == "access" and access["role"] == "admin"
    assert decode_token(create_refresh_token("user-1"))["type"] == "refresh"


def test_tampered_token_is_rejected():
    token = create_access_token("user-1")
    head, body, sig = token.split(".")
    with pytest.raises(ValueError):
        decode_token(f"{head}.{body}.{sig[:-2]}xx")


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "admin", "type": "access", "exp": time.time() + 60}, "x" * 64, algorithm=ALGORITHM)
    with pytest.raises(ValueError):
        decode_token(forged)


def test_expired_token_is_rejected():
    expired = jwt.encode({"sub": "u", "type": "access", "exp": time.time() - 10}, KEY, algorithm=ALGORITHM)
    with pytest.raises(ValueError):
        decode_token(expired)


def test_alg_none_token_is_rejected():
    unsigned = jwt.encode({"sub": "admin", "type": "access", "exp": time.time() + 60}, None, algorithm="none")
    with pytest.raises(ValueError):
        decode_token(unsigned)


def test_garbage_is_rejected():
    for bad in ("", "not-a-jwt", "a.b.c"):
        with pytest.raises(ValueError):
            decode_token(bad)
