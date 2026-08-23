"""Structured JSON logs to data/logs/terrawatch.jsonl, rotated."""
import json, logging, logging.handlers, os

from .config import data_dir

_SECRET = ("password", "token", "secret", "credential", "client_id", "key")


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
    root = logging.getLogger("terrawatch")
    if root.handlers:
        return root
    root.setLevel(level)
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(d, "terrawatch.jsonl"), maxBytes=20_000_000, backupCount=5)
    fh.setFormatter(JsonFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    root.addHandler(sh)
    return root


log = setup()
