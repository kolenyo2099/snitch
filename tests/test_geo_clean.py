"""The duplicate-vertex case that made Earth Search answer 400 on hand-drawn shapes."""
import pytest

from terrawatch import db, geo


def test_duplicate_vertex_is_dropped():
    dup = {"type": "Polygon", "coordinates": [[[-70, -33.5], [-70, -33.5],
                                               [-69.9, -33.5], [-69.9, -33.4],
                                               [-70, -33.5]]]}
    ring = geo.clean(dup)["coordinates"][0]
    assert all(a != b for a, b in zip(ring, ring[1:]))
    assert geo.area_km2(geo.clean(dup)) == pytest.approx(geo.area_km2(dup), rel=1e-6)


def test_self_intersection_becomes_valid():
    from shapely.geometry import shape
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1],
                                                  [0, 0]]]}
    assert not shape(bowtie).is_valid
    assert shape(geo.clean(bowtie)).is_valid


def test_degenerate_shape_is_refused():
    with pytest.raises(ValueError):
        geo.clean({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]})


def test_progress_is_reported_and_tolerates_no_job(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    job_id = db.enqueue(con, "noop", {})
    job = con.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    db.progress(con, job, 3, 10, "Scoring")
    row = con.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert (row["progress_done"], row["progress_total"], row["progress_note"]) \
        == (3, 10, "Scoring")
    db.progress(con, job, 4)                     # total/note are kept when omitted
    row = con.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert (row["progress_done"], row["progress_total"]) == (4, 10)
    db.progress(con, None, 1, 2, "no job")       # must not raise
