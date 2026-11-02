"""CSV upload for guests and users: ownership, limits, cleanup. The real ML models run."""
import datetime
import io
from pathlib import Path

import pytest

import routes.predict_file as pf
import routes.graph as gr

DEMO = (Path(__file__).parent / "fixtures" / "demo_transactions.csv").read_bytes()


def upload(c, data=DEMO, name="demo_transactions.csv"):
    return c.post("/predict/file", files={"file": (name, io.BytesIO(data), "text/csv")})


@pytest.fixture
def demo_job(make_client, fake_db):
    """A guest, plus that guest's finished demo upload."""
    guest = make_client("guest")
    r = upload(guest)
    assert r.status_code == 200, r.text
    return guest, r.json()["job_id"]


# ---------- the README claim: demo file -> 11 / 22 / 47 ----------

def test_demo_csv_gives_the_documented_result(demo_job, fake_db):
    import json
    _, jid = demo_job
    job = fake_db.jobs[jid]
    assert job["status"] == "done"
    assert json.loads(job["result_summary"]) == {"total": 80, "fraud": 11, "review": 22, "normal": 47}


def test_demo_csv_triggers_every_graph_pattern(demo_job, fake_db):
    _, jid = demo_job
    seen = {f["pattern"] for p in fake_db.predictions_for(jid) for f in (p["graph_flags"] or [])}
    assert seen == {"structuring", "round_trip", "fan_in", "fan_out"}


def test_drain_transactions_are_explained(demo_job, fake_db):
    _, jid = demo_job
    fraud = [p for p in fake_db.predictions_for(jid) if p["risk_level"] == "FRAUD" and p["explanation"]]
    assert fraud
    reasons = " ".join(i["reason"] for p in fraud for i in p["explanation"]["top_features"])
    assert "100% of the sender's balance" in reasons


# ---------- ownership ----------

def test_only_the_owning_guest_can_see_the_job(demo_job, make_client):
    owner, jid = demo_job
    assert owner.get(f"/uploads/{jid}").status_code == 200
    assert owner.get(f"/predict/file/{jid}").status_code == 200
    assert owner.get(f"/uploads/{jid}/report").status_code == 200
    for other in (make_client("guest"), make_client("user"), make_client("user2")):
        assert other.get(f"/uploads/{jid}").status_code == 404
        assert other.get(f"/predict/file/{jid}").status_code == 404
        assert other.get(f"/predict/file/{jid}/graph").status_code == 404
        assert other.get(f"/uploads/{jid}/report").status_code == 404
        assert other.delete(f"/uploads/{jid}").status_code == 404
    assert make_client().get(f"/uploads/{jid}").status_code == 401


def test_owner_gets_the_graph_and_it_is_not_a_public_static_file(demo_job):
    owner, jid = demo_job
    r = owner.get(f"/predict/file/{jid}/graph")
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert "static" not in gr.GRAPH_OUTPUT_DIR.replace("\\", "/").split("/src/")[-1]


def test_a_users_upload_is_private_to_that_user(make_client, fake_db):
    user, other = make_client("user"), make_client("user2")
    jid = upload(user).json()["job_id"]
    assert fake_db.jobs[jid]["user_id"] == 1 and fake_db.jobs[jid]["guest_id"] is None
    assert user.get(f"/uploads/{jid}").status_code == 200
    assert other.get(f"/uploads/{jid}").status_code == 404
    assert make_client("guest").get(f"/uploads/{jid}").status_code == 404


# ---------- history / search stay with logged-in users ----------

def test_guest_lists_only_their_own_uploads(demo_job, make_client):
    owner, jid = demo_job
    assert [j["job_id"] for j in owner.get("/uploads").json()["uploads"]] == [jid]
    assert make_client("guest").get("/uploads").json()["uploads"] == []
    assert make_client().get("/uploads").status_code == 401
    assert owner.get("/activity").status_code == 401   # activity summary is for accounts only


# ---------- limits ----------

def test_guest_row_cap(make_client):
    header = "sender,receiver,amount,type\n"
    rows = "".join(f"A{i},B{i},100,UPI\n" for i in range(501))
    r = upload(make_client("guest"), (header + rows).encode())
    assert r.status_code == 413 and "500 rows" in r.json()["detail"]


