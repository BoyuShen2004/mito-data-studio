# Sharing and hard cases

## Public sharing

Managers can share a project, dataset, or volume. A volume's current viewer
location is included when the share is created or copied from the viewer.

1. Choose **Share** on the relevant project, dataset, volume, or viewer.
2. The application creates a revocable public link and attempts to copy it.
3. Use **Copy link** again when needed.
4. Choose **Stop sharing** to revoke it.

Project links open a dataset browser, dataset links open a volume table, and
volume links open the viewer directly. Recipients need no account. Every public
share is clearly marked **READ-ONLY · NO ACCOUNT NEEDED** and provides viewing
controls such as Axis and Region only, never annotation or Save.

Parent share indicators can show that all, some, or none of their descendants
are shared. Stopping a parent share does not silently rewrite unrelated data.

## Hard cases

In Annotate, make an existing label Active and choose **Record hard case**.
The case belongs to the project and captures enough view context to return to
that label. Project members can browse it in **Hard Cases**; its creator and
managers can work on it according to current permissions.

Opening a hard case — from **Hard Cases** or from a public hard-case link —
lands on a layer where the recorded label is actually painted, with that label
soloed on the canvas and shown in the 3D view. The recipient sees the label
without hunting for it. Solo is a default, not a lock: **Show all** in the
Labels header clears the solo/hidden filters and brings the other labels back.
(That control only clears *visibility* — it is unrelated to Annotate's **Reset
labels** and Assign's **Reset annotations**, which discard work.)

An open hard case can be taken down/resolved without deleting the underlying
label. A public hard-case link is read-only and can be revoked independently of
the case's project status.
