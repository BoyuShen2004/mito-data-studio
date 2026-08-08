# Keyboard shortcuts and tips

Shortcuts are ignored while focus is in an input, textarea, or select.

| Key | Action |
| --- | --- |
| `A` / `D` | Previous / next layer |
| Arrow keys | Pan the canvas |
| `V` | Select |
| `B` | Brush |
| `E` | Erase |
| `R` | Box Erase |
| `M` | Box Mask |
| `P` | Point Mask |
| `O` | Boundary |
| `T` | Seeds |
| `I` | Interpolate, when enabled |
| `L` | Flood fill, when enabled |
| `C` | Split |
| `G` | Merge |
| `Enter` | Commit an AI/Track proposal or run Interpolate in that tool |
| `Escape` | Clear a proposal or close the context menu |
| `Ctrl/Cmd+Z` | Undo |
| `Ctrl/Cmd+Shift+Z` | Redo |
| `S` / `Shift+S` | Solo Active label / show all |
| `F` | Verify Active label |
| `H` | Toggle Hide verified |
| `Delete` | Confirm rejection/removal of the Active label |

The toolbar and right-click menu remain the authoritative way to select a tool
when a feature flag changes which shortcuts are available.

## Your own tool shortcuts

Annotators and managers can rebind the annotate tool keys plus **Verify** and
**Solo** on their
[profile](accounts-and-roles.md#your-profile): open the identity in the top-right
of the navbar, then **Annotate shortcuts**.

- A custom binding is the **modifier plus a letter**: `Cmd` on macOS, `Ctrl` on
  Windows and Linux. The page shows whichever applies to the machine you are on.
- The modified defaults mirror the bare-letter shortcuts: `Ctrl/Cmd+B` is
  Brush, `Ctrl/Cmd+F` is Verify, `Ctrl/Cmd+S` is Solo, and Flood fill is now
  `Ctrl/Cmd+L` so it does not compete with Verify.
- One letter, one tool. The page highlights a clash and refuses to save until it
  is resolved, and the server rejects one as well.
- Leave a box empty for "no shortcut". **Delete** is unbound by default.
- **Reset to defaults** restores the built-in letters (nothing is saved until
  you press Save profile).
- Bindings are stored on your account, not in the browser, so they follow you to
  another machine.

Tool switching plus the active-label Verify and Solo actions are customisable.
Save, Undo, Redo, layer navigation, the other label-lifecycle keys and the
Track-only bindings remain fixed.

Some of these combinations are also browser or OS shortcuts (`Ctrl/Cmd+F`,
`+P`, `+S`). While the editor has focus, the tool binding wins and the browser
action is suppressed. Pick a different letter if you would rather keep the
browser one.

## Practical tips

- Save at deliberate checkpoints; an Unsaved result exists only in this tab.
- Use Undo to validate compound tools before Save. Interpolate, Track, Split,
  Merge, Watershed, and Delete each remain reversible while pending.
- Let a requested plane finish loading before painting it. Obsolete reads are
  cancelled during scrubbing, but painting the wrong visual context is still
  confusing.
- Use Jump to region rather than dragging through a large empty range.
- Keep Region only on for focus and hidden-label protection; switch it off and
  review the Overwrite choice before saving staged outside-region paint.
- Use Empty voxels only when preserving existing instances matters.
- On CPU-only deployments, expect SAM2 Track to be slower; ordinary paint tools
  do not depend on the model runtime.
- Before Submit, press Save and confirm the Unsaved marker is gone.
