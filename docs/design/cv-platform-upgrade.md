# Design — mitochondria label schema, project management, and quality metrics

Status: **implemented**. Revision 2.

Implemented as specified, with one deliberate deviation recorded in §1.5.

Revision 1 proposed a general ontology system, an annotation-guideline module,
and a configurable domain vocabulary. All three were **cut** on review as
over-general. This revision is deliberately narrower and mitochondria-specific.

Read alongside [Product invariants](../product-invariants.md), which this
design does not modify.

| Module | Purpose | Flag |
| --- | --- | --- |
| 1. Instance annotation | Per-instance morphology and QA flags | `FEATURE_INSTANCE_ANNOTATION` |
| 2. Project management | Notifications, milestones, analytics | `FEATURE_NOTIFICATIONS`, `FEATURE_MILESTONES` |
| 3. Quality metrics | Dice/IoU, gold standard, annotator-vs-reviewer agreement | `FEATURE_QUALITY_METRICS` |

### Explicitly out of scope

Recorded so a later reader does not mistake these for oversights:

* **No generic ontology.** No user-definable class hierarchy, no per-dataset
  class scoping, no manifest import, no class versioning. The vocabulary below
  is fixed in `core/choices.py` and changes by code review, like every other
  enum in this codebase.
* **No annotation guidelines module.**
* **No domain/terminology profiles.** EM and mitochondria wording stays as it
  is; this remains a mitochondria tool.
* **No consensus groups.** Agreement is measured between an annotator's
  submission and the reviewer's corrections, which is work that already
  happens — never by paying two people to annotate the same voxels twice.

---

## 0. Current state this attaches to

Facts established by reading the code, so the design below attaches to real
seams rather than assumed ones.

* Hierarchy is `Project → Dataset → Volume → AnnotationTask`; `Volume`
  denormalizes `project` for query speed.
* Working labels are **instance id** rasters. `frontend/src/features/viewer/
  labelColor.ts` colors an instance by hashing its integer id, and the backend
  mirrors that hash in `annotation/visualization/slice_io.py`. There is no
  per-instance metadata of any kind today.
* QC is a provider registry (`annotation/quality_control/registry.py`) selected
  by `MITO_QC_PROVIDER`. The shipped `basic` provider checks link-to-task,
  file-exists, non-empty, and extension.
* `AnnotatorProfile.quality_score` is a `FloatField(default=0.0)` that nothing
  ever writes.
* `AuditEvent` is an append-only (actor, verb, target_type, target_id,
  metadata) log. There is **no** notification model in the backend.
* `core/statistics.py` computes elapsed-time aggregates behind
  `FEATURE_DASHBOARDS` under a hard rule: every figure comes from a grouped
  database query, never a Python loop over rows. This design keeps that rule.
* `WorkSession` / `WorkInterval` record real annotation minutes;
  `TimeTracking.LEGACY_EXEMPT` marks volumes whose history is unknowable and
  must report `-`, never `0`.
* `approve_submission` calls `_install_submission_as_official`, which promotes
  the submitted label to the volume's official label. That promotion is the
  natural hook for quality scoring.
* The frontend has **no charting dependency**. All charts are hand-authored
  inline SVG.

---

## 1. Mitochondria instance annotation

### 1.1 Shape

Two orthogonal dimensions on one row. A mitochondrion can be both *swollen*
(what it is) and *uncertain* (how confident the annotator is); collapsing them
into one enum would force a false choice.

```python
# core/choices.py

class MitoMorphology(models.TextChoices):
    """Morphological phenotype of one mitochondrion instance."""
    NORMAL       = "normal", "Normal"
    ELONGATED    = "elongated", "Elongated / tubular"
    FRAGMENTED   = "fragmented", "Fragmented / punctate"
    SWOLLEN      = "swollen", "Swollen"
    DONUT        = "donut", "Donut / toroidal (MOAS)"
    MEGA         = "mega", "Megamitochondrion"
    CRISTAE_LOSS = "cristae_loss", "Cristae disrupted / lost"
    MITOPHAGY    = "mitophagy", "Undergoing mitophagy"


class InstanceQaFlag(models.TextChoices):
    """What is wrong (or unresolved) about how this instance is labelled."""
    UNCERTAIN          = "uncertain", "Uncertain — needs a second look"
    BOUNDARY_TRUNCATED = "boundary_truncated", "Cut off by the volume boundary"
    NEEDS_SPLIT        = "needs_split", "Under-segmented — one id covers two objects"
    NEEDS_MERGE        = "needs_merge", "Over-segmented — one object split across ids"
    FALSE_POSITIVE     = "false_positive", "Not a mitochondrion"
```

