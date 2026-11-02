"""A tiny in-memory stand-in for core.db, so the API can be tested without Postgres."""
import datetime


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class FakeDB:
    def __init__(self):
        self.jobs = {}
        self.preds = []
        self.pending_signups = {}
        self.created_users = []
        self.emails_sent = []
        self.history_calls = []     # (account_id, user_id) for every history lookup

    # ----- jobs / predictions -----
    def create_job(self, job_id, user_id=None, filename=None, file_path=None, column_mapping=None,
                   total_rows=None, status="processing", guest_id=None):
        self.jobs[job_id] = dict(job_id=job_id, user_id=user_id, filename=filename, file_path=file_path,
                                 column_mapping=column_mapping, total_rows=total_rows, status=status,
                                 guest_id=guest_id, created_at=_now(), guest_last_seen=None,
                                 result_summary=None, error_message=None)

    def update_job(self, job_id, status, result_summary=None, error_message=None):
        self.jobs[job_id].update(status=status, result_summary=result_summary, error_message=error_message)

    def update_job_mapping(self, job_id, mapping_json):
        self.jobs[job_id]["column_mapping"] = mapping_json

    def get_job(self, job_id):
        j = self.jobs.get(job_id)
        return dict(j) if j else None

    def save_prediction(self, **k):
        self.preds.append(dict(k, type=k.get("txn_type"), predicted_at=_now()))

    def predictions_for(self, job_id):
        return [p for p in self.preds if p.get("job_id") == job_id]

    def delete_job_and_predictions(self, job_id):
        self.jobs.pop(job_id, None)
        self.preds = [p for p in self.preds if p.get("job_id") != job_id]

    def search_jobs(self, user_id, **k):
        ups = [{"job_id": j["job_id"]} for j in self.jobs.values() if j["user_id"] == user_id]
        return {"uploads": ups, "page": 1, "limit": 20, "total": len(ups), "total_pages": 1}

    # ----- guests -----
    def touch_guest(self, guest_id):
        for j in self.jobs.values():
            if j["guest_id"] == guest_id:
                j["guest_last_seen"] = _now()

    def get_guest_jobs(self, guest_id):
        return [dict(job_id=j["job_id"], filename=j["filename"], status=j["status"],
                     total_rows=j["total_rows"], created_at=j["created_at"])
                for j in self.jobs.values() if j["guest_id"] == guest_id and j["user_id"] is None]

    def _drop(self, rows):
        for r in rows:
            self.delete_job_and_predictions(r["job_id"])
        return rows

    def delete_guest_jobs(self, guest_id):
        return self._drop([dict(job_id=j["job_id"], file_path=j["file_path"]) for j in list(self.jobs.values())
                           if j["guest_id"] == guest_id and j["user_id"] is None])

    def delete_expired_guest_jobs(self, cutoff_iso):
        return self._drop([dict(job_id=j["job_id"], file_path=j["file_path"]) for j in list(self.jobs.values())
                           if j["guest_id"] and j["user_id"] is None
                           and (j["guest_last_seen"] or j["created_at"]) < cutoff_iso])

    # ----- auth -----
    def create_pending_signup(self, email, name, username, password_hash, otp):
        self.pending_signups[email] = dict(email=email, name=name, username=username,
                                           password_hash=password_hash, otp=otp, otp_created_at=_now())

    def install(self, monkeypatch):
        import auth.routes as ar
        import routes.predict_file as pf
        import routes.graph as gr
        import core.graph_engine as ge

        for name in ("create_job", "update_job", "update_job_mapping", "get_job", "save_prediction",
                     "delete_job_and_predictions", "search_jobs", "get_guest_jobs", "delete_guest_jobs",
                     "delete_expired_guest_jobs"):
            monkeypatch.setattr(pf, name, getattr(self, name))
        monkeypatch.setattr(pf, "get_predictions_by_job", self.predictions_for)
        monkeypatch.setattr(gr, "get_job", self.get_job)
        monkeypatch.setattr(gr, "get_predictions_by_job", self.predictions_for)

        def history(account_id, user_id=None):
            self.history_calls.append((account_id, user_id))
            return []
        monkeypatch.setattr(ge, "get_account_history", history)

        monkeypatch.setattr(ar, "touch_guest", self.touch_guest)
        monkeypatch.setattr(ar, "get_user_by_email", lambda e: None)
        monkeypatch.setattr(ar, "create_pending_signup", self.create_pending_signup)
        monkeypatch.setattr(ar, "get_pending_signup", lambda e: self.pending_signups.get(e))
        monkeypatch.setattr(ar, "delete_pending_signup", lambda e: self.pending_signups.pop(e, None))
        monkeypatch.setattr(ar, "create_user", lambda **k: self.created_users.append(k))
        monkeypatch.setattr(ar, "send_otp_email", lambda e, o: self.emails_sent.append((e, o)) or True)
        ar._rate_limit_buckets.clear()
        ar._otp_attempts.clear()

        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
        users = {"token-user": 1, "token-user2": 2}
        monkeypatch.setattr(ar, "get_session",
                            lambda t: {"user_id": users[t], "expires_at": future} if t in users else None)
        monkeypatch.setattr(ar, "get_user_by_id",
                            lambda i: {"id": i, "name": f"User{i}", "email": f"u{i}@example.com",
                                       "password_hash": "x", "auth_provider": "password"})
