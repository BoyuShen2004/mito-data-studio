# ADR-009-A1 — Addendum: the region mask as a second streamed layer

**Status:** accepted, 2026-08-04
**Amends:** [ADR-009](ADR-009-volume-storage-and-pyramids.md) §3 (layout),
[ADR-010](ADR-010-chunk-service-and-authorization.md) §2/§4 (address, tokens)
**Scope:** the read-only region mask (ROI). **Editable labels remain out of
scope** — see §6.

---

## 1. What is being decided

Raw images stream through a Zarr v3 pyramid and the chunk service; the region
mask did not, so every z step in View/Annotate still fetched a full-resolution
PNG of the ROI from `/region-mask-slice/`. That is the same problem Phase 11–14
solved for the image, on a layer with the same properties: immutable, derived
from a file the app must never rewrite, and read on every navigation.

**Decision: give the region mask the same derivative and the same transport, as
a distinct *layer*.**

## 2. Storage layout — a sibling group per layer

    <MITO_DATA_ROOT>/<project>/<dataset>/pyramids/<stem>.zarr/          image
    <MITO_DATA_ROOT>/<project>/<dataset>/pyramids/<stem>.region.zarr/   region

| Alternative | Verdict |
|---|---|
| **Sibling group per layer** | **Chosen.** Every derivative built before layers existed keeps its exact path, so nothing needs rewriting or a migration. A layer is built, promoted, validated and rolled back on its own. The serving cache holds one handle per store, which is what a handle *is*. |
| One group, arrays per layer (`image/1`, `region/1`) | Rejected. It re-nests the mag ladder that ADR-009 §3 locked, invalidates every existing group, and makes a region rebuild rewrite a group the image is being served from. |
| Second store root (`region-pyramids/`) | Rejected. ADR-009 §3 keeps a volume's belongings beside the volume; a parallel tree splits them for no gain. |

Group attributes gain `"layer"`, so a derivative on disk says which layer it is
without reference to its filename.

## 3. Reduction — mode, never mean

The ROI is categorical: a voxel is inside or outside. Averaging a binary mask
produces values that are neither, and at mag 2 that turns a boundary into a
gradient the viewer would threshold arbitrarily. Region builds therefore use the
**mode** reduction Phase 11 already implemented for labels (`is_label=True`),
and `build_pyramid` derives that from the layer rather than from a caller's
flag — the wrong reduction here is silent, not loud.

## 4. Addressing, capabilities and tokens

The chunk address gains a `layer`, defaulting to `image`:

    GET /api/volumes/<pk>/chunks/<mag>/<cz>/<cy>/<cx>/?layer=region
    GET /api/chunks/signed/<mag>/<cz>/<cy>/<cx>/?t=…&layer=region
    GET /api/volumes/<pk>/chunks/capabilities/?layer=region

A query parameter rather than a parallel route set: the layer is one more
coordinate of the address, and duplicating five routes would double the surface
that has to stay authorized identically. Responses echo `X-Mito-Layer`, and the
browser client refuses a response whose layer is not the one it asked for — a
mask composited as EM intensity (or the reverse) would look like data.

Capabilities also report `layers`, the layers this volume can serve right now,
so a client mounts the ROI without probing for a 404.

**Tokens.** ADR-010 §4 always described `layers[]`; this implements it. A token
names the layers it may read, the schema version moves to 2, and `authorize`
rejects a layer the token does not carry. Read access itself is unchanged and
still per volume: a region chunk is the same volume's data, so the ACL is
neither wider nor narrower — the claim is containment, exactly like `mags`.

**Handle cache.** The key becomes `(volume id, layer, build identity)`, and
invalidation takes an optional layer. The two stores are independent; a region
rebuild must not make every subsequent image read re-open its store.

## 5. Readiness, jobs and fallback

`Volume` gains `region_ready_streaming` / `region_pyramid_metadata` beside the
image pair. The two are independent by design: a volume may stream its image and
read its ROI from the source file, or the reverse.

Builds reuse `ProcessingJobType.BUILD_PYRAMID` with `config.layer`. A second job
type would have forked the dispatcher allow-list, the metrics and the status UI
for what is one build with a different source and reduction. Registration
enqueues a region build only when the volume has a mask; changing the mask
clears the derivative built from the *previous* one and requeues, because a
stale ROI pyramid is worse than none — it looks correct.

Status is reported per layer, with `absent` distinguished from `not_built`: a
volume with no ROI has nothing to build, and offering a build button for it
would be an invitation to a no-op.

**Fallback is per layer and one-way.** If region chunks are unavailable or fail
mid-session, the ROI returns to `/region-mask-slice/` with a visible notice
while the image keeps streaming. The client renders region planes as the same
flat cyan RGBA the server PNG produces, because the editor uses that image twice
— as the overlay and as the CSS mask implementing ROI-only — so a different edge
would move the ROI boundary depending on which transport served the plane.

## 6. Explicitly not in this addendum

**Editable labels.** A writable layer needs a sparse write store with
read-modify-write semantics, undo, autosave and conflict handling; a static,
promote-after-validate pyramid is the wrong shape for it. The label slice/RLE
and working-copy stack is untouched here, and remains the follow-up.

Also unchanged: the annotation write path, brush coordinates, ROI-only write
guards, public/share fallbacks, and every non-streaming volume.
