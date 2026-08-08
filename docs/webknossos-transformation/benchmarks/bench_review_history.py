#!/usr/bin/env python
"""Phase 5 — measure the review-history path against the delete baseline.

Two questions ADR-003 has to answer with numbers rather than assertion:

1. Does making history append-only cost more per resubmit than deleting?
   The old path loops in Python (one DELETE per row, plus a file unlink); the
   new one is a single UPDATE. Compared here at increasing history depth.

2. Does reading "the current submission" stay cheap as history grows?
   If it degrades with depth, append-only history has traded a real regression
   for an audit trail, and `idx_submission_current` is not doing its job.

Runs against a throwaway `test_*` database; never touches mito_dev.

    python bench_review_history.py
    python bench_review_history.py --depths 1,5,20,100
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import statistics
import sys
import time
from pathlib import Path

import django

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["MITO_DB_CONN_MAX_AGE"] = "0"
os.environ.setdefault("DJANGO_DEBUG", "1")  # connection.queries needs DEBUG

django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.db import connection, connections  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.test.utils import CaptureQueriesContext, setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.models import UserProfile  # noqa: E402
from annotation.models import AnnotationSubmission, AnnotationTask  # noqa: E402
from annotation.services import (  # noqa: E402
    _supersede_submissions, current_submission,
)
from core.choices import SubmissionSource, TaskStatus, UserRole  # noqa: E402
from projects.models import Dataset, Project  # noqa: E402
from volumes.models import Volume  # noqa: E402


def build(depth: int):
    """A task carrying `depth` prior submissions, plus one current."""
    AnnotationSubmission.objects.all().delete()
    AnnotationTask.objects.all().delete()
    Volume.objects.all().delete()
    Dataset.objects.all().delete()
    Project.objects.all().delete()
    User.objects.filter(username__startswith="rh-").delete()

    u = User.objects.create_user(f"rh-{depth}")
    UserProfile.objects.update_or_create(
        user=u, defaults={"role": UserRole.ANNOTATOR})
    p = Project.objects.create(title="rh")
    d = Dataset.objects.create(project=p, name="ds")
    v = Volume.objects.create(project=p, dataset=d, name="v", image_path="a.tif")
    task = AnnotationTask.objects.create(
        project=p, volume=v, z_start=0, z_end=1, y_end=64, x_end=64,
        assigned_to=u, status=TaskStatus.SUBMITTED,
    )
    # In-app submissions own no file, so this measures the row work alone —
    # file unlinking is filesystem time and would drown the query comparison.
    AnnotationSubmission.objects.bulk_create([
        AnnotationSubmission(
            task=task, annotator=u, source=SubmissionSource.INAPP, notes=f"r{i}"
        )
        for i in range(depth)
    ])
    keep = AnnotationSubmission.objects.create(
        task=task, annotator=u, source=SubmissionSource.INAPP, notes="current"
    )
    return task, keep


def db_ms(queries):
    return sum(float(q.get("time", 0)) for q in queries) * 1000.0


def measure(fn, repeats=5):
    walls, dbs, counts = [], [], []
    for _ in range(repeats):
        with CaptureQueriesContext(connection) as cap:
            t0 = time.perf_counter()
            fn()
            walls.append((time.perf_counter() - t0) * 1000)
        dbs.append(db_ms(cap.captured_queries))
        counts.append(len(cap.captured_queries))
    return {
        "queries": counts[0],
        "queries_vary": len(set(counts)) > 1,
        "wall_p50_ms": round(statistics.median(walls), 2),
        "wall_max_ms": round(max(walls), 2),
        "db_p50_ms": round(statistics.median(dbs), 2),
    }


def bench_supersede(depth: int, history_on: bool):
    task, keep = build(depth)
    flag = override_settings(FEATURE_REVIEW_HISTORY=history_on)
    flag.enable()
    try:
        with CaptureQueriesContext(connection) as cap:
            t0 = time.perf_counter()
            removed = _supersede_submissions(task, keep=keep)
            wall = (time.perf_counter() - t0) * 1000
        remaining = AnnotationSubmission.objects.filter(task=task).count()
        current = AnnotationSubmission.objects.filter(
            task=task, superseded_at__isnull=True
        ).count()
        return {
            "depth": depth,
            "strategy": "append-only (retire)" if history_on else "delete (baseline)",
            "queries": len(cap.captured_queries),
            "wall_ms": round(wall, 2),
            "db_ms": round(db_ms(cap.captured_queries), 2),
            "affected": removed,
            "rows_remaining": remaining,
            "current_submissions": current,
        }
    finally:
        flag.disable()


def bench_current_read(depth: int):
    """Reading the current submission must not degrade with history depth."""
    task, keep = build(depth)
    flag = override_settings(FEATURE_REVIEW_HISTORY=True)
    flag.enable()
    try:
        _supersede_submissions(task, keep=keep)
        stats = measure(lambda: current_submission(task), repeats=9)
        return {"depth": depth, **stats}
    finally:
        flag.disable()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--depths", default="1,5,20,100")
    args = ap.parse_args()

    if connections["default"].vendor != "postgresql":
        print("refusing to benchmark off PostgreSQL"); return 2

    setup_test_environment()
    old = connections["default"].settings_dict["NAME"]
    connections["default"].creation.create_test_db(verbosity=0, autoclobber=True)
    atexit.register(
        lambda: connections["default"].creation.destroy_test_db(old, verbosity=0))

    depths = [int(d) for d in args.depths.split(",")]
    supersede, reads = [], []
    for d in depths:
        supersede.append(bench_supersede(d, history_on=False))
        supersede.append(bench_supersede(d, history_on=True))
        reads.append(bench_current_read(d))

    out = {"supersede": supersede, "current_submission_read": reads}
    print(json.dumps(out, indent=2))

    # A correctness guard on the benchmark itself: append-only must never lose
    # a row, and must always leave exactly one current submission.
    bad = any(
        r["strategy"].startswith("append-only")
        and (r["rows_remaining"] != r["depth"] + 1 or r["current_submissions"] != 1)
        for r in supersede
    )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
