"""Carry review decisions from an earlier stitching run into a re-run.

After tiles were added or relabelled, run phases 1-3 (and stitch_auto_decisions)
in a NEW work directory, then this command. It re-keys every earlier human
decision that is still valid onto the new candidates (decisions_carried.csv),
reports each earlier decision as carried, invalidated (with the reason) or
skipped (carry_report.csv), and lists the new candidates that still need
review (needs_review.csv, for stitch_review --only). No tile is read.
See docs/engineering/stitching.md, "Re-runs".

    python manage.py stitch_carry_decisions --old-work stitch_p10 --work stitch_p10_v2 \
        --decisions stitch_p10/decisions_region.csv
    python manage.py stitch_review --work stitch_p10_v2 --images /data/nag_p10/images \
        --only stitch_p10_v2/needs_review.csv
"""

import shlex

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError
from annotation.stitching_rerun import CARRIED_FILE, NEEDS_REVIEW_FILE, REPORT_FILE, run_carry


def _command_line(opts) -> str:
    args = ["stitch_carry_decisions", "--old-work", str(opts["old_work"]), "--work", str(opts["work"])]
    for path in opts["decisions"]:
        args += ["--decisions", str(path)]
    for flag in ("auto_decisions", "out"):
        if opts[flag]:
            args += ["--" + flag.replace("_", "-"), str(opts[flag])]
    args += [f"--{name.replace('_', '-')}" for name in ("include_auto", "region_only") if opts[name]]
    return shlex.join(args)


class Command(BaseCommand):
    help = "Carry still-valid review decisions from an earlier stitching run into a re-run."

    def add_arguments(self, parser):
        parser.add_argument("--old-work", required=True, help="Work directory of the earlier run.")
        parser.add_argument("--work", required=True,
                            help="Work directory of the re-run, after stitch_candidates (a different directory).")
        parser.add_argument("--decisions", action="append", required=True,
                            help="A decisions.csv of the earlier run; repeat for several files.")
        parser.add_argument("--auto-decisions", default=None,
                            help="decisions_auto.csv of the re-run (default: <work>/decisions_auto.csv if present). "
                                 "Pairs it decides are left out of needs_review.csv.")
        parser.add_argument("--include-auto", action="store_true",
                            help="Also carry earlier decisions made only by the auto rule (default: skip them; "
                                 "rerun stitch_auto_decisions instead).")
        parser.add_argument("--region-only", action="store_true",
                            help="List only in_region candidates in needs_review.csv (needs stitch_ids --regions).")
        parser.add_argument("--out", default=None, help=f"Carried decisions file (default <work>/{CARRIED_FILE}).")

    def handle(self, *args, **opts):
        try:
            result = run_carry(opts["old_work"], opts["work"], opts["decisions"], out=opts["out"],
                               include_auto=opts["include_auto"], auto_decisions=opts["auto_decisions"],
                               region_only=opts["region_only"], command=_command_line(opts))
        except StitchingError as exc:
            raise CommandError(str(exc))

        c = result.counts
        self.stderr.write(f"tiles: {c['tiles_added']} added, {c['tiles_removed']} removed since the earlier run")
        self.stderr.write(f"earlier decisions: {c['earlier_decisions']}: {c['carried']} carried, "
                          f"{c['invalidated']} invalidated, {c['skipped_auto']} skipped (auto rule)")
        for reason, count in c["invalidated_by_reason"].items():
            self.stderr.write(f"  invalidated, {reason}: {count}")
        for row in [r for r in result.report if r["status"] == "invalidated"][:20]:
            self.stderr.write(f"    seam {row['old_seam']} {row['old_tile_a']}:{row['label_a']} -> "
                              f"{row['old_tile_b']}:{row['label_b']} ({row['decision']}): {row['detail']}")
        if c["invalidated"] > 20:
            self.stderr.write(f"    ... and {c['invalidated'] - 20} more in {REPORT_FILE}")
        if c["auto_conflicts"]:
            self.stderr.write(self.style.WARNING(
                f"{c['auto_conflicts']} carried decision(s) disagree with the new auto rule; the reviewer's "
                f"decision must win: import decisions_auto.csv into the review page first, then {CARRIED_FILE} "
                f"(see {REPORT_FILE})"))
        why = ", ".join(f"{count} {name}" for name, count in sorted(c["needs_review_by_why"].items()))
        self.stderr.write(f"new candidates: {c['new_candidates']}: {c['carried']} carried, {c['auto_decided']} decided "
                          f"by the auto rule, {c['needs_review']} need review" + (f" ({why})" if why else "")
                          + f" -> {NEEDS_REVIEW_FILE}")
        self.stderr.write(self.style.SUCCESS("ok"))
