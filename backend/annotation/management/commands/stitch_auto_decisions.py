"""Auto-accept clear-cut seam merges as a decisions.csv.

Marks "yes" only for candidates that are one-to-one on their seam (each label
has exactly that one partner there), with min(frac_a, frac_b) >= --min-frac
and contact_voxels >= --min-contact. Everything else stays undecided for human
review. The output can be imported into the review page and passed to
stitch_objects. --sample N writes a reproducible random sample of the accepted
pairs for a spot-check page (stitch_review --only). See
docs/engineering/stitching.md.

    python manage.py stitch_auto_decisions --work stitch_p10
    python manage.py stitch_auto_decisions --work stitch_p10 --sample 100 --seed 1
"""

from django.core.management.base import BaseCommand, CommandError

from annotation.stitching import StitchingError, run_auto_decisions


class Command(BaseCommand):
    help = "Write decisions_auto.csv: 'yes' for one-to-one candidates above the thresholds."

    def add_arguments(self, parser):
        parser.add_argument("--work", required=True, help="Work directory with candidates.csv.")
        parser.add_argument("--out", default=None, help="Output path (default <work>/decisions_auto.csv).")
        parser.add_argument("--min-frac", type=float, default=0.5,
                            help="Minimum of frac_a and frac_b (default 0.5).")
        parser.add_argument("--min-contact", type=int, default=50,
                            help="Minimum contact_voxels (default 50).")
        parser.add_argument("--sample", type=int, default=0,
                            help="Also write a random sample of this many accepted pairs.")
        parser.add_argument("--seed", type=int, default=0, help="Random seed for --sample (default 0).")
        parser.add_argument("--sample-out", default=None,
                            help="Sample path (default <work>/auto_sample_<n>_seed<seed>.csv).")

    def handle(self, *args, **opts):
        try:
            result = run_auto_decisions(opts["work"], opts["out"], min_frac=opts["min_frac"],
                                        min_contact=opts["min_contact"], sample=opts["sample"],
                                        seed=opts["seed"], sample_out=opts["sample_out"])
        except StitchingError as exc:
            raise CommandError(str(exc))

        self.stderr.write(
            f"{result['accepted']} of {result['candidates']} candidate(s) accepted "
            f"({result['reviewer']}) -> {result['out']}"
        )
        if result["sample_out"]:
            self.stderr.write(f"sample of {result['sample']} (seed {opts['seed']}) -> {result['sample_out']}")
        self.stderr.write(self.style.SUCCESS("ok"))