### 1.2 Model — `annotation/models.py`

```python
class LabelInstanceAnnotation(models.Model):
    """Per-instance metadata for one label id in one volume.

    An absent row means "not annotated", which is exactly today's behaviour —
    that is what makes this module additive. Every existing volume is valid on
    day one with zero rows in this table, and nothing backfills it.

    Keyed on (volume, label_id) rather than on the task: instance ids are a
    property of the volume's label raster, and a task's z-range can be
    re-scoped without the annotation on an instance becoming meaningless.
    """

    volume     = FK(Volume, related_name="instance_annotations", on_delete=CASCADE)
    label_id   = PositiveIntegerField()

    # Both dimensions are optional and independent. Blank morphology means
    # "nobody has classified this one", never "normal".
    morphology = CharField(max_length=20, choices=MitoMorphology.choices, blank=True)
    # Multi-select. Stored as a JSON list of InstanceQaFlag values, validated
    # in the serializer against the enum; a list keeps "flagged for two reasons"
    # expressible without a second table for a handful of short strings.
    qa_flags   = JSONField(default=list, blank=True)
    note       = CharField(max_length=280, blank=True)

    # Provenance — who last touched it, so a reviewer can tell an annotator's
    # own uncertainty flag from one the reviewer added.
    updated_by = FK(User, null=True, blank=True, on_delete=SET_NULL,
                    related_name="instance_annotations")
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

    class Meta:
        ordering = ["volume_id", "label_id"]
        constraints = [
            UniqueConstraint(fields=["volume", "label_id"],
                             name="uniq_instance_annotation"),
        ]
        indexes = [
            # The two hot reads: "this volume's panel" and "everything flagged
            # for review across a project".
            models.Index(fields=["volume", "morphology"],
                         name="idx_instance_morphology"),
        ]
```

A row whose `morphology` is blank, `qa_flags` empty, and `note` blank is
deleted rather than stored, so the table never accumulates empty rows and
"has a row" always means "carries information".

### 1.3 Write path

One service function, one write point:

```python
# annotation/services.py
def set_instance_annotation(volume, label_id, *, actor, morphology=None,
                            qa_flags=None, note=None) -> LabelInstanceAnnotation | None
```

It upserts, deletes-when-empty, and writes an `AuditEvent`. Permission is
`can_annotate_task` for the task covering that instance, or manager — reusing
the existing checks rather than inventing a parallel rule.

### 1.4 API

| Method & path | Purpose |
| --- | --- |
| `GET /api/volumes/<id>/instance-annotations/` | Whole map for the viewer panel |
| `GET /api/tasks/<id>/instance-annotations/` | Same, scoped to the task's z-range |
| `PUT /api/tasks/<id>/instance-annotations/<label_id>/` | Upsert one instance |
| `DELETE /api/tasks/<id>/instance-annotations/<label_id>/` | Clear one instance |
| `GET /api/projects/<id>/instance-annotations/summary/` | Grouped counts per morphology and per flag |

The summary endpoint is one grouped query, per the `core/statistics.py` rule.

### 1.5 Frontend

* **`LabelsPanel.tsx` was left unchanged** for the same reason: it is a
  736-line surface with its own filter/sort/lifecycle state, and a per-row
  morphology column is a follow-up rather than part of this slice.
* **`InstanceAnnotationPanel.tsx`** — a new panel for the currently active
  instance: morphology radio group, QA flag checkboxes, note field. Hotkeys
  reuse the server-side `UserProfile.annotate_shortcuts` mechanism already
  built for tools, so bindings follow the account between machines.
* **`labelColor.ts` was deliberately left untouched.** The plan called for
  morphology to tint the canvas fill. It was not built: the colour path runs
  through `AnnotationCanvas.tsx` (7,200 lines) and is mirrored voxel-for-voxel
  by `annotation/visualization/slice_io.py`, so tinting means changing two
  renderers in step, and getting it wrong changes how existing volumes look.
  The panel delivers the recording workflow without that risk. If tinting is
  added later it must keep an unannotated instance rendering byte-identically
  and carry a regression test saying so — recorded in
  [product invariants](../product-invariants.md).
* **`ReviewSubmissionPage`** gains a "flagged instances" list so a reviewer
  lands on the annotator's own uncertainty rather than hunting for it.
* Project detail gains a morphology distribution readout, fed by the summary
  endpoint.

---

## 2. Project management

### 2.1 Notifications

