"""Code ported (copy-adapt, not reinvented) from the local Cellable app.

Cellable lives at ``/projects/weilab/shenb/cellable`` (a sibling PyQt/Labelme
fork, not part of this repo) — see
``progress/history/19-cellable-parity-annotator-brief.md`` for why this
package exists: mito's in-app Annotate editor is meant to have the same
interactive-AI/segmentation *capabilities* as running Cellable locally, and
the brief requires porting Cellable's actual algorithms rather than
re-implementing them from scratch.

Each module below keeps a header comment pointing at the exact Cellable
source file/function it was ported from, with the Qt/desktop-specific parts
(background threads, statusbar messages, undo-stack widgets, on-disk
embedding-directory cache) stripped since this runs inside a stateless
Django request instead of a single long-lived desktop session:

- ``ai/efficient_sam.py`` — ``cellable/labelme/ai/efficient_sam.py``
- ``watershed.py`` — ``cellable/labelme/app.py``'s ``apply_3d_watershed`` /
  ``_label_bbox_3d`` / ``compute_bbox_3d``
- ``split_components.py`` — ``cellable/labelme/app.py``'s ``split_label``
  (3D connected-component split; scipy 26-connectivity instead of cc3d)
- ``merge_labels.py`` — ``cellable/labelme/app.py``'s ``merge_labels``
- ``labels_3d.py`` — new glue (not a direct port): per-label bbox/voxel
  summary and a downsampled 3D preview grid, playing the role Cellable's
  ``VTKSurfaceWidget`` plays locally, adapted for a browser (three.js
  instanced voxels instead of a VTK marching-cubes surface — see that
  module's docstring for why).

Not ported here: ``cellable/labelme/utils/compute_points_from_mask.py``.
That utility exists in Cellable to re-derive a prompt point for the *next*
slice during ``predictNextNSlices`` (multi-slice AI-mask propagation). mito
already has a separate multi-slice propagation path — fork-aware SAM2
tracking (``annotation/tracking/``) — so the interactive Point/Box/Boundary
tools ported here are deliberately single-slice only; porting a point-
re-derivation helper with no call site would be dead code. If per-slice
AI-mask propagation is wanted later, port it then.
"""
