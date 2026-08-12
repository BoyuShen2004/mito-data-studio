# Attribution Register

One row per reused artifact. **An entry is created *before* the code is
committed**, never after. Required by decision **D3**.

Companion to the repo-root [`THIRD_PARTY_NOTICES.md`](../../../THIRD_PARTY_NOTICES.md).

## Relationship vocabulary

| Value | Meaning | Licence obligation |
|---|---|---|
| `copied` | Bytes taken from upstream, unmodified | Full — upstream licence governs |
| `modified` | Upstream file edited in place | Full — plus a modification notice |
| `ported` | Line-by-line translation to another language | Full — translation is a derivative work |
| `independently reimplemented` | Written from a behavioural/algorithmic description | **None** — describing behaviour is not copying |
| `api-integrated` | Called over a process/network boundary | Narrower — no source combination |

> `independently reimplemented` requires the implementer to have worked from
> the *description* (docs, papers, our own research pack), not from the
> upstream source file. Reading upstream to verify behaviour is fine;
> transcribing it is not, and makes the row `ported`.

---

## Reused artifacts

| # | Source repo | Upstream path | Commit | Licence | Relationship | Destination | Notices preserved | Notes |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | **No WEBKNOSSOS code reused as of 2026-08-01. The integrated upgrade profile is an independent implementation of the behavioural references below.** |

---

## Behavioural references (no licence obligation)

Recorded for provenance and honesty, not because they create obligations.
These informed design; no source was copied.

| Area | Upstream reference | Commit | How it was used |
|---|---|---|---|
| Task assignment concurrency | `app/models/task/Task.scala` (`assignNext`, `findNextTaskQ`) | `a24aecc6f` | Read to verify Serializable + 50-retry strategy and eligibility ordering. Findings in `AUDIT_DELTA.md` §3. mito reimplements the *behaviour* in Django/Postgres. |
| Task instance accounting | `schema/schema.sql`, `schema/evolutions/008`, `026`, `107` | `a24aecc6f` | Read to establish that only `pendingInstances >= 0` is enforced (the upper bound was dropped in `026`). Corrects the research pack. |
| Open-task capacity gate | `app/models/task/TaskService.scala` (`getAllowedTeamsForNextTask`) | `a24aecc6f` | Read to confirm `maxOpenPerUser` narrows the eligible team set. |
| Volume interpolation | `frontend/.../volume_interpolation_saga.ts` | `a24aecc6f` | Read to verify the SDF + linear-blend description in research doc `07` is accurate (it is, line-for-line). Phase 8 implements from that description. |
| Chunk pull scheduling | `frontend/.../pullqueue.ts` | `a24aecc6f` | Read to confirm batch/priority/abort constants. Concepts are generic. |
| Product model / task concepts | `docs/tasks_projects/concepts.md` + docs.webknossos.org | `a24aecc6f` | Documentation, not source. |

---

## Open items

| Item | Status |
|---|---|
| `vendor/efficient_sam` — pin upstream commit, add LICENSE | **closed 2026-08-01** — exact bytes matched labelmeai/efficient-sam tag `onnx-models-20231225`, commit `6aebcba09318c4dfe2f9560f7a3f8c42d8b01657`; Apache-2.0 copied beside the weights. |
| `vendor/sam2` — pin upstream commit, add LICENSE | **closed 2026-08-01** — all vendored source/config files match facebookresearch/sam2 commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`; official checkpoint bytes verified; Apache-2.0 copied beside the tree. |
| Choose a LICENSE for mito-data-studio itself | **closed conservatively 2026-08-01** — root `LICENSE` grants no permission for first-party code and defers third-party material to its own terms. |
| Generate full dependency licence manifest before distribution | not started |

The 2026-07-28 correction remains part of the audit history: the earlier
Apache-2.0 claim was temporarily downgraded until source, bytes and license text
could be proven. The 2026-08-01 closure is based on fresh official downloads and
byte-for-byte comparison, not inference from filenames.