```python
class Notification(models.Model):
    """One thing that happened which one person still needs to look at.

    Deliberately separate from AuditEvent: audit answers "what happened to this
    object, forever"; a notification answers "what does this person still owe
    attention to". Different lifetimes, different indexes, and an audit row
    must never be marked read.
    """
    recipient   = FK(User, related_name="notifications", on_delete=CASCADE)
    verb        = CharField(64, choices=NotificationVerb.choices)
    actor       = FK(User, null=True, on_delete=SET_NULL)
    target_type = CharField(64, blank=True)   # (type, id) like AuditEvent, so a
    target_id   = CharField(64, blank=True)   # notification outlives its subject
    title       = CharField(200)
    body        = CharField(500, blank=True)
    url         = CharField(300)              # SPA deep link
    read_at     = DateTimeField(null=True, blank=True, db_index=True)
    created_at  = DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"])]
```

`NotificationVerb` (new in `core/choices.py`): `task.assigned`,
`task.withdrawn`, `submission.received`, `submission.reviewed`,
`hard_case.opened`, `hard_case.replied`, `deadline.approaching`,
`milestone.at_risk`, `quality.flagged`.

**Emission points**, all inside existing service functions so no view changes:
`assign_task_to_annotator`, `withdraw_project_assignments`,
`submit_annotation` / `submit_inapp_annotation`, `approve_submission` /
`reject_submission` / `request_revision`, `HardCaseMessage` creation. The
time-based verbs come from a `notify_deadlines` management command, idempotent
per `(recipient, target, day)` so re-running it cannot spam.

| Method & path | Purpose |
| --- | --- |
| `GET /api/notifications/?unread=1` | Inbox, paginated |
| `POST /api/notifications/read/` | Mark listed ids — or all — read |
| `GET /api/notifications/unread-count/` | Cheap poll for the bell badge |

Frontend: `NotificationBell` in `Navbar` polling the count every 60s, and
`pages/InboxPage.tsx` at `/inbox`.

### 2.2 Milestones

```python
class Milestone(models.Model):
    project       = FK(Project, related_name="milestones")
    name          = CharField(200)
    description   = TextField(blank=True)
    due_on        = DateField()
    order         = PositiveIntegerField(default=0)
    status        = CharField(choices=MilestoneStatus)   # planned/active/met/missed
    # Empty = every volume in the project. An explicit scope beats a saved
    # filter: a milestone must keep meaning the same thing as the project grows.
    volumes       = M2M(Volume, blank=True)
    target_metric = CharField(choices=["tasks_approved", "volumes_completed"])
    target_value  = PositiveIntegerField()
    completed_at  = DateTimeField(null=True, blank=True)
```

Progress is a grouped query in `core/statistics.py`.

### 2.3 Analytics — extending `core/statistics.py`

Each of these is one grouped query:

* `throughput_series(project, bucket="day")` — approvals per day via
  `TruncDate(approved_at)`, zero-filled across the range so a chart never has
  to guess which days are missing.
* `burndown(milestone)` — remaining vs. ideal, from the target and the series.
* `annotator_productivity(project)` — per person: tasks approved, mean review
  rounds (from `submission_count`), mean elapsed-to-submit, and **real**
  annotated minutes from `WorkInterval`. Volumes marked
  `TimeTracking.LEGACY_EXEMPT` report `-`, never `0`.
* `attention_queue(project)` — overdue tasks, tasks due within N days and not
  submitted, and submissions pending review longer than N days.

Endpoints: `GET /api/statistics/project/<id>/timeseries/`,
`.../productivity/`, `.../attention/`, plus milestone CRUD under
`/api/projects/<id>/milestones/`.

Frontend: an `Analytics` tab on `ProjectDetailPage` (burndown line, throughput
bars, status distribution, annotator table), a `Milestones` panel, and
overdue/at-risk chips on `TaskTable` rows. All inline SVG with explicit
light/dark tokens.

---

## 3. Quality metrics

### 3.1 Gold standard

A gold-standard volume is scored against **an approved submission**, not a
separately curated file — the trusted answer is work that already went through
review.

```python
# volumes/models.py — two additive fields
is_gold_standard        = BooleanField(default=False)
reference_submission    = FK("annotation.AnnotationSubmission", null=True,
                             blank=True, on_delete=SET_NULL,
                             related_name="gold_standard_for")
```

The annotator is not told a volume is a gold standard. On submit, the score is
computed against `reference_submission`'s stored label snapshot — which is
immutable by design, since in-app submissions already copy the working label
at submit time rather than pointing at a mutable draft.

