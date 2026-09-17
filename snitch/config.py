"""config.yaml with SNITCH_ environment overrides. Credentials are read from the
environment by name and never stored in, or returned from, this module."""
import os
from functools import lru_cache

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("SNITCH_CONFIG", os.path.join(ROOT, "config.yaml"))


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = yaml.safe_load(open(PATH)) if os.path.exists(PATH) else {}
    # SNITCH_STORAGE__DATA_DIR=... overrides cfg['storage']['data_dir']
    for key, val in os.environ.items():
        if not key.startswith("SNITCH_") or "__" not in key:
            continue
        # An empty override means "not set": compose passes password vars through
        # unconditionally, and an empty string must not clobber the configured one.
        if val is None or str(val).strip() == "":
            continue
        node = cfg
        *path, leaf = key[len("SNITCH_"):].lower().split("__")
        for p in path:
            node = node.setdefault(p, {})
        node[leaf] = yaml.safe_load(val)
    return cfg


def get(dotted: str, default=None):
    node = config()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def credential_status() -> dict:
    """Presence only. Never values — see spec §16."""
    out = {}
    for name in ("cdse", "gee"):
        envs = get(f"adapters.{name}.credentials_env") or []
        if name == "gee":
            envs = ["GEE_SERVICE_ACCOUNT_JSON"] if get("adapters.gee.enabled") else []
        out[name] = {e: bool(os.environ.get(e)) for e in envs}
    return out


def data_dir() -> str:
    d = os.environ.get("SNITCH_DATA_DIR") or get("storage.data_dir", "./data")
    return os.path.abspath(os.path.join(ROOT, d)) if not os.path.isabs(d) else d
