"""P0: a job can be enqueued, leased, executed, completed — and retried cleanly."""
import json
import pytest

from terrawatch import db, worker


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
        assert worker.run_one(con) is True
    row = con.execute("SELECT * FROM job WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed" and row["attempts"] == 3
    # a killed run leaves no partial alert, only a diagnostic
    assert con.execute("SELECT COUNT(*) n FROM alert").fetchone()["n"] == 0
    assert con.execute("SELECT code FROM diagnostic").fetchone()["code"] == "RUN_FAILED"
    assert con.execute("SELECT COUNT(*) n FROM diagnostic").fetchone()["n"] == 1


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


def test_diagnostic_includes_stable_severity_and_remedy(con):
    db.diagnostic(con, "MISREGISTRATION", "info", "Shift exceeded tolerance.")
    row = con.execute("SELECT severity, detail_json FROM diagnostic").fetchone()
    assert row["severity"] == "warning"
    assert "automatic co-registration" in json.loads(row["detail_json"])["remedy"]
