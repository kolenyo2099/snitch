"""Job runner. Leases one job at a time from SQLite and dispatches it."""
from __future__ import annotations
import json, signal, time, traceback

from . import db
from .log import log

HANDLERS: dict[str, callable] = {}


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
    # Explicit assignment is intentional. With ``python -m terrawatch.worker`` Python
    # executes this file as ``__main__``, while decorators imported by pipeline can bind
    # to a second ``terrawatch.worker`` module. Relying only on decorator side effects
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
    try:
        if fn is None:
            raise KeyError(f"no handler for job kind {job['kind']!r}")
        fn(con, job, payload)
        db.finish(con, job["id"])
        log.info("job done", extra={"extra": {"job": job["id"], "kind": job["kind"]}})
    except Exception:
        err = traceback.format_exc()
        final_status = db.finish(con, job["id"], error=err)
        log.error("job failed", extra={"extra": {"job": job["id"],
                                                 "kind": job["kind"]}})
        if final_status == "failed":
            db.diagnostic(con, "RUN_FAILED", "error",
                          f"Job {job['kind']} failed: {err.strip().splitlines()[-1]}",
                          project_id=job["project_id"], detail={"traceback": err})
    return True


def main(poll_seconds: float = 2.0):
    _load_handlers()
    con = db.connect()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("worker started", extra={"extra": {"handlers": sorted(HANDLERS)}})
    while not stop:
        try:
            if not run_one(con):
                time.sleep(poll_seconds)
        except Exception:
            log.error("worker loop error", exc_info=True)
            time.sleep(poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