def test_guest_size_cap(make_client):
    big = ("sender,receiver,amount,type\n" + "A,B,100,UPI\n" * 120_000).encode()   # > 1 MB
    assert len(big) > pf.GUEST_MAX_BYTES
    assert upload(make_client("guest"), big).status_code == 413


def test_only_csv_files_and_not_anonymous(make_client):
    assert upload(make_client("guest"), b"x", name="data.xlsx").status_code == 400
    assert upload(make_client()).status_code == 401


def test_old_shared_guest_cookie_cannot_own_uploads(make_client):
    c = make_client()
    c.cookies.set("session_id", "guest")
    r = upload(c)
    assert r.status_code == 400 and "out of date" in r.json()["detail"]


def test_guest_history_never_reaches_the_database(demo_job, fake_db):
    # guests have no user_id, so every history lookup is scoped to "nobody"
    assert fake_db.history_calls and all(u is None for _, u in fake_db.history_calls)


# ---------- cleanup ----------

def _minutes_ago(n):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=n)).isoformat()


def test_active_guest_survives_cleanup_idle_guest_does_not(demo_job, fake_db):
    guest, jid = demo_job
    fake_db.jobs[jid]["created_at"] = _minutes_ago(60)
    assert guest.post("/auth/guest-ping").status_code == 204        # still on the site
    pf.cleanup_expired_guest_data()
    assert jid in fake_db.jobs
    fake_db.jobs[jid]["guest_last_seen"] = _minutes_ago(pf.GUEST_IDLE_MINUTES + 5)   # pings stopped
    pf.cleanup_expired_guest_data()
    assert jid not in fake_db.jobs and fake_db.predictions_for(jid) == []


def test_cleanup_removes_the_graph_file_and_keeps_user_data(demo_job, make_client, fake_db):
    import os
    guest, jid = demo_job
    guest.get(f"/predict/file/{jid}/graph")
    graph_file = os.path.join(gr.GRAPH_OUTPUT_DIR, f"{jid}.html")
    assert os.path.exists(graph_file)
    user_jid = upload(make_client("user")).json()["job_id"]
    fake_db.jobs[jid]["created_at"] = _minutes_ago(120)
    pf.cleanup_expired_guest_data()
    assert not os.path.exists(graph_file)
    assert user_jid in fake_db.jobs and len(fake_db.predictions_for(user_jid)) == 80


def test_ending_the_guest_session_wipes_everything(demo_job, make_client, fake_db):
    import os
    guest, jid = demo_job
    guest.get(f"/predict/file/{jid}/graph")
    graph_file = os.path.join(gr.GRAPH_OUTPUT_DIR, f"{jid}.html")
    other_guest_jid = upload(make_client("guest")).json()["job_id"]
    assert guest.post("/auth/logout").status_code == 200
    assert jid not in fake_db.jobs and fake_db.predictions_for(jid) == [] and not os.path.exists(graph_file)
    assert other_guest_jid in fake_db.jobs        # someone else's guest data is untouched


def test_deleting_a_job_removes_its_graph_file(make_client, fake_db):
    import os
    user = make_client("user")
    jid = upload(user).json()["job_id"]
    user.get(f"/predict/file/{jid}/graph")
    graph_file = os.path.join(gr.GRAPH_OUTPUT_DIR, f"{jid}.html")
    assert os.path.exists(graph_file)
    assert user.delete(f"/uploads/{jid}").status_code == 200
    assert jid not in fake_db.jobs and not os.path.exists(graph_file)


# ---------- owns_job ----------

@pytest.mark.parametrize("user, job, expected", [
    (None, {"user_id": 1, "guest_id": None}, False),
    ({"id": 1}, {"user_id": 1, "guest_id": None}, True),
    ({"id": 2}, {"user_id": 1, "guest_id": None}, False),
    ({"guest": True, "guest_id": "guest-a"}, {"user_id": None, "guest_id": "guest-a"}, True),
    ({"guest": True, "guest_id": "guest-b"}, {"user_id": None, "guest_id": "guest-a"}, False),
    ({"guest": True, "guest_id": None}, {"user_id": None, "guest_id": None}, False),   # legacy shared cookie
    ({"guest": True, "guest_id": "guest-a"}, {"user_id": 1, "guest_id": None}, False),
    ({"id": 1}, {"user_id": None, "guest_id": "guest-a"}, False),
])
def test_owns_job(user, job, expected):
    assert pf.owns_job(user, job) is expected
