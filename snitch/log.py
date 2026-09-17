"""Structured JSON logs to data/logs/, one file per process, rotated.

Per-process files are deliberate: the api and worker containers share the same
volume, and two processes rotating one file lose whatever the other still has
open when the rename happens. Override the name with SNITCH_LOG_NAME."""
import json, logging, logging.handlers, os, sys

from .config import data_dir

_SECRET = ("password", "token", "secret", "credential", "client_id", "key")


def _process_name() -> str:
    env = os.environ.get("SNITCH_LOG_NAME")
    if env:
        return env
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.endswith("worker.py"):
        return "worker"      # `python -m snitch.worker` and `python snitch/worker.py`
    if "uvicorn" in os.path.basename(argv0):
        return "api"
    return "app"


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        rec = {"ts": self.formatTime(r), "level": r.levelname, "logger": r.name,
               "msg": r.getMessage()}
        rec.update({k: v for k, v in getattr(r, "extra", {}).items()
                    if not any(s in k.lower() for s in _SECRET)})
        if r.exc_info:
            rec["exc"] = self.formatException(r.exc_info)
        return json.dumps(rec)


def setup(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger("snitch")
    if root.handlers:
        return root
    root.setLevel(level)
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(d, f"snitch-{_process_name()}.jsonl"),
        maxBytes=20_000_000, backupCount=5)
    fh.setFormatter(JsonFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    root.addHandler(sh)
    return root


log = setup()
