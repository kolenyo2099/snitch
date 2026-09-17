"""Job runner. Leases one job at a time from SQLite and dispatches it."""
from __future__ import annotations
import json, signal, threading, time, traceback

from . import db
from .log import log

HANDLERS: dict[str, callable] = {}


class StopWorker(Exception):
    """Raised in the main thread by SIGTERM/SIGINT. Python delivers it at the next
    bytecode boundary, so a job in flight is abandoned between scenes or requests
    instead of holding the container until SIGKILL — and it is re-queued, not
    failed, so the next worker picks it up from the queue rather than the retry
    budget paying for a deploy."""


def _renew_lease(job_id: int, stop: threading.Event,
                 interval: float = db.LEASE_SECONDS / 5) -> None:
    """Keep a running job's lease fresh. A lease that expires while its job is still
    executing gets reclaimed by the next worker and run a second time — duplicated
    backtests, alerts and notifications. Handlers that report progress already extend
    the lease through db.progress(); this covers the stretches that do not."""
    while not stop.wait(interval):
        try:
            con = db.connect()
            try:
                con.execute("UPDATE job SET lease_expires_at=? WHERE id=?"
                            " AND status='leased'", (db._lease_expires(), job_id))
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            log.warning("lease renewal failed", extra={"extra": {"job": job_id}})


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


@handler("noop")
def _noop(con, job, payload):
    """Exists so the queue is demonstrable without any imagery (spec §17.1)."""
    return {"echo": payload}


def _load_handlers():
    # Explicit assignment is intentional. With ``python -m snitch.worker`` Python
    # executes this file as ``__main__``, while decorators imported by pipeline can bind
    # to a second ``snitch.worker`` module. Relying only on decorator side effects
    # therefore leaves the running worker with just `noop`.
    from . import export, pipeline
    HANDLERS.update({
        "poll": pipeline.poll,
        "baseline": pipeline.fit_baseline,
        "run": pipeline.run_scene,
        "backtest": pipeline.backtest,
        "reanalyse": pipeline.reanalyse,
        "export": export._export_job,
    })


def run_one(con) -> bool:
    """Lease and execute a single job. Returns False when the queue is empty."""
    job = db.lease(con)
    if job is None:
        return False
    payload = json.loads(job["payload_json"])
    fn = HANDLERS.get(job["kind"])
    if fn is None:
        _load_handlers()  # safe for callers that use run_one() without main()
        fn = HANDLERS.get(job["kind"])
    renew_stop = threading.Event()
    heartbeat = threading.Thread(target=_renew_lease, args=(job["id"], renew_stop),
                                 daemon=True)
    heartbeat.start()
    try:
        if fn is None:
            raise KeyError(f"no handler for job kind {job['kind']!r}")
        fn(con, job, payload)
        db.finish(con, job["id"])
        log.info("job done", extra={"extra": {"job": job["id"], "kind": job["kind"]}})
    except StopWorker:
        try:
            con.rollback()          # never leave a half-open transaction behind
        except Exception:  # noqa: BLE001
            pass
        db.requeue(con, job["id"])
        log.info("job requeued for shutdown",
                 extra={"extra": {"job": job["id"], "kind": job["kind"]}})
        raise
    except Exception:
        err = traceback.format_exc()
        final_status = db.finish(con, job["id"], error=err)
        log.error("job failed", extra={"extra": {"job": job["id"],
                                                 "kind": job["kind"]}})
        if final_status == "failed":
            db.diagnostic(con, "RUN_FAILED", "error",
                          f"Job {job['kind']} failed: {err.strip().splitlines()[-1]}",
                          project_id=job["project_id"], detail={"traceback": err})
    finally:
        renew_stop.set()
    return True


def main(poll_seconds: float = 2.0):
    _load_handlers()
    con = db.connect()

    def _stop(signum, _frame):
        raise StopWorker(f"signal {signum}")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("worker started", extra={"extra": {"handlers": sorted(HANDLERS)}})
    while True:
        try:
            if not run_one(con):
                time.sleep(poll_seconds)
        except StopWorker:
            break
        except Exception:
            try:
                con.rollback()
            except Exception:  # noqa: BLE001
                pass
            log.error("worker loop error", exc_info=True)
            time.sleep(poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
