#!/usr/bin/env python
"""Phase 7 — measure the operation log, and test ADR-005's central claim.

ADR-005 §5 chooses **materialized current state with the log as history** over
event sourcing, on the grounds that replaying an unbounded history on every read
would be indefensible. That is an assertion until measured, so this measures it:
the same "what is the current state" question answered three ways.

    replay-all       walk every operation from seq 1
    checkpointed     walk only operations after the last checkpoint
    materialized     read the cursor (what Phase 7 actually does)

Also measures append cost, history reads, heartbeat ingestion, aggregation, and
storage growth per operation.

Runs against a throwaway `test_*` database; never touches mito_dev.

    python bench_operations.py
    python bench_operations.py --depths 10,100,1000,10000
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
os.environ["FEATURE_ANNOTATION_OPS"] = "1"
os.environ["MITO_DB_CONN_MAX_AGE"] = "0"
os.environ.setdefault("DJANGO_DEBUG", "1")

django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.db import connection, connections, reset_queries  # noqa: E402
from django.test.utils import CaptureQueriesContext, setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.models import UserProfile  # noqa: E402
from annotation.models import (  # noqa: E402
    AnnotationOperation, AnnotationTask, WorkSession,
)
from annotation.operations import (  # noqa: E402
    append_operation, current_version, history,
)
from annotation.sessions import active_seconds_for, heartbeat, start_session  # noqa: E402
from core.choices import TaskStatus, UserRole  # noqa: E402
from projects.models import Dataset, Project  # noqa: E402
from volumes.models import Volume  # noqa: E402

K = AnnotationOperation.Kind
# One checkpoint every N operations — the "snapshot boundary" doc 22 describes.
CHECKPOINT_EVERY = 100


def seed(depth: int):
    AnnotationOperation.objects.all().delete()
    WorkSession.objects.all().delete()
    AnnotationTask.objects.all().delete()
    Volume.objects.all().delete()
    Dataset.objects.all().delete()
    Project.objects.all().delete()
    User.objects.filter(username__startswith="op-b").delete()

    u = User.objects.create_user("op-b0")
    UserProfile.objects.update_or_create(
        user=u, defaults={"role": UserRole.ANNOTATOR})
    p = Project.objects.create(title="ops")
    d = Dataset.objects.create(project=p, name="ds")
    v = Volume.objects.create(project=p, dataset=d, name="v", image_path="a.tif")
    task = AnnotationTask.objects.create(
        project=p, volume=v, z_start=0, z_end=1, y_end=64, x_end=64,
        assigned_to=u, status=TaskStatus.ASSIGNED,
    )
    # bulk_create bypasses the service, which is fine here: the service's job is
    # sequence allocation and we are allocating densely ourselves.
    AnnotationOperation.objects.bulk_create([
        AnnotationOperation(
            task=task, actor=u, seq=i + 1, kind=K.PAINT_SLICE,
            payload={"axis": "z", "index": i % 64, "runs": i},
            payload_digest="0" * 64,
        )
        for i in range(depth)
    ], batch_size=2000)
    return task, u


def db_ms(queries):
    return sum(float(q.get("time", 0)) for q in queries) * 1000.0


def measure(fn, repeats=7):
    walls, dbs, counts = [], [], []
    for _ in range(repeats):
        reset_queries()
        with CaptureQueriesContext(connection) as cap:
            t0 = time.perf_counter()
            fn()
            walls.append((time.perf_counter() - t0) * 1000)
        dbs.append(db_ms(cap.captured_queries))
        counts.append(len(cap.captured_queries))
    walls.sort()

    def pct(p):
        return round(walls[min(int(len(walls) * p), len(walls) - 1)], 2)

    return {
        "queries": counts[0],
        "p50_ms": round(statistics.median(walls), 3),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "db_p50_ms": round(statistics.median(dbs), 2),
    }


# --- the three ways to answer "what is the current state" -------------------


def replay_all(task):
    """Event sourcing: walk every operation. The option ADR-005 rejected."""
    state = {}
    for op in AnnotationOperation.objects.filter(task=task).order_by("seq").iterator():
        state[op.payload.get("index")] = op.payload.get("runs")
    return state


def replay_checkpointed(task, checkpoint_seq):
    """Only what follows the last checkpoint."""
    state = {}
    qs = (
        AnnotationOperation.objects.filter(task=task, seq__gt=checkpoint_seq)
        .order_by("seq").iterator()
    )
    for op in qs:
        state[op.payload.get("index")] = op.payload.get("runs")
    return state


def materialized(task):
    """What Phase 7 actually does: read the cursor. One indexed lookup."""
    return current_version(task)


def storage_bytes(task) -> dict:
    """Row and index growth for this task's operations."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_total_relation_size('annotation_annotationoperation'),
                   pg_relation_size('annotation_annotationoperation'),
                   count(*)
              FROM annotation_annotationoperation
        """)
        total, heap, n = cur.fetchone()
    return {
        "rows": int(n),
        "total_bytes": int(total),
        "heap_bytes": int(heap),
        "index_bytes": int(total) - int(heap),
        "bytes_per_op": round(int(total) / max(int(n), 1), 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--depths", default="10,100,1000,10000")
    args = ap.parse_args()

    if connections["default"].vendor != "postgresql":
        print("refusing to benchmark off PostgreSQL"); return 2

    setup_test_environment()
    old = connections["default"].settings_dict["NAME"]
    connections["default"].creation.create_test_db(verbosity=0, autoclobber=True)
    atexit.register(
        lambda: connections["default"].creation.destroy_test_db(old, verbosity=0))

    out = {"append": [], "read_state": [], "history": [], "storage": [],
           "sessions": []}

    for depth in [int(d) for d in args.depths.split(",")]:
        task, user = seed(depth)

        out["append"].append({
            "existing_ops": depth,
            **measure(lambda: append_operation(
                task=task, actor=user, kind=K.PAINT_SLICE, payload={"x": 1}),
                repeats=5),
        })

        checkpoint = max(depth - (depth % CHECKPOINT_EVERY or CHECKPOINT_EVERY), 0)
        out["read_state"].append({
            "ops": depth,
            "replay_all": measure(lambda: replay_all(task), repeats=3),
            "checkpointed": measure(
                lambda: replay_checkpointed(task, checkpoint), repeats=3),
            "materialized": measure(lambda: materialized(task), repeats=7),
            "checkpoint_at": checkpoint,
        })

        out["history"].append({
            "ops": depth,
            **measure(lambda: history(task, limit=100)),
        })
        out["storage"].append({"ops": depth, **storage_bytes(task)})

    # Heartbeat ingestion and aggregation, independent of op depth.
    task, user = seed(0)
    session = start_session(task=task, actor=user)
    out["sessions"].append({
        "op": "heartbeat",
        **measure(lambda: heartbeat(
            WorkSession.objects.get(pk=session.pk), actor=user), repeats=7),
    })
    for i in range(200):
        s = start_session(task=task, actor=user)
        WorkSession.objects.filter(pk=s.pk).update(active_seconds=60)
    out["sessions"].append({
        "op": "aggregate_200_sessions",
        **measure(lambda: active_seconds_for(task=task), repeats=5),
    })

    print(json.dumps(out, indent=2))

    # Guards: append and materialized read must be constant in history depth.
    append_q = {r["queries"] for r in out["append"]}
    mat_q = {r["materialized"]["queries"] for r in out["read_state"]}
    bad = len(append_q) != 1 or len(mat_q) != 1
    if bad:
        print(f"FAIL: cost varied with depth (append={sorted(append_q)}, "
              f"materialized={sorted(mat_q)})", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
