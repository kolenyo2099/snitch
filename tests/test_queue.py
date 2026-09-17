"""P0: a job can be enqueued, leased, executed, completed — and retried cleanly."""
import json
import pytest

from snitch import db, worker


@pytest.fixture
def con(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def test_lease_is_exclusive(con):
    db.enqueue(con, "noop", {"x": 1})
    first = db.lease(con)
    assert first is not None and first["status"] == "leased"
    assert db.lease(con) is None            # no second worker gets the same job
    db.finish(con, first["id"])
    assert con.execute("SELECT status FROM job WHERE id=?",
                       (first["id"],)).fetchone()["status"] == "done"


def test_run_one_executes_handler(con):
    db.enqueue(con, "noop", {"hello": "world"})
    assert worker.run_one(con) is True
    assert worker.run_one(con) is False     # queue drained


def test_failure_retries_then_gives_up(con):
    @worker.handler("boom")
    def _boom(c, job, payload):
        raise RuntimeError("nope")

    jid = db.enqueue(con, "boom", {})
    for _ in range(3):
        # Rewind the backoff: this test is about the attempt lifecycle, not the wait.
        con.execute("UPDATE job SET available_at=? WHERE id=?", (db.now(), jid))
        assert worker.run_one(con) is True
    row = con.execute("SELECT * FROM job WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed" and row["attempts"] == 3
    # a killed run leaves no partial alert, only a diagnostic
    assert con.execute("SELECT COUNT(*) n FROM alert").fetchone()["n"] == 0
    assert con.execute("SELECT code FROM diagnostic").fetchone()["code"] == "RUN_FAILED"
    assert con.execute("SELECT COUNT(*) n FROM diagnostic").fetchone()["n"] == 1


def test_failed_job_retries_with_backoff(con):
    @worker.handler("boom2")
    def _boom2(c, job, payload):
        raise RuntimeError("nope")

    jid = db.enqueue(con, "boom2", {})
    assert worker.run_one(con) is True
    row = con.execute("SELECT status, available_at FROM job WHERE id=?",
                      (jid,)).fetchone()
    assert row["status"] == "queued"
    assert row["available_at"] > db.now()   # first retry waits, not immediate


def test_load_handlers_explicitly_registers_backtest_even_after_cache(con):
    worker._load_handlers()
    expected = {"poll", "baseline", "run", "backtest", "reanalyse", "export"}
    assert expected <= worker.HANDLERS.keys()
    worker.HANDLERS.pop("backtest")
    worker._load_handlers()
    assert worker.HANDLERS["backtest"].__name__ == "backtest"


def test_expired_lease_is_reclaimed(con):
    db.enqueue(con, "noop", {})
    j = db.lease(con, lease_seconds=-1)      # already expired
    assert db.lease(con) is not None and db.lease(con) is None


def test_progress_renews_a_leased_job(con):
    """A job that reports progress is still running: its lease must be pushed out,
    or the next worker reclaims and double-executes it."""
    db.enqueue(con, "noop", {})
    j = db.lease(con)
    con.execute("UPDATE job SET lease_expires_at=? WHERE id=?",
                (db.now(), j["id"]))          # lease about to expire
    db.progress(con, j, 1, 2, "halfway")
    row = con.execute("SELECT lease_expires_at, status FROM job WHERE id=?",
                      (j["id"],)).fetchone()
    assert row["lease_expires_at"] > db.now() and row["status"] == "leased"


def test_progress_never_renews_a_finished_job(con):
    db.enqueue(con, "noop", {})
    j = db.lease(con)
    db.finish(con, j["id"])
    db.progress(con, j, 2, 2)
    row = con.execute("SELECT status FROM job WHERE id=?", (j["id"],)).fetchone()
    assert row["status"] == "done"            # no resurrected lease


def test_diagnostic_includes_stable_severity_and_remedy(con):
    db.diagnostic(con, "MISREGISTRATION", "info", "Shift exceeded tolerance.")
    row = con.execute("SELECT severity, detail_json FROM diagnostic").fetchone()
    assert row["severity"] == "warning"
    assert "automatic co-registration" in json.loads(row["detail_json"])["remedy"]


def test_legacy_terrawatch_files_are_adopted(tmp_path, monkeypatch):
    """A data directory written before the rename must be taken over, not silently
    replaced with a fresh, empty database."""
    (tmp_path / "terrawatch.db").write_bytes(b"")
    (tmp_path / "terrawatch.db-wal").write_bytes(b"")
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "snitch.db"))
    db._adopt_legacy_files()
    assert (tmp_path / "snitch.db").exists()
    assert (tmp_path / "snitch.db-wal").exists()
    assert not (tmp_path / "terrawatch.db").exists()
    # never clobber a database that is already the new name
    (tmp_path / "terrawatch.db").write_bytes(b"")
    db._adopt_legacy_files()
    assert (tmp_path / "terrawatch.db").exists()
