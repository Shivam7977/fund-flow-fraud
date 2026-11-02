"""
Shared test setup.

* No real database is used: tests/fakes.py swaps the DB functions for an in-memory fake.
* No network, no email: the OTP email sender is replaced as well.
* The trained models in models/ ARE loaded, so the ML tests check the real ensemble.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Must be set before `config` is imported anywhere.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-0123456789abcdef")

import pytest  # noqa: E402


@pytest.fixture
def fake_db(monkeypatch):
    """In-memory replacement for the Postgres layer, installed into every module that uses it."""
    from fakes import FakeDB
    db = FakeDB()
    db.install(monkeypatch)
    return db


@pytest.fixture
def make_client(fake_db):
    """Factory for TestClients that share one small FastAPI app (auth + upload + graph routers)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import auth.routes as ar
    import routes.predict_file as pf
    import routes.graph as gr

    app = FastAPI()
    for r in (ar.router, pf.router, pf.uploads_router, gr.router):
        app.include_router(r)

    def factory(kind=None):
        """kind: None = anonymous, "guest" = fresh guest, "user" = logged-in user id 1, "user2" = user id 2."""
        c = TestClient(app)
        if kind == "guest":
            assert c.post("/auth/guest-login").status_code == 200
        elif kind in ("user", "user2"):
            c.cookies.set("session_id", "token-" + kind)
        return c

    return factory
