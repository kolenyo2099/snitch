"""Recipe registry: YAML files under recipes/, loaded once at startup."""
import glob, os
import yaml

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "recipes")
REQUIRED = {"id", "version", "display_name", "plain_question", "sensor",
            "detector", "bands", "mask_chain", "defaults", "reference",
            "limitations"}


def load() -> dict:
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "*.yaml"))):
        r = yaml.safe_load(open(path))
        missing = REQUIRED - r.keys()
        if missing:
            raise ValueError(f"{path}: missing keys {sorted(missing)}")
        if not str(r["reference"].get("url", "")).startswith("http"):
            raise ValueError(f"{path}: reference.url must be resolvable")
        provenance = r.get("default_provenance", {})
        missing_provenance = set(r["defaults"]) - set(provenance)
        if missing_provenance:
            raise ValueError(f"{path}: defaults missing provenance for "
                             f"{sorted(missing_provenance)}")
        if any(v not in {"literature", "statistical", "heuristic"}
               for v in provenance.values()):
            raise ValueError(f"{path}: default provenance must be literature, "
                             "statistical, or heuristic")
        out[r["id"]] = r
    return out


REGISTRY = load()


def verify_references(timeout: float = 20.0) -> dict:
    """Build-time check (spec §19): a citation that does not resolve is worse than no
    citation. Run via `python -m terrawatch.recipes`; non-zero exit fails the build."""
    import httpx
    out = {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        for rid, r in REGISTRY.items():
            url = r["reference"]["url"]
            try:
                resp = c.head(url)
                if resp.status_code >= 400:
                    resp = c.get(url)
                # Some journal hosts return 403 to non-browser clients after doi.org
                # has successfully resolved the DOI. That proves the citation target
                # exists; treating the publisher's bot policy as a broken reference
                # makes otherwise reproducible Docker builds randomly fail.
                doi_resolved = (url.startswith("https://doi.org/") and
                                any(300 <= h.status_code < 400 for h in resp.history))
                out[rid] = {"url": url, "status": resp.status_code,
                            "ok": resp.status_code < 400 or doi_resolved}
                if doi_resolved and resp.status_code >= 400:
                    out[rid]["note"] = "DOI resolved; publisher blocks automated clients"
            except Exception as e:  # noqa: BLE001
                out[rid] = {"url": url, "ok": False, "error": str(e)}
    return out


if __name__ == "__main__":
    import json, sys
    res = verify_references()
    print(json.dumps(res, indent=2))
    sys.exit(0 if all(v["ok"] for v in res.values()) else 1)
