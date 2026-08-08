"""Phase 0 baseline: does today's assignment path race?

Runs the *current* read-modify-write claim pattern against a throwaway SQLite
DB (never `backend/db.sqlite3`) with W concurrent workers, and reports double
claims + lock errors. This is the evidence for the Phase 3 Postgres decision.

Usage:
    python bench_claim_race.py [--workers 20] [--tasks 1] [--trials 5]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
from collections import Counter

_SKIP_LOCKED = "--skip-locked" in __import__("sys").argv
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Point Django at a scratch DB *before* setup, so the real one is never opened.
_TMPDIR = tempfile.mkdtemp(prefix="mito-race-")
os.environ["MITO_DATA_ROOT"] = _TMPDIR

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

if "--postgres" not in sys.argv:
    settings.DATABASES["default"]["NAME"] = str(Path(_TMPDIR) / "race.sqlite3")

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import OperationalError, connections, transaction  # noqa: E402

from accounts.models import Institution  # noqa: E402
from annotation.models import AnnotationTask  # noqa: E402
from core.choices import TaskStatus, TaskType  # noqa: E402
from projects.models import Dataset, Project  # noqa: E402
from volumes.models import Volume  # noqa: E402


def seed(n_tasks: int, n_workers: int):
    inst = Institution.objects.create(name="Bench Inst")
    User = get_user_model()
    owner = User.objects.create_user("bench-owner", password="x")
    annotators = [
        User.objects.create_user(f"bench-annot-{i}", password="x")
        for i in range(n_workers)
    ]
    proj = Project.objects.create(title="Bench", institution=inst, created_by=owner)
    ds = Dataset.objects.create(project=proj, name="ds")
    vol = Volume.objects.create(
        project=proj, dataset=ds, name="v", image_path="a.tif"
    )
    for i in range(n_tasks):
        AnnotationTask.objects.create(
            project=proj, volume=vol, z_start=i, z_end=i + 1,
            y_end=64, x_end=64,
            task_type=TaskType.MANUAL_ANNOTATION, status=TaskStatus.UNASSIGNED,
        )
    return annotators


def claim_like_production(user_id: int, results: list, lock: threading.Lock):
    """The read-modify-write every current push/auto-assign path performs.

    Mirrors `services.assign_tasks_rule_based`: filter unassigned,
    `select_for_update()`, then write. On SQLite `select_for_update()` is a
    documented no-op, so this is the pattern under test.
    """
    outcome = {"user": user_id, "task": None, "error": None}
    try:
        with transaction.atomic():
            task = (
                AnnotationTask.objects.filter(status=TaskStatus.UNASSIGNED)
                .select_for_update(skip_locked=_SKIP_LOCKED)
                .order_by("id")
                .first()
            )
            if task is None:
                outcome["error"] = "no-work"
            else:
                # Widen the read->write window the way real work does
                # (permission checks, serializer, capacity lookups).
                threading.Event().wait(0.01)
                task.assigned_to_id = user_id
                task.status = TaskStatus.ASSIGNED
                task.save(update_fields=["assigned_to", "status"])
                outcome["task"] = task.id
    except OperationalError as exc:
        outcome["error"] = f"OperationalError: {exc}"
    except Exception as exc:  # noqa: BLE001
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        connections.close_all()
    with lock:
        results.append(outcome)


def run_trial(n_workers: int, n_tasks: int) -> dict:
    AnnotationTask.objects.update(status=TaskStatus.UNASSIGNED, assigned_to=None)
    annotators = list(get_user_model().objects.filter(username__startswith="bench-annot-"))
    results: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_workers)

    def worker(uid):
        barrier.wait()  # maximize overlap
        claim_like_production(uid, results, lock)

    threads = [
        threading.Thread(target=worker, args=(annotators[i % len(annotators)].id,))
        for i in range(n_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r["task"] for r in results if r["task"] is not None]
    counts = Counter(claimed)
    double = {t: c for t, c in counts.items() if c > 1}
    lock_errors = [r["error"] for r in results if r["error"] and "Operational" in r["error"]]
    return {
        "workers": n_workers,
        "tasks_available": n_tasks,
        "successful_claims": len(claimed),
        "distinct_tasks_claimed": len(counts),
        "double_claimed_tasks": double,
        "over_claims": len(claimed) - len(counts),
        "lock_errors": len(lock_errors),
        "no_work": sum(1 for r in results if r["error"] == "no-work"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--tasks", type=int, default=1)
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--wal", action="store_true", help="enable WAL + busy timeout")
    ap.add_argument("--postgres", action="store_true", help="use the configured PostgreSQL instead of scratch SQLite")
    ap.add_argument("--skip-locked", action="store_true", help="claim with select_for_update(skip_locked=True)")
    args = ap.parse_args()

    if args.wal:
        settings.DATABASES["default"].setdefault("OPTIONS", {})
        settings.DATABASES["default"]["OPTIONS"].update(
            {"timeout": 30, "init_command": "PRAGMA journal_mode=WAL;"}
        )
    if args.postgres:
        # Use Django's test-database machinery so nothing touches mito_dev.
        from django.test.utils import setup_test_environment

        setup_test_environment()
        _old_name = connections["default"].settings_dict["NAME"]
        connections["default"].creation.create_test_db(verbosity=0, autoclobber=True)
        # Registered immediately so an early exit still tears the scratch
        # database down — a leftover `test_*` makes the next `manage.py test`
        # prompt for confirmation and die on EOF in CI.
        import atexit

        atexit.register(
            lambda: connections["default"].creation.destroy_test_db(
                _old_name, verbosity=0
            )
        )
    else:
        call_command("migrate", run_syncdb=True, verbosity=0)
    seed(args.tasks, args.workers)

    trials = [run_trial(args.workers, args.tasks) for _ in range(args.trials)]
    summary = {
        "backend": connections["default"].vendor + (" skip_locked" if _SKIP_LOCKED else "") + ("" if args.postgres else (" (WAL)" if args.wal else " (default journal)")),
        "select_for_update_supported": connections["default"].features.has_select_for_update,
        "scenario": f"{args.workers} concurrent workers claiming {args.tasks} task(s)",
        "trials": trials,
        "trials_with_double_claim": sum(1 for t in trials if t["over_claims"] > 0),
        "total_over_claims": sum(t["over_claims"] for t in trials),
        "total_lock_errors": sum(t["lock_errors"] for t in trials),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
