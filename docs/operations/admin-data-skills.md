# Agent skills for administrative data operations

Use these skills to inspect, archive, import and validate Mito Data Studio data
with a coding agent. They encode the operational lessons from the verified P10
import without turning its host paths, project IDs or calibration into global
defaults. They do not grant accounts, manager roles, shell privileges or authority
to delete live data. Existing task authorization remains in effect.

The [illustrated P10 guide](https://docs.google.com/document/d/1OG_ZShmILcS3QB503Wvvn93wIbVIqL5AKXhj0QNO9ms)
contains live reference screenshots and specific prompts for the lab's five-volume
exercise. Google Drive access is separate from repository access. This page and
the skill packages are the repository-native handoff; they remain usable without
the external document or its deployment-specific artifacts.

## Install and invoke

From a reviewed checkout, choose your coding tool:

```bash
python3 scripts/skills/install.py --tool codex
python3 scripts/skills/install.py --tool codex --apply
# Alternatives: --tool claude, --tool antigravity, or --tool all
```

The default is a preview. Applying copies the four packages into the selected
personal skill directories and verifies their content hashes. Differing existing
skills and skill-folder symlinks are refused before any copying. Identical
reinstallation is safe. Review differences manually when updating an installed
skill; the installer does not overwrite local edits or modify tool settings.

| Tool | Personal location | Explicit invocation |
| --- | --- | --- |
| Codex | `$CODEX_HOME/skills` or `~/.codex/skills` | `$mito-admin-preflight` |
| Claude Code | `~/.claude/skills` | `/mito-admin-preflight` |
| Antigravity | `~/.gemini/config/skills` | `/mito-admin-preflight` or name the skill |

Refresh or start a session after installation. If discovery is unavailable,
ask the agent to read the installed `SKILL.md` directly. This does not bypass
host or application permissions. See the official
[Claude Code](https://code.claude.com/docs/en/skills) and
[Antigravity](https://antigravity.google/docs/skills?app=antigravity)
references. Installed files are not evidence that every tool's interactive
runtime has been exercised.

## Choose the operation

| Skill | Outcome |
| --- | --- |
| [mito-admin-preflight](../../skills/mito-admin-preflight/SKILL.md) | Ground source profile, calibration, dependencies, access and incremental capacity |
| [mito-admin-archive](../../skills/mito-admin-archive/SKILL.md) | Verified versioned copies, restore mapping and scoped local retirement |
| [mito-admin-import](../../skills/mito-admin-import/SKILL.md) | Verified local staging and official-service registration with bounded task creation |
| [mito-admin-validate](../../skills/mito-admin-validate/SKILL.md) | Metadata, viewer, source integrity, existing-work and derivative checks |

The entrypoints load only relevant references. Read the current
[product invariants](../product-invariants.md),
[data/storage design](../engineering/data-and-storage.md), and the intended
deployment's instructions before a write. Automatic skill selection remains
available; the operation still requires authorization for its actual scope.

## Copyable prompts

The prompts below use plain skill names across tools. Supply your actual instance,
project and source manifest; an agent must discover and report missing values
rather than guessing them. Use the same task/session to retain run state.

### Inspect first

> Use mito-admin-preflight on the Mito Data Studio instance, project and source
> manifest I identify. Inspect the actual production checkout, effective reader
> account, source/backup access, live dataset/task/submission dependencies and
> source keys/dtypes/shapes. Trace authoritative physical spacing and units to
> the generating pipeline. Budget incremental copies, working labels, snapshots,
> derivatives, reserve and concurrent commitments. Save a disjoint manifest and
> a concrete readiness report. Do not import, delete, assign work, change global
> flags or invent credentials. Continue independent checks if one prerequisite
> is missing.

### Archive a concrete candidate set

> Use mito-admin-archive for the candidate files I identify. Trace DB references,
> review/lock state, services, jobs, open readers, caches and rollback dependencies.
> Produce an exact manifest with hashes, sizes, metadata, scientific status and
> restore mapping. Copy to a new persistent version and verify every source and
> archive file by full SHA256 and size. Preserve local originals unless this
> task explicitly authorizes their retirement. If a reviewed retirement set is
> already authorized, freshly reverify its copies and local identities and
> retire only those exact unchanged regular-file leaves, recording receipts.

### Stage and import a bounded manifest

> Use mito-admin-import on the preflighted manifest and source profile. Hash and
> stage only that subset using partial files, checksum verification, atomic rename
> and service-account readability. Recheck incremental capacity before writing.
> Capture existing project state and create and verify a fresh private matching-
> version database backup while preserving referenced payloads. Save a wrapper
> with explicit project, dataset, root, source profile, units, manifest and receipts;
> give it a preview before apply. Use the current official registration services,
> source-path idempotency, conflict checks and one bounded metadata transaction.
> Keep imported model labels as predictions and tasks unassigned unless I requested
> assignment. Do not enqueue all pyramids or allocate every working label in advance.
> Apply only within my authorized scope and save actual IDs and created/replayed counts.

### Validate and hand off

> Use mito-admin-validate for exactly the imported IDs. Check source hashes/receipts,
> keys/dtypes/shapes, calibration units, scientific status, task bounds and assignments.
> Compare protected existing records and the filesystem write set without overwriting
> concurrent human changes. Open a nonempty ROI in the actual read-only viewer and
> verify imagery, predictions, region alignment, axes and loading errors. Capture
> credential-free screenshots. Use an isolated clone/generated-data root for any
> required edit/save/reopen smoke test, never a human annotation. Build only an
> explicitly budgeted pilot derivative through the official validator. Save evidence,
> actual capacity, IDs/links, reserved rows and remaining prerequisites. Do not post
> messages or start the next batch without the corresponding task instruction.

### Diagnose and resume

> Diagnose the failed authorized stage with the relevant Mito admin skill. Read
> the last command/status, manifest, checksums, partial files, receipts and registered
> source identities. Separate access, capacity, checksum, calibration/header, conflict
> and viewer failures. Resume the same run rather than making another full copy.
> Preserve sources and human work; do not clear production, overwrite assignments,
> invent spacing or silently expand the batch. If an external prerequisite is absent,
> name it and retain completed evidence.

## Read-only capacity helper

```bash
python3 skills/mito-admin-preflight/scripts/manifest_capacity.py \
  --manifest selected.csv --filesystem /path/on/intended/storage \
  --reserve-gib 20 --committed-gib 0
```

The CSV schema is `name`, `shape_zyx`, `triplet_bytes`,
`working_label_uint16_bytes`, and optional empty `validation_errors`.
For this schema the working-byte budget must equal `product(shape) * 2`.
The helper rejects duplicates and invalid budgets. It reads filesystem space
and the CSV only: no SSH, Django, payload reads, credential use or mutation.
Adapt the explicit working-label calculation for another dtype/layout.
The report does not reserve space or prove access/source integrity.

Source-only capacity can be misleading. In the worked P10 case, the operator's
55 source triplets used 65.70 GiB but their potential working labels required
136.28 GiB before snapshots/pyramids. A five-volume exercise used 4.78 GiB of
sources plus 10 GiB of labels; the full reserved 56 needed 66.54 GiB of sources
plus 138 GiB of labels. Recheck current space and concurrent commitments.

## Validation and scope

```bash
python3 scripts/skills/test_skills.py
python3 scripts/docs/check_links.py
git diff --check
```

The isolated tests cover preview/no-write behavior, verified installation,
idempotent replay, preserving edited or symlinked installed skills, capacity
including labels/reserve/commitments, and invalid/duplicate manifests. They do
not import live data or prove cross-tool interactive discovery. The P10 case's
actual source, viewer and isolated edit tests are historical evidence, not a
new validation of every future deployment.

These packages add instructions and read-only/installation tooling. They do not
change authentication, feature flags, application reset, annotation state or
service registration behavior. The repository contains no production manifests,
DB dumps, research volume payloads, credentials or host-bound mutation script.
