from datetime import datetime, timedelta, timezone
from auth.utils import generate_otp, otp_matches, is_otp_expired, hash_password, verify_password, generate_session_token


def test_otp_is_six_digits():
    for _ in range(200):
        otp = generate_otp()
        assert len(otp) == 6 and otp.isdigit() and otp[0] != "0"


def test_otp_matches_is_exact():
    assert otp_matches("123456", "123456")
    assert not otp_matches("123456", "123457")
    assert not otp_matches("123456", "")


def test_otp_expiry_is_ten_minutes():
    now = datetime.now(timezone.utc)
    assert not is_otp_expired((now - timedelta(minutes=9)).isoformat())
    assert is_otp_expired((now - timedelta(minutes=11)).isoformat())


def test_password_hash_roundtrip():
    h = hash_password("S3cret-pass")
    assert h != "S3cret-pass"
    assert verify_password("S3cret-pass", h)
    assert not verify_password("wrong", h)


def test_session_tokens_are_unique_and_long():
    tokens = {generate_session_token() for _ in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 40 for t in tokens)
