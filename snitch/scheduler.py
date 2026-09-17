"""APScheduler in the api process. It only ever enqueues jobs."""
import random

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, db
from .log import log

_sched: BackgroundScheduler | None = None


def _enqueue_poll(project_id: int):
    con = db.connect()
    jitter = random.randint(0, config.get("scheduler.poll_jitter_minutes", 30)) * 60
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) + timedelta(seconds=jitter)).isoformat(timespec="seconds")
    db.enqueue(con, "poll", {"project_id": project_id}, project_id, available_at=at)
    con.close()


def start() -> BackgroundScheduler:
    global _sched
    if _sched:
        return _sched
    # APScheduler drives its store through SQLAlchemy: a second connection stack with
    # its own pragmas and no busy timeout. Pointing it at the application database
    # meant two independent writers on one file (three, once the worker container is
    # scaled) — the way this database got truncated. It gets its own file.
    _sched = BackgroundScheduler(jobstores={
        "default": SQLAlchemyJobStore(
            url=f"sqlite:///{db.SCHEDULER_DB_PATH}",
            engine_options={"connect_args": {"timeout": 30}})})
    _sched.start()
    con = db.connect()
    for row in con.execute("SELECT id, uuid, schedule_cron FROM project"
                           " WHERE status='active'"):
        schedule(row["id"], row["uuid"], row["schedule_cron"])
    con.close()
    log.info("scheduler started")
    return _sched


def shutdown():
    """Stop the scheduler thread and release its jobstore connection so the process
    can exit (and free the port) instead of lingering after uvicorn stops serving."""
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None
        log.info("scheduler stopped")


def schedule(project_id: int, project_uuid: str, cron: str):
    start().add_job(_enqueue_poll, CronTrigger.from_crontab(cron),
                    args=[project_id], id=f"poll:{project_uuid}",
                    replace_existing=True, misfire_grace_time=3600)


def unschedule(project_uuid: str):
    try:
        start().remove_job(f"poll:{project_uuid}")
    except Exception:
        pass


def next_poll(project_uuid: str) -> str | None:
    job = start().get_job(f"poll:{project_uuid}")
    return job.next_run_time.isoformat() if job and job.next_run_time else None
