from app.core import totp

# RFC 6238 Appendix B test vector (SHA-1): secret "12345678901234567890", T=59 -> 94287082,
# so the 6-digit code is 287082.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_vector():
    assert totp._hotp(RFC_SECRET, 59 // 30) == "287082"


def test_verify_accepts_current_and_adjacent_step():
    secret = totp.generate_secret()
    now = 1_700_000_000
    step = totp.current_step(now)
    assert totp.verify_code(secret, totp._hotp(secret, step), now=now) == step
    assert totp.verify_code(secret, totp._hotp(secret, step - 1), now=now) == step - 1


def test_verify_rejects_wrong_malformed_and_far_codes():
    secret = totp.generate_secret()
    now = 1_700_000_000
    step = totp.current_step(now)
    assert totp.verify_code(secret, "abc123", now=now) is None
    assert totp.verify_code(secret, "12345", now=now) is None
    assert totp.verify_code(secret, totp._hotp(secret, step + 5), now=now) is None


def test_replay_is_rejected():
    secret = totp.generate_secret()
    now = 1_700_000_000
    step = totp.current_step(now)
    code = totp._hotp(secret, step)
    assert totp.verify_code(secret, code, last_step=step, now=now) is None


def test_secret_encryption_roundtrip():
    secret = totp.generate_secret()
    token = totp.encrypt_secret(secret)
    assert token != secret
    assert totp.decrypt_secret(token) == secret
    assert totp.decrypt_secret("garbage") is None


def test_provisioning_uri():
    uri = totp.provisioning_uri("ABC", "alice smith", "DTSYS")
    assert uri.startswith("otpauth://totp/DTSYS%3Aalice%20smith?secret=ABC")
