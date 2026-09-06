# 9. Instance annotation, quality, and delivery

Three optional modules, each behind its own feature flag. On a deployment where
a flag is off, the corresponding surface says so plainly rather than
disappearing — so "not enabled" is always distinguishable from "broken".

| Surface | Flag | Where |
| --- | --- | --- |
| Instance morphology and QA flags | `FEATURE_INSTANCE_ANNOTATION` | Annotate view, project **Quality** tab |
| Inbox | `FEATURE_NOTIFICATIONS` | The 🔔 in the navbar, `/inbox` |
| Milestones and delivery analytics | `FEATURE_MILESTONES` | Project **Delivery** tab |
| Measured quality | `FEATURE_QUALITY_METRICS` | Review page, project **Quality** tab, a person's page |

## Recording what an instance is

Select an instance in the Annotate view and the **Instance details** panel
above the Labels list applies to it. It has two independent halves, and that
independence is the point: one mitochondrion can be both *swollen* and
*uncertain*, so you never have to choose which fact to record.

**Morphology** — what the mitochondrion *is*. Normal, elongated/tubular,
fragmented/punctate, swollen, donut/toroidal (MOAS), megamitochondrion, cristae
disrupted, undergoing mitophagy. Click the selected chip again to clear it.

Leaving it blank is a real state: **unclassified**, meaning nobody has judged
the shape. That is deliberately not the same as **Normal**, which is the
positive claim that somebody looked and found nothing unusual. The project
summary counts them separately.

**Annotation issues** — what is wrong with the *labelling*, not the biology:

| Flag | Meaning |
| --- | --- |
| Uncertain | Needs a second look |
| Cut off by the volume boundary | The object continues outside the data |
| Under-segmented | One id covers two objects |
| Over-segmented | One object is split across ids |
| Not a mitochondrion | A false positive |

The first, third, fourth and fifth mark an instance for review; boundary
truncation does not, because it is a fact about the data rather than a request
for attention.

An instance with nothing recorded stores no row at all, so a project's
"instances annotated" count only ever reflects work somebody actually did.

## The inbox

The 🔔 in the navbar carries an unread count and opens `/inbox`. You are
notified when a task is assigned to you, when a submission of yours is
reviewed, when something lands in your review queue, when a hard case you are
part of gets a reply, and when a deadline or a quality threshold trips.

You are never notified about your own actions.

Following a notification marks it read; **Mark all read** clears the badge.

Deadline and milestone warnings come from a scheduled command rather than from
an action, so they only appear where an operator has put `notify_deadlines` on
a timer — see [deployment](../deployment.md#scheduled-notification-tasks).

## Milestones and delivery

The project **Delivery** tab holds dated targets and the analytics that read
them. A manager (or the requester who owns the project) adds a milestone with a
name, a due date, and a number of tasks to approve, and can edit any of the
three later with **Edit** on the row. **Remove** asks for confirmation first;
it deletes the target and its volume scope, never the task progress itself. Progress is recomputed from
the tasks on every load and never stored, so it cannot drift from the work it
describes.

A milestone with no volume scope covers the whole project. Scoping it to
specific volumes fixes what it means as the project grows.

Below the milestones:

- **Throughput** — tasks approved per day, zero-filled across the window so a
  gap always means "no work", never "no data".
- **People** — assigned, approved, mean review rounds, mean elapsed time to
  submit, and measured annotation time. A dash in the time column means the
  volume predates time tracking and the real total is unknowable; it is never
  shown as zero.
- **Needs attention** — overdue tasks, tasks due soon, and submissions that
  have been waiting for review too long.

The manager dashboard has an **Attention** tab carrying the same three figures
**across every project at once**, which is the view for deciding where to look
first. It is manager-only: "every project" has no membership to scope it by,
and a filtered subset would read as "nothing is overdue" to somebody who simply
cannot see it.

## Measured quality

Two kinds of score, both computed automatically:

**Gold standard.** On a volume's page a manager sees a **Gold standard** card:
tick it and pick an already-approved submission as the trusted answer, and every
submission on that volume is scored against it. The reference has to be work
that already passed review, so the card says so plainly when a volume has no
approved submission yet.

The card is invisible to everyone except managers, and the annotator is never
told the volume is a test — a known test measures attention, not ordinary
working accuracy.

**Reviewer agreement.** When a reviewer corrects work — by editing the labels
and submitting their own version, then approving that instead of the
annotator's — the difference between the two submissions is how much correction
the work needed. This costs no extra annotation; nobody labels the same voxels
twice.

Both report the same metrics on the review page:

- **Dice** and **IoU** — how much of the foreground agrees, voxel by voxel.
- **Instance F1**, **false merges**, **false splits** — whether individual
  mitochondria came out as separate objects.

Read them together. A segmentation can score a near-perfect Dice and still be
unusable if every neighbouring pair of mitochondria is fused into one id; only
the instance-level numbers can see that.

**A dash means the metric was not measured.** It is never rendered as zero. If
a submission and its reference disagree about shape, no score is written at all
— that is a configuration problem, not an annotation-quality result.

A person's page shows their rolling gold-standard Dice over their recent scored
submissions. Somebody who has never been tested shows a dash, not a zero.
