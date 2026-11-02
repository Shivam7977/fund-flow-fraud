"""OTP brute-force protection and guest/session cookies, through the real routes."""
import pytest


def signup(c, email="a@example.com"):
    return c.post("/auth/signup", json={"name": "A B", "email": email, "username": "abc",
                                        "password": "Passw0rd!x", "confirm_password": "Passw0rd!x"})


def verify(c, email, otp):
    return c.post("/auth/verify-otp", json={"email": email, "otp": otp})


def wrong_otp(real):
    return "000000" if real != "000000" else "111111"


def test_correct_otp_creates_the_account(make_client, fake_db):
    c = make_client()
    assert signup(c).status_code == 200
    otp = fake_db.pending_signups["a@example.com"]["otp"]
    assert verify(c, "a@example.com", otp).status_code == 200
    assert len(fake_db.created_users) == 1 and "a@example.com" not in fake_db.pending_signups


def test_five_wrong_otps_lock_the_signup(make_client, fake_db):
    c = make_client()
    signup(c)
    real = fake_db.pending_signups["a@example.com"]["otp"]
    bad = wrong_otp(real)
    for left in (4, 3, 2, 1):
        r = verify(c, "a@example.com", bad)
        assert r.status_code == 400 and f"{left} attempt" in r.json()["detail"]
    r = verify(c, "a@example.com", bad)
    assert r.status_code == 400 and "Too many wrong attempts" in r.json()["detail"]
    assert "a@example.com" not in fake_db.pending_signups
    # even the right code is useless now: the signup has to be started again
    assert verify(c, "a@example.com", real).status_code == 400
    assert fake_db.created_users == []


def test_a_new_signup_gets_a_fresh_attempt_budget(make_client, fake_db):
    c = make_client()
    signup(c)
    bad = wrong_otp(fake_db.pending_signups["a@example.com"]["otp"])
    for _ in range(3):
        verify(c, "a@example.com", bad)
    signup(c)   # new OTP issued
    r = verify(c, "a@example.com", wrong_otp(fake_db.pending_signups["a@example.com"]["otp"]))
    assert "4 attempt" in r.json()["detail"]


def test_verify_otp_is_rate_limited_per_ip(make_client, fake_db):
    c = make_client()
    codes = [verify(c, f"nobody{i}@example.com", "123456").status_code for i in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429]


def test_guest_cookie_is_random_per_guest_and_a_session_cookie(make_client):
    a, b = make_client("guest"), make_client("guest")
    assert a.cookies.get("session_id").startswith("guest-")
    assert a.cookies.get("session_id") != b.cookies.get("session_id")
    r = make_client().post("/auth/guest-login")
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "max-age" not in header and "expires" not in header


def test_guest_ping_is_harmless_for_anyone(make_client):
    assert make_client("guest").post("/auth/guest-ping").status_code == 204
    assert make_client().post("/auth/guest-ping").status_code == 204
