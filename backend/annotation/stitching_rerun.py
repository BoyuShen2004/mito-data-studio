"""Carry review decisions from an earlier stitching run into a re-run.

When tiles are added (a missing slab is filled) or relabelled, phases 1-3 are
run again in a new work directory. Seam numbers and global IDs then differ
from the earlier run, so its ``decisions.csv`` files no longer match.
``stitch_carry_decisions`` re-keys every earlier human decision that is still
valid onto the new candidates, reports the ones that are not and why, and
lists the new candidates that still need review.

A decision is about two labels touching across one tile face. It is carried
only when everything it was based on is unchanged:

- both tiles are still there (matched by box, so a suffix may change);
- both labels still exist with the same voxel count;
- the pair is still a candidate with the same contact and face areas.

Anything else is invalidated with a reason, never guessed. Nothing is read
from the tiles: the comparison uses ``tiles.csv``, ``seams.csv``,
``id_map.csv`` and ``candidates.csv`` of the two work directories. The design
is in ``docs/engineering/stitching.md`` ("Re-runs").
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from annotation.stitching import (
    DECISION_FIELDS,
    IN_REGION,
    StitchingError,
    _listing,
    _pair_key,
    code_revision,
    csv_fields,
    load_tiles_csv,
    read_csv,
    read_decisions,
    resolve_decisions,
    write_csv,
)

CARRIED_FILE = "decisions_carried.csv"
REPORT_FILE = "carry_report.csv"
NEEDS_REVIEW_FILE = "needs_review.csv"
CARRY_RUN = "carry_run.json"
CARRIED, INVALIDATED, SKIPPED_AUTO = "carried", "invalidated", "skipped_auto"
# Why a decision was not carried, in the order they are checked.
REASONS = ("tile_removed", "label_removed", "label_changed", "no_longer_touching", "contact_changed")
REPORT_FIELDS = ("status", "reason", "detail", "decision", "reviewer", "decided_at",
                 "old_seam", "old_tile_a", "label_a", "old_tile_b", "label_b", "old_global_a", "old_global_b",
                 "new_seam", "new_tile_a", "new_tile_b", "new_global_a", "new_global_b")
# stitch_review --only reads the first five columns. No "decision" column, so
# importing the file into a review page decides nothing.
NEEDS_REVIEW_FIELDS = ("seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                       "contact_voxels", "frac_a", "frac_b", "why")
EVIDENCE_FIELDS = ("contact_voxels", "area_a", "area_b")
AUTO_PREFIX = "auto:"


def _boxes(work: Path, what: str) -> dict[str, tuple]:
    """``{tile name: box}`` from tiles.csv; duplicate boxes cannot be matched."""
    tiles = load_tiles_csv(work)
    seen = Counter(t.box for t in tiles)
    twice = sorted(t.name for t in tiles if seen[t.box] > 1)
    if twice:
        raise StitchingError(_listing(f"{what} tiles.csv lists a box twice", twice))
    return {t.name: t.box for t in tiles}


def _label_counts(work: Path) -> dict[tuple[str, int], int]:
    return {(r["tile"], int(r["local_id"])): int(r["voxel_count"]) for r in read_csv(work / "id_map.csv")}


def _box_key(row: dict, boxes: dict[str, tuple]) -> tuple:
    """A candidate independent of seam numbers, tile suffixes and global IDs."""
    return (boxes[row["tile_a"]], int(row["label_a"]), boxes[row["tile_b"]], int(row["label_b"]))


@dataclass
class CarryResult:
    carried: list[dict]
    report: list[dict]
    needs_review: list[dict]
    counts: dict = field(default_factory=dict)
    run: dict = field(default_factory=dict)


def carry_decisions(old_work, new_work, decision_paths, *, include_auto: bool = False, auto_decisions=None,
                    region_only: bool = False) -> CarryResult:
    old_work, new_work = Path(old_work), Path(new_work)
    if old_work.resolve() == new_work.resolve():
        raise StitchingError("--old-work and --work are the same directory; run phases 1-3 of the re-run in a "
                             "new work directory so the earlier candidates.csv is kept")
    if not decision_paths:
        raise StitchingError("pass at least one earlier decisions.csv with --decisions")
    old_boxes, new_boxes = _boxes(old_work, "the earlier"), _boxes(new_work, "the new")
    new_name = {box: name for name, box in new_boxes.items()}
    old_candidates = read_csv(old_work / "candidates.csv")
    new_candidates = read_csv(new_work / "candidates.csv")
    old_counts, new_counts = _label_counts(old_work), _label_counts(new_work)
    if region_only and IN_REGION not in csv_fields(new_work / "candidates.csv"):
        raise StitchingError("--region-only needs region data; run stitch_ids --regions and stitch_candidates "
                             "in the new work directory")

    # The earlier decisions, validated against the earlier candidates exactly as stitch_objects does.
    rows = read_decisions(decision_paths)
    decided = resolve_decisions(old_candidates, rows)
    signed: dict[tuple, list[dict]] = {}
    for row in rows:
        if (row.get("decision") or "").strip():
            signed.setdefault(_pair_key(row), []).append(row)

    new_by_key = {_box_key(c, new_boxes): c for c in new_candidates}
    old_seams = {(old_boxes[s["tile_a"]], old_boxes[s["tile_b"]]) for s in read_csv(old_work / "seams.csv")}

    carried, report = [], []
    outcome: dict[tuple, tuple[str, str]] = {}      # box key -> (status, reason) of the earlier decision
    for old in old_candidates:
        decision = decided.get(_pair_key(old))
        if decision is None:
            continue
        humans = [r for r in signed[_pair_key(old)] if not (r.get("reviewer") or "").startswith(AUTO_PREFIX)]
        source = (humans or signed[_pair_key(old)])[0]
        key = _box_key(old, old_boxes)
        entry = {
            "decision": decision, "reviewer": source.get("reviewer", ""), "decided_at": source.get("decided_at", ""),
            "old_seam": old["seam"], "old_tile_a": old["tile_a"], "label_a": old["label_a"],
            "old_tile_b": old["tile_b"], "label_b": old["label_b"],
            "old_global_a": old["global_a"], "old_global_b": old["global_b"],
        }
        if not humans and not include_auto:
            report.append({**entry, "status": SKIPPED_AUTO, "reason": "auto_decision",
                           "detail": "made by the auto rule; rerun stitch_auto_decisions on the new candidates"})
            outcome[key] = (SKIPPED_AUTO, "auto_decision")
            continue

        reasons, details = [], []
        gone = [side for side, box in (("a", key[0]), ("b", key[2])) if box not in new_name]
        if gone:
            reasons.append("tile_removed")
            details += [f"tile_{side} {old['tile_' + side]} is not in the new run" for side in gone]
        else:
            entry.update(new_tile_a=new_name[key[0]], new_tile_b=new_name[key[2]])
            for side in ("a", "b"):
                label = int(old["label_" + side])
                before = old_counts.get((old["tile_" + side], label))
                after = new_counts.get((entry["new_tile_" + side], label))
                if after is None:
                    reasons.append("label_removed")
                    details.append(f"label_{side} {label} is not in {entry['new_tile_' + side]} any more")
                elif after != before:
                    reasons.append("label_changed")
                    details.append(f"label_{side} {label} has {after} voxels, had {before}")
            new = new_by_key.get(key)
            if new is None:
                if "label_removed" not in reasons:
                    reasons.append("no_longer_touching")
                    details.append("the two labels no longer touch across this face")
            else:
                entry.update(new_seam=new["seam"], new_global_a=new["global_a"], new_global_b=new["global_b"])
                changed = [f"{f} {new[f]}, was {old[f]}" for f in EVIDENCE_FIELDS if int(new[f]) != int(old[f])]
                if changed:
                    reasons.append("contact_changed")
                    details.append("; ".join(changed))
        reasons = sorted(set(reasons), key=REASONS.index)
        if reasons:
            report.append({**entry, "status": INVALIDATED, "reason": ";".join(reasons), "detail": "; ".join(details)})
            outcome[key] = (INVALIDATED, ";".join(reasons))
            continue
        report.append({**entry, "status": CARRIED, "reason": "", "detail": ""})
        outcome[key] = (CARRIED, "")
        carried.append({**{f: new[f] for f in DECISION_FIELDS[:7]}, "decision": decision,
                        "reviewer": entry["reviewer"], "decided_at": entry["decided_at"]})
    order = {_pair_key(c): i for i, c in enumerate(new_candidates)}
    carried.sort(key=lambda r: order[_pair_key(r)])

    # What the new auto rule already decided (optional): those pairs need no review.
    if auto_decisions is None and (new_work / "decisions_auto.csv").exists():
        auto_decisions = new_work / "decisions_auto.csv"
    auto = resolve_decisions(new_candidates, read_decisions([auto_decisions])) if auto_decisions else {}
    carried_keys = {_pair_key(r): r["decision"] for r in carried}
    auto_conflicts = {key for key, decision in carried_keys.items() if auto.get(key, decision) != decision}
    for row in report:
        if row["status"] != CARRIED:
            continue
        key = (int(row["new_seam"]), row["new_tile_a"], int(row["label_a"]), row["new_tile_b"], int(row["label_b"]))
        if key in auto_conflicts:
            row["detail"] = (f"the new auto rule says {auto[key]}; the reviewer's decision is carried and "
                             "must win (import decisions_auto.csv first, then this file)")

    old_keys = {_box_key(c, old_boxes) for c in old_candidates}
    needs_review = []
    for new in new_candidates:
        pair = _pair_key(new)
        if pair in carried_keys or pair in auto:
            continue
        if region_only and new[IN_REGION] != "true":
            continue
        key = _box_key(new, new_boxes)
        status, reason = outcome.get(key, ("", ""))
        if status == INVALIDATED:
            why = f"changed:{reason}"
        elif (key[0], key[2]) not in old_seams:
            why = "new_seam"
        elif key not in old_keys:
            why = "new_candidate"
        else:
            why = "not_decided_before"      # undecided earlier, or auto-accepted then but not now
        needs_review.append({**{f: new[f] for f in NEEDS_REVIEW_FIELDS[:-1]}, "why": why})

    by_status = Counter(r["status"] for r in report)
    reasons = Counter(reason for r in report if r["status"] == INVALIDATED for reason in r["reason"].split(";"))
    counts = {
        "earlier_decisions": len(report), "carried": by_status[CARRIED], "invalidated": by_status[INVALIDATED],
        "skipped_auto": by_status[SKIPPED_AUTO], "invalidated_by_reason": {r: reasons[r] for r in REASONS if reasons[r]},
        "new_candidates": len(new_candidates), "auto_decided": len(auto), "auto_conflicts": len(auto_conflicts),
        "needs_review": len(needs_review),
        "needs_review_by_why": dict(Counter(r["why"].split(":")[0] for r in needs_review)),
        "tiles_added": len(set(new_name) - set(old_boxes.values())),
        "tiles_removed": len(set(old_boxes.values()) - set(new_name)),
    }
    run = {
        "old_work": str(old_work.resolve()), "decisions": [str(Path(p).resolve()) for p in decision_paths],
        "include_auto": include_auto, "region_only": region_only,
        "auto_decisions": str(Path(auto_decisions).resolve()) if auto_decisions else None,
        "counts": counts, "code_revision": code_revision(),
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return CarryResult(carried, report, needs_review, counts, run)


def run_carry(old_work, new_work, decision_paths, *, out=None, include_auto: bool = False, auto_decisions=None,
              region_only: bool = False, command: str = "") -> CarryResult:
    result = carry_decisions(old_work, new_work, decision_paths, include_auto=include_auto,
                             auto_decisions=auto_decisions, region_only=region_only)
    new_work = Path(new_work)
    write_csv(Path(out) if out else new_work / CARRIED_FILE, DECISION_FIELDS, result.carried)
    write_csv(new_work / REPORT_FILE, REPORT_FIELDS, result.report)
    write_csv(new_work / NEEDS_REVIEW_FILE, NEEDS_REVIEW_FIELDS, result.needs_review)
    result.run["command"] = command
    result.run["carried_file"] = str((Path(out) if out else new_work / CARRIED_FILE).resolve())
    (new_work / CARRY_RUN).write_text(json.dumps(result.run, indent=2) + "\n")
    return result
