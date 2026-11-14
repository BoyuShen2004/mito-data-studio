"""Stitching phase 7b: the deliverable for the scientist.

Builds a delivery folder from a validated work directory (phases 1-6 with
region data):

- ``mitochondria.csv``: one row per object that reaches into the region and
  was not excluded by reviewers, with volume, length, region share, flags,
  bounding box, centroid and the ``core`` flag;
- ``summary.csv``: counts and volume/length statistics for all and core objects;
- ``excluded.csv``: objects left out because reviewers judged one of their
  labels not to be a mitochondrion;
- ``README.md``: what every column means, how the numbers were made, the
  known limits and provenance, in plain language.

No tile is read: everything comes from the phase 2-6 outputs. The design is in
``docs/engineering/stitching.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from annotation.measurements import CROP_PAD, DUST_SIZE, SKIPPED_CROP_LIMIT
from annotation.stitching import (
    IN_REGION,
    REGION_VOXELS,
    StitchingError,
    _listing,
    _pair_key,
    code_revision,
    csv_fields,
    load_tiles_csv,
    read_csv,
    read_decisions,
    write_csv,
)
from annotation.stitching_measure import BBOX_FIELDS, CENTER_FIELDS, load_measure_run
from annotation.stitching_validate import check_consistency

NOT_CORE_REASONS = ("cut_off_at_data_edge", "crosses_region_boundary", "touches_unproofread_tile",
                    "unresolved_contacts")
MITO_FIELDS = ("mito_id", "volume_um3", "length_um", "volume_in_region_um3", "frac_in_region", "n_tiles", "tiles",
               "cut_off_at_data_edge", "crosses_region_boundary", "touches_unproofread_tile", "length_status",
               *BBOX_FIELDS, *CENTER_FIELDS, "core", "not_core_reasons")
SUMMARY_FIELDS = ("metric", "all", "core")
SUMMARY_METRICS = ("objects", "multi_tile", "length_measured", "length_skipped_too_large",
                   "cut_off_at_data_edge", "crosses_region_boundary", "touches_unproofread_tile",
                   "unresolved_contacts", "volume_um3_median", "volume_um3_p90", "volume_um3_max",
                   "length_um_median", "length_um_p90", "length_um_max")
EXCLUDED_FIELDS = ("mito_id", "excluded_global_ids", "n_tiles", "tiles", "volume_um3", "reaches_region")
DELIVERY_FILES = ("mitochondria.csv", "summary.csv", "excluded.csv", "README.md")
MEASURED, SKIPPED_TOO_LARGE = "measured", "skipped_too_large"


def _bool(flag: bool) -> str:
    return "true" if flag else "false"


# --- inputs ------------------------------------------------------------------

def read_exclusions(path, objects: dict[int, int], *, what: str = "global id",
                    source: str = "objects.csv") -> dict[int, list[int]]:
    """``{object_id: [global ids]}`` for the global IDs listed in ``path`` (one
    per line; blank lines, ``#`` comments and a ``global_id`` header are
    ignored). Refuses non-integers and IDs that are not in objects.csv.

    ``measure_volume`` passes label IDs (``what="label id"``, header
    ``label_id``) with each label as its own object."""
    header = what.replace(" ", "_")
    bad, unknown, excluded = [], [], {}
    for lineno, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip().rstrip(",")
        if not line or (lineno == 1 and line.lower() == header):
            continue
        if not line.isdigit():
            bad.append(f"{path}:{lineno}: {raw.strip()!r} is not a {what}")
            continue
        g = int(line)
        if g not in objects:
            unknown.append(f"{path}:{lineno}: {what} {g} is not in {source}")
            continue
        ids = excluded.setdefault(objects[g], [])
        if g not in ids:
            ids.append(g)
    problems = []
    if bad:
        problems.append(_listing(f"lines that are not {what}s", bad))
    if unknown:
        problems.append(_listing(f"excluded {what}s that match no label", unknown))
    if problems:
        raise StitchingError("\n".join(problems))
    return {o: sorted(ids) for o, ids in excluded.items()}


@dataclass
class DecisionStats:
    counts: dict[str, int]              # yes/no/unsure/undecided over all candidates
    in_region: dict[str, int] | None    # the same over in_region candidates, when known
    auto_rules: list[str]               # reviewer names in decisions_auto.csv
    auto_accepted: int                  # auto-accepted pairs used ("yes" in decisions_resolved.csv)
    spot_checks: list[dict]             # {file, checked, agreed, reviewers}

    @property
    def decided(self) -> int:
        return self.counts["yes"] + self.counts["no"] + self.counts["unsure"]


def decision_stats(work: Path, resolved: list[dict], auto_path=None, spot_check_paths=()) -> DecisionStats:
    counts = dict.fromkeys(("yes", "no", "unsure", "undecided"), 0)
    for r in resolved:
        counts[r["decision"]] += 1
    in_region = None
    if IN_REGION in csv_fields(work / "candidates.csv"):
        flag = {_pair_key(c): c[IN_REGION] == "true" for c in read_csv(work / "candidates.csv")}
        in_region = dict.fromkeys(counts, 0)
        for r in resolved:
            if flag.get(_pair_key(r)):
                in_region[r["decision"]] += 1
    decision_of = {_pair_key(r): r["decision"] for r in resolved}

    if auto_path is None and (work / "decisions_auto.csv").exists():
        auto_path = work / "decisions_auto.csv"
    auto_keys, rules = set(), []
    if auto_path is not None:
        unused = []
        for row in read_decisions([auto_path]):
            key = _pair_key(row)
            if decision_of.get(key) != "yes":
                unused.append(f"{row['_source']}: seam {key[0]} {key[1]}:{key[2]} -> {key[3]}:{key[4]} is "
                              f"{decision_of.get(key, 'not a candidate')!r} in decisions_resolved.csv")
            auto_keys.add(key)
            if row["reviewer"] not in rules:
                rules.append(row["reviewer"])
        if unused:
            raise StitchingError(_listing(f"{auto_path} was not used by stitch_objects; pass the auto decisions "
                                          "stitch_objects used with --auto-decisions", unused))

    spot_checks = []
    for path in spot_check_paths:
        rows = [r for r in read_decisions([path]) if (r.get("decision") or "").strip()]
        stray = [f"{r['_source']}: seam {r['seam']} {r['tile_a']}:{r['label_a']} -> {r['tile_b']}:{r['label_b']}"
                 for r in rows if _pair_key(r) not in auto_keys]
        if stray:
            raise StitchingError(_listing(f"{path}: spot-check rows that are not auto-accepted pairs", stray))
        spot_checks.append({"file": Path(path).name, "checked": len(rows),
                            "agreed": sum(r["decision"].strip().lower() == "yes" for r in rows),
                            "reviewers": sorted({r.get("reviewer", "") for r in rows} - {""})})
    return DecisionStats(counts, in_region, rules, len(auto_keys), spot_checks)


def carry_stats(work: Path, resolved: list[dict]) -> dict | None:
    """What ``stitch_carry_decisions`` carried into this work directory, or
    ``None`` when it was not used: the earlier work directory, how many
    earlier decisions were invalidated, and how many carried decisions are
    still the decision ``stitch_objects`` used (a reviewer may have changed
    one on the review page afterwards)."""
    path = work / "carry_run.json"
    if not path.exists():
        return None
    run = json.loads(path.read_text())
    carried_file = Path(run.get("carried_file") or work / "decisions_carried.csv")
    decision_of = {_pair_key(r): r["decision"] for r in resolved}
    carried = read_decisions([carried_file]) if carried_file.exists() else []
    kept = sum(decision_of.get(_pair_key(r)) == r["decision"].strip().lower() for r in carried)
    return {"old_work": run.get("old_work", ""), "carried": len(carried), "kept": kept,
            "invalidated": int(run.get("counts", {}).get("invalidated", 0)), "finished_at": run.get("finished_at", "")}


# --- tables ------------------------------------------------------------------

def mito_row(r: dict, voxel_nm3: float) -> tuple[dict, dict[str, bool]]:
    flags = {
        "cut_off_at_data_edge": r["touches_edge"] == "true",
        "crosses_region_boundary": r["crosses_region_boundary"] == "true",
        "touches_unproofread_tile": int(r["unproofread_tiles"]) > 0,
        "unresolved_contacts": int(r["unresolved_contacts"]) > 0,
    }
    if r["skeleton_length_um"]:
        status = MEASURED
    elif r["skipped_reason"] == SKIPPED_CROP_LIMIT:
        status = SKIPPED_TOO_LARGE
    else:
        raise StitchingError(f"measurements.csv: object {r['object_id']} has no length and skipped_reason "
                             f"{r['skipped_reason']!r}")
    reasons = [name for name in NOT_CORE_REASONS if flags[name]]
    row = {
        "mito_id": r["object_id"], "volume_um3": r["volume_um3"], "length_um": r["skeleton_length_um"],
        "volume_in_region_um3": repr(int(r[REGION_VOXELS]) * voxel_nm3 / 1e9),
        "frac_in_region": r["frac_in_region"], "n_tiles": len(r["tiles"].split(";")), "tiles": r["tiles"],
        "cut_off_at_data_edge": _bool(flags["cut_off_at_data_edge"]),
        "crosses_region_boundary": _bool(flags["crosses_region_boundary"]),
        "touches_unproofread_tile": _bool(flags["touches_unproofread_tile"]),
        "length_status": status,
        **{f: r[f] for f in (*BBOX_FIELDS, *CENTER_FIELDS)},
        "core": _bool(not reasons), "not_core_reasons": ";".join(reasons),
    }
    return row, flags


def _stats(values: list[float]) -> tuple[str, str, str]:
    if not values:
        return "", "", ""
    return (repr(float(np.median(values))), repr(float(np.percentile(values, 90))), repr(float(max(values))))


def summarise(rows: list[dict], flags: list[dict]) -> dict[str, object]:
    volumes = [float(r["volume_um3"]) for r in rows]
    lengths = [float(r["length_um"]) for r in rows if r["length_status"] == MEASURED]
    out: dict[str, object] = {
        "objects": len(rows), "multi_tile": sum(r["n_tiles"] > 1 for r in rows),
        "length_measured": len(lengths),
        "length_skipped_too_large": sum(r["length_status"] == SKIPPED_TOO_LARGE for r in rows),
        **{name: sum(f[name] for f in flags) for name in NOT_CORE_REASONS},
    }
    out.update(zip(("volume_um3_median", "volume_um3_p90", "volume_um3_max"), _stats(volumes)))
    out.update(zip(("length_um_median", "length_um_p90", "length_um_max"), _stats(lengths)))
    return out


# --- README ------------------------------------------------------------------

def _column_docs(voxel: tuple[float, float, float]) -> dict[str, str]:
    vz, vy, vx = (f"{v:g}" for v in voxel)
    docs = {
        "mito_id": "Identifier of the mitochondrion. It is the smallest global label ID among its pieces, so it "
                   "traces back to the stitching work files (`objects.csv`, `id_map.csv`). IDs are not consecutive.",
        "volume_um3": "Volume of the whole mitochondrion in µm³ (voxel count × voxel volume), including any part "
                      "outside the region.",
        "length_um": "Length in µm: cable length of the centreline skeleton (see *How length is measured*). "
                     "Empty when `length_status` is `skipped_too_large`.",
        "volume_in_region_um3": "Volume of the part inside the region, in µm³.",
        "frac_in_region": "Fraction of the mitochondrion's voxels inside the region (0 to 1; 1 means entirely "
                          "inside).",
        "n_tiles": "Number of tiles the mitochondrion spans.",
        "tiles": "Names of those tiles, separated by `;`. Each name starts with the tile's box `z0-z1_y0-y1_x0-x1` "
                 "in voxels.",
        "cut_off_at_data_edge": "`true` when the mitochondrion reaches the edge of the imaged data, so part of it "
                                "may be missing and its volume and length are lower bounds.",
        "crosses_region_boundary": "`true` when it lies partly inside and partly outside the region. The outside "
                                   "part was not proofread.",
        "touches_unproofread_tile": "`true` when one of its tiles still holds automatic predictions, not "
                                    "proofread labels.",
        "length_status": "`measured`, or `skipped_too_large` when the object was too large to skeletonise "
                         "(its volume is still exact).",
        "bbox_z0": "Bounding box, lowest z (voxels, inclusive).",
        "bbox_z1": "Bounding box, end z (voxels, exclusive: the box covers z0 to z1 − 1).",
        "bbox_y0": "Bounding box, lowest y (voxels, inclusive).",
        "bbox_y1": "Bounding box, end y (voxels, exclusive).",
        "bbox_x0": "Bounding box, lowest x (voxels, inclusive).",
        "bbox_x1": "Bounding box, end x (voxels, exclusive).",
        "center_z_nm": f"Centroid (mean position of all its voxels), z in nm. Voxel z is at z × {vz} nm.",
        "center_y_nm": f"Centroid, y in nm. Voxel y is at y × {vy} nm.",
        "center_x_nm": f"Centroid, x in nm. Voxel x is at x × {vx} nm.",
        "core": "`true` when the measurement can be trusted as complete: not cut off at the data edge, not "
                "crossing the region boundary, not touching an unproofread tile, and no unresolved seam contacts. "
                "Use core objects for statistics that need whole mitochondria.",
        "not_core_reasons": "Why `core` is `false`, separated by `;`: `cut_off_at_data_edge`, "
                            "`crosses_region_boundary`, `touches_unproofread_tile`, `unresolved_contacts` (a seam "
                            "contact with a neighbouring piece was answered unsure or never reviewed, so the "
                            "mitochondrion may continue into it). Empty for core objects.",
    }
    return docs


SUMMARY_DOCS = {
    "objects": "number of mitochondria",
    "multi_tile": "how many span two or more tiles",
    "length_measured": "how many have a measured length",
    "length_skipped_too_large": "how many were too large to skeletonise",
    "cut_off_at_data_edge": "how many are cut off at the data edge",
    "crosses_region_boundary": "how many cross the region boundary",
    "touches_unproofread_tile": "how many touch an unproofread tile",
    "unresolved_contacts": "how many have an unresolved seam contact",
    "volume_um3_median": "median volume (µm³)",
    "volume_um3_p90": "90th percentile of volume (µm³; linear interpolation)",
    "volume_um3_max": "largest volume (µm³)",
    "length_um_median": "median length (µm), over measured lengths",
    "length_um_p90": "90th percentile of length (µm), over measured lengths",
    "length_um_max": "largest length (µm)",
}
EXCLUDED_DOCS = {
    "mito_id": "the excluded object",
    "excluded_global_ids": "the reviewer-listed global label IDs that caused the exclusion, separated by `;`",
    "n_tiles": "number of tiles it spans",
    "tiles": "those tiles, separated by `;`",
    "volume_um3": "its volume in µm³",
    "reaches_region": "`true` if it reaches into the region (otherwise it would not have been listed anyway)",
}


def _counts_line(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[k]:,} {k}" for k in ("yes", "no", "unsure", "undecided"))


# README sections shared with measure_volume (annotation.volume_measure), so
# the two deliveries describe their identical columns in the same words.

def readme_files_lines(mito_text: str) -> list[str]:
    return [
        "## What is here",
        "",
        "| File | Contents |",
        "|------|----------|",
        f"| `mitochondria.csv` | {mito_text} |",
        "| `summary.csv` | Counts and volume/length statistics, for all mitochondria and for core ones. |",
        "| `excluded.csv` | Objects left out because reviewers judged them not to be mitochondria. |",
        "| `README.md` | This file. |",
        "",
    ]


def readme_units_lines(voxel, box_coordinates: str) -> list[str]:
    vz, vy, vx = (f"{v:g}" for v in voxel)
    return [
        "## Units and coordinates",
        "",
        f"- Voxel size: **{vz} × {vy} × {vx} nm (Z, Y, X)**. One voxel is {voxel[0] * voxel[1] * voxel[2]:g} nm³.",
        "- Volumes are in µm³, lengths in µm, centroids in nm.",
        f"- Bounding boxes are in voxels in {box_coordinates}, Z, Y, X order. Lower bounds are inclusive and upper "
        "bounds exclusive.",
        f"- A voxel at index (z, y, x) sits at (z × {vz}, y × {vy}, x × {vx}) nm, so dividing a centroid by the "
        "voxel size gives its position in voxels.",
        "- `true`/`false` columns are text. Lists inside a cell are separated by `;`.",
        "",
    ]


def readme_tables_lines(column_docs: dict[str, str], excluded_intro: str,
                        excluded_docs: dict[str, str] = EXCLUDED_DOCS) -> list[str]:
    return [
        "## Columns of `mitochondria.csv`",
        "",
        "| Column | Meaning |",
        "|--------|---------|",
        *[f"| `{name}` | {text} |" for name, text in column_docs.items()],
        "",
        "## `summary.csv`",
        "",
        "Columns `metric`, `all` (every row of `mitochondria.csv`) and `core` (only rows with `core` = `true`). "
        "Metrics:",
        "",
        *[f"- `{name}`: {text}" for name, text in SUMMARY_DOCS.items()],
        "",
        "## `excluded.csv`",
        "",
        excluded_intro,
        "",
        *[f"- `{name}`: {text}" for name, text in excluded_docs.items()],
        "",
    ]


def readme_length_lines(crop_sentence: str, max_crop_voxels: int) -> list[str]:
    return [
        "## How length is measured",
        "",
        "Each mitochondrion is turned into a one-voxel-wide centreline skeleton with the TEASAR algorithm, "
        "using the kimimaro library with the lab's parameters (the same code and parameters Mito Data Studio "
        "uses for single volumes; TEASAR scale 1.5, const 500 nm). The voxel size above is passed in, so the "
        "skeleton is in nm. Length is the **cable length**: the sum of the lengths of all skeleton edges, so "
        f"branches count. Pieces smaller than {DUST_SIZE} voxels are ignored by the skeletoniser, so a very "
        "small mitochondrion can have length 0.",
        "",
        f"{crop_sentence} Objects whose box would exceed "
        f"{max_crop_voxels:,} voxels are not skeletonised (`length_status` = `skipped_too_large`); their "
        "volume, box and centroid are still exact.",
        "",
    ]


def build_readme(ctx: dict) -> str:
    voxel = ctx["voxel"]
    stats: DecisionStats = ctx["decisions"]
    s_all, s_core = ctx["summary_all"], ctx["summary_core"]
    lines = [
        "# Mitochondria: stitched measurements",
        "",
        f"Generated {ctx['generated_at']} from the stitching work directory `{ctx['work']}`.",
        "",
        *readme_files_lines("One row per mitochondrion that reaches into the region (exclusions removed)."),
        f"In short: **{s_all['objects']:,} mitochondria**, of which **{s_core['objects']:,} are core** "
        f"(complete and fully proofread; see `core`). {s_all['multi_tile']:,} span more than one tile. "
        f"{ctx['excluded_count']:,} object(s) were excluded.",
        "",
        "## Scope: region only",
        "",
        "Annotators proofread mitochondria only inside the region (a mask of roughly one cell in each tile). "
        "Labels outside it are unverified automatic predictions and may be inaccurate. So this delivery lists "
        "only mitochondria that reach into the region. A mitochondrion that is partly outside is still listed "
        "whole (its volume and length include the outside part), and is flagged `crosses_region_boundary`; "
        "`volume_in_region_um3` and `frac_in_region` give the inside share.",
        "",
        *readme_units_lines(voxel, "the coordinates of the whole dataset (the same coordinates as the tile names)"),
        *readme_tables_lines(_column_docs(voxel),
                             "Reviewers listed global label IDs that are not mitochondria. Each listed ID removes "
                             "the whole stitched object it belongs to. Columns:"),
        *readme_length_lines(f"A stitched mitochondrion is skeletonised once, as one piece, from a box around all "
                             f"of it (padded by {CROP_PAD} voxels), so the path across tile seams counts.",
                             ctx["max_crop_voxels"]),
        "## How stitching works",
        "",
        "The data arrive as tiles that were segmented one at a time, so a mitochondrion crossing a tile "
        "boundary appears as separate labels in each tile. Stitching rejoins them:",
        "",
        "1. Every label in every tile gets a unique global ID.",
        "2. At each shared tile face (seam), labels that touch across the face are listed as candidate pairs, "
        "with how much they overlap.",
        "3. Each candidate pair is decided yes (same mitochondrion), no, or unsure.",
        "4. Labels are joined through yes decisions only. A mitochondrion can span many tiles.",
        "5. Each joined object is measured as a whole.",
        "",
        "## How seam decisions were made",
        "",
    ]
    if stats.auto_rules:
        lines += [
            f"- **Automatic acceptance.** {stats.auto_accepted:,} clear-cut pairs were accepted by rule "
            f"(`{'`, `'.join(stats.auto_rules)}`): each label touches only the other across that seam, at "
            "least the given fraction of each label's face area is in contact, and the contact is at least "
            "the given number of voxels.",
        ]
    else:
        lines += ["- No automatic acceptance was used."]
    for check in stats.spot_checks:
        reviewers = f" ({', '.join(check['reviewers'])})" if check["reviewers"] else ""
        lines += [f"- **Spot check.** A reviewer{reviewers} judged a random sample of {check['checked']:,} "
                  f"auto-accepted pairs blind, without seeing the rule's answer: {check['agreed']:,} of "
                  f"{check['checked']:,} agreed (`{check['file']}`)."]
    if not stats.spot_checks and stats.auto_rules:
        lines += ["- No spot check of the automatic rule was supplied with this delivery."]
    human = stats.decided - stats.auto_accepted
    lines += [
        f"- **Human review.** {human:,} pair(s) were decided by reviewers on an offline review page that shows "
        "each pair on the electron microscopy images.",
    ]
    carry = ctx.get("carry")
    if carry:
        lines += [
            f"- **Carried over from an earlier run.** {carry['kept']:,} of the reviewed decisions were made on an "
            f"earlier stitching run (`{carry['old_work']}`) and carried over, because for each of them both "
            "tiles, both labels (same voxel count) and their contact across the seam were unchanged"
            + (f" ({carry['carried'] - carry['kept']:,} more were carried and then changed by a reviewer)"
               if carry["carried"] != carry["kept"] else "")
            + f". {carry['invalidated']:,} earlier decision(s) no longer applied, because a tile or label had "
            "changed, and those pairs were reviewed again.",
        ]
    lines += [
        f"- **All candidate pairs** (from `decisions_resolved.csv`): {_counts_line(stats.counts)}.",
    ]
    if stats.in_region is not None:
        lines += [f"- **Pairs with a label in the region:** {_counts_line(stats.in_region)}. Pairs entirely "
                  "outside the region were not reviewed and stay unjoined."]
    lines += [
        "- Unsure and undecided pairs never join. If one touches a listed mitochondrion it is flagged "
        "`unresolved_contacts` and the mitochondrion is not core.",
        "",
        "## Known limits",
        "",
        "- **Data edges.** Any tile face with no neighbouring tile counts as the edge of the data: the outer "
        "boundary, missing grid positions, and both faces of a missing slab of tiles (in this dataset the z "
        "range 864-1120 is missing entirely). Mitochondria touching them are flagged `cut_off_at_data_edge`.",
        f"- **Unproofread tiles.** {len(ctx['unproofread'])} tile(s) still hold automatic predictions"
        + (": " + ", ".join(f"`{t}`" for t in ctx["unproofread"]) if ctx["unproofread"] else "")
        + ". Mitochondria touching them are flagged `touches_unproofread_tile`.",
        "- **Outside the region** labels are unverified; see *Scope*.",
        f"- **Too large to skeletonise:** {s_all['length_skipped_too_large']:,} listed (see `length_status`); "
        "their lengths are missing, not zero.",
        "- Seam decisions are human or rule judgements and can be wrong; a wrong yes joins two mitochondria, a "
        "wrong no splits one.",
        "",
        "## Provenance",
        "",
        f"- Delivery generated: {ctx['generated_at']}, code revision `{ctx['revision']}`.",
        f"- Measurements (`stitch_measure`) finished {ctx['run'].get('finished_at', 'unknown')}, code revision "
        f"`{ctx['run'].get('code_revision', 'unknown')}`"
        + (f", command `{ctx['run']['command']}`" if ctx["run"].get("command") else "") + ".",
        f"- Input tiles ({len(ctx['tiles'])}), with the file each was read from:",
        "",
        "| Tile | File | Proofread |",
        "|------|------|-----------|",
        *[f"| `{t.name}` | `{t.path}` | {'no' if t.name in ctx['unproofread'] else 'yes'} |" for t in ctx["tiles"]],
        "",
    ]
    if ctx["no_region"]:
        lines += [f"- Tiles without a region file (counted as entirely outside the region): "
                  f"{', '.join(f'`{t}`' for t in ctx['no_region'])}.", ""]
    return "\n".join(lines)


# --- entry point -------------------------------------------------------------

@dataclass
class DeliverResult:
    out: Path
    rows: list[dict]
    excluded: list[dict]
    summary: list[dict]
    decisions: DecisionStats


def run_deliver(work, *, out=None, exclude_global_ids=None, overwrite: bool = False, auto_decisions=None,
                spot_checks=(), generated_at: str | None = None) -> DeliverResult:
    work = Path(work)
    data = check_consistency(work)
    fields = data.measurement_fields
    if data.measurements and REGION_VOXELS not in fields:
        raise StitchingError("measurements.csv has no region columns; the delivery is region-only, so run "
                             "stitch_ids --regions and the later phases")
    if data.measurements and any(f not in fields for f in (*BBOX_FIELDS, *CENTER_FIELDS)):
        raise StitchingError("measurements.csv has no bounding boxes or centroids; rerun stitch_measure (phase 6)")
    run = load_measure_run(work)
    voxel = tuple(float(v) for v in run["voxel_size_zyx_nm"])
    voxel_nm3 = voxel[0] * voxel[1] * voxel[2]
    excluded = read_exclusions(exclude_global_ids, data.objects) if exclude_global_ids else {}
    stats = decision_stats(work, data.resolved, auto_decisions, spot_checks)

    out = Path(out) if out else work / "delivery"
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise StitchingError(f"{out} is not empty; pass --overwrite to replace {', '.join(DELIVERY_FILES)}")

    rows, flags = [], []
    for r in sorted(data.measurements, key=lambda r: int(r["object_id"])):
        if int(r[REGION_VOXELS]) == 0 or int(r["object_id"]) in excluded:
            continue
        row, f = mito_row(r, voxel_nm3)
        rows.append(row)
        flags.append(f)
    core = [i for i, r in enumerate(rows) if r["core"] == "true"]
    s_all = summarise(rows, flags)
    s_core = summarise([rows[i] for i in core], [flags[i] for i in core])
    summary = [{"metric": m, "all": s_all[m], "core": s_core[m]} for m in SUMMARY_METRICS]

    member = {int(r["global_id"]): r for r in data.id_rows}
    by_object = {int(r["object_id"]): r for r in data.summary}
    excluded_rows = []
    for o, ids in sorted(excluded.items()):
        s = by_object[o]
        region = sum(int(member[g].get(REGION_VOXELS) or 0) for g in data.groups[o])
        excluded_rows.append({"mito_id": o, "excluded_global_ids": ";".join(map(str, ids)),
                              "n_tiles": len(s["tiles"].split(";")), "tiles": s["tiles"],
                              "volume_um3": repr(int(s["total_voxel_count"]) * voxel_nm3 / 1e9),
                              "reaches_region": _bool(region > 0)})

    tiles = load_tiles_csv(work)
    no_region = []
    if (work / "region_pairing.csv").exists():
        no_region = [r["tile"] for r in read_csv(work / "region_pairing.csv") if r["status"] == "no_region"]
    ctx = {
        "carry": carry_stats(work, data.resolved),
        "work": str(work), "voxel": voxel, "decisions": stats, "summary_all": s_all, "summary_core": s_core,
        "excluded_count": len(excluded_rows), "max_crop_voxels": int(run["max_crop_voxels"]),
        "unproofread": list(run.get("unproofread_tiles", [])), "run": run, "tiles": tiles, "no_region": no_region,
        "revision": code_revision(),
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "mitochondria.csv", MITO_FIELDS, rows)
    write_csv(out / "summary.csv", SUMMARY_FIELDS, summary)
    write_csv(out / "excluded.csv", EXCLUDED_FIELDS, excluded_rows)
    (out / "README.md").write_text(build_readme(ctx))
    return DeliverResult(out, rows, excluded_rows, summary, stats)
