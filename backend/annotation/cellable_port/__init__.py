"""Segmentation and label-state tools behind the Annotate editor.

Provenance: several modules were ported from the Cellable desktop annotator;
each keeps a one-line note naming the function it came from, pending the
provenance and redistribution audit in
``documentation/release/RELEASE_CHECKLIST.md``.

- ``ai/`` — prompt handling, image normalisation, and the SAM 2 prompted-mask
  adapter (``ai/sam2_masks.py``).
- ``watershed.py`` — 3D marker-based watershed for the Seeds tool.
- ``split_components.py`` — 3D connected-component split (scipy
  26-connectivity).
- ``merge_labels.py`` — merge two labels into the smaller id.
- ``label_state.py`` — per-label Proposed / Edited / Verified lifecycle.
- ``labels_3d.py`` — per-label summary and 3D surface meshes for the 3D
  Labels panel.

Point / Box / Boundary are single-slice tools; multi-slice propagation is SAM 2
Track (``annotation/tracking/``).
"""