### 3.2 Score record

```python
class QualityScore(models.Model):
    submission           = FK(AnnotationSubmission, related_name="quality_scores")
    kind                 = CharField(choices=["gold_standard", "reviewer_agreement"])
    reference_submission = FK(AnnotationSubmission, null=True, on_delete=SET_NULL,
                              related_name="scored_against")
    # Semantic overlap
    dice, iou, precision, recall = FloatField(null=True)
    # Instance-level — what actually matters for EM connectomics
    instance_f1              = FloatField(null=True)   # matched at IoU >= 0.5
    false_merges             = PositiveIntegerField(null=True)
    false_splits             = PositiveIntegerField(null=True)
    variation_of_information = FloatField(null=True)
    provider    = CharField(64)
    detail      = JSONField(default=dict)
    computed_at = DateTimeField(auto_now_add=True)
```

**Every metric is nullable.** A provider that cannot compute one records its
absence, never a misleading `0.0` — the same discipline `region_mask_coverage`
and `TimeTracking.LEGACY_EXEMPT` already enforce.

### 3.3 Where scoring runs — **changed during implementation**

The plan put the metrics behind a new `OverlapQualityControlProvider` in the
`QC_PROVIDERS` registry. That was **not** built, and should not be: scoring is
already triggered from the service layer — `_after_submission` for the
gold-standard case and `_after_approval` for reviewer agreement — which is
where the reference submission is actually known. A QC provider would have been
a second path producing the same numbers from a place that has less context,
and two paths to one figure is how the two start disagreeing.

The metrics themselves live in `annotation/quality_metrics.py` (pure functions
over a contingency table, no Django) and are driven by
`annotation/quality_scoring.py`. `MITO_QC_PROVIDER` keeps its existing meaning
and needs no change.

Volumes are read in z-slabs (`SLAB_DEPTH = 8`) rather than whole, so scoring a
large volume stays bounded in memory.

### 3.4 Annotator-vs-reviewer agreement

No duplicated annotation. When a reviewer approves a submission **after
editing it**, the difference between what the annotator submitted and what the
reviewer approved *is* the error signal, and it is already on disk:

* `approve_submission` → `_install_submission_as_official` promotes the label.
* If the promoted label differs from the annotator's submitted snapshot, the
  service computes a `QualityScore(kind="reviewer_agreement")` between the two.
* Low agreement raises a `quality.flagged` notification to the manager.

This measures exactly the thing worth measuring — how much correction the work
needed — at zero extra annotation cost.

### 3.5 Feeding `AnnotatorProfile.quality_score`

It becomes **derived**: the mean gold-standard Dice over the person's last K
(default 10) scored submissions, recomputed on each `QualityScore` write. The
column stays for backwards compatibility, the docstring changes to say it is
derived, and a person with no scored submissions reports **`None`, not `0.0`**
— "not measured" and "measured as zero" must not look alike.

### 3.6 Frontend

* `ReviewSubmissionPage` gains a metric card (Dice, IoU, false merges/splits)
  shown only when a score exists.
* `PersonPage` gains a quality trend sparkline.
* A `Quality` tab on `ProjectDetailPage`: gold-standard results, agreement
  distribution, and flagged submissions.

---

## 4. Cross-cutting

**Migrations.** All additive. Every new column is nullable or defaulted; no
existing column is altered or dropped; no data is rewritten. Expand only — no
contract step is needed.

New: `annotation/0025_instance_annotations`, `accounts/0011_notifications`,
`projects/0013_milestones`, `volumes/0016_gold_standard`,
`annotation/0026_quality_scores`.

**Flags.** Added to the `settings.py` flag block defaulting to `False`, so an
existing deployment upgrades to identical behaviour and opts in per feature.

**Performance.** `Notification` is indexed on `(recipient, read_at,
-created_at)`; `LabelInstanceAnnotation` on `(volume, morphology)`; the
statistics additions reuse the existing no-Python-row-loops rule.

**Security.** Every new endpoint reuses `core/permissions.py` and
`annotation.services.is_project_member`. No new public/token surface is added.

**Documentation.** `docs/user-guide/` gains sections for the instance panel,
the inbox, milestones, and quality. `docs/product-invariants.md` gains the
"unmeasured is not zero" invariant.

## 5. Build order

1. Instance annotation (§1) — smallest, and the panel is the most visible win.
2. Notifications (§2.1) — needed by §3.4's flagged-quality alert.
3. Milestones and analytics (§2.2, §2.3).
4. Quality (§3).
