import { useEffect, useRef, useState } from "react";
import {
  createPublicShare,
  getEntityShare,
  revokePublicShare,
  type EntityShareState,
  type PublicShare,
} from "../api/shares";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../auth/AuthContext";
import { withViewLocation, type ViewLocation } from "../features/viewer/viewLocation";
import { showError } from "../errorPopup";

export default function ShareControl({scope, projectId, datasetId, volumeId, getViewLocation}: {
  scope: PublicShare["scope"];
  projectId: number;
  datasetId?: number;
  volumeId?: number;
  /** Viewer surfaces provide a getter so every Copy link samples the latest position. */
  getViewLocation?: () => ViewLocation | null;
}) {
  const {user, isManager} = useAuth();
  const params = {scope, project_id: projectId, dataset_id: datasetId, volume_id: volumeId};
  const remote = useAsync(() => getEntityShare(params), [scope, projectId, datasetId, volumeId]);
  const [local, setLocal] = useState<EntityShareState | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedReset = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (remote.data) setLocal(remote.data);
  }, [remote.data]);

  useEffect(() => () => {
    if (copiedReset.current) clearTimeout(copiedReset.current);
  }, []);

  const current = local ?? remote.data;
  const share = async () => {
    setBusy(true);
    try {
      const row = await createPublicShare(params);
      setLocal({
        scope,
        active: true,
        aggregate_state: scope === "volume" ? "shared" : "all",
        shares: [row],
      });
      const url = withViewLocation(row.url, getViewLocation?.());
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Share succeeded; Copy link is available — no chrome tip.
      }
    } catch (e) {
      showError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await Promise.all((current?.shares ?? []).map(row => revokePublicShare(row.id)));
      // Patch immediately. Parent aggregate state is then refreshed inside this
      // tiny control only; volume metadata/tasks and the containing page never reload.
      setLocal({scope, active: false, aggregate_state: scope === "volume" ? "not_shared" : "none", shares: []});
      // A volume has no descendant scopes, so the local off state is final.
      // Parent scopes may still be partially shared through children; refresh
      // only this compact control to learn that aggregate LED, never its page.
      if (scope !== "volume") setLocal(await getEntityShare(params));
    } catch (e) {
      showError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const active = current?.active === true;
  const liveShare = current?.shares?.[0];
  const canStop = Boolean(
    liveShare &&
    (isManager || (scope === "volume" && liveShare.created_by === user?.id)),
  );
  const ledState = current?.aggregate_state ?? "not_shared";
  const ledLabel = ledState === "all" ? "All shared" : ledState === "partial" ? "Partially shared" : ledState === "shared" ? "Sharing on" : "Sharing off";
  return <span className="share-control row">
    <span className={`share-led share-led-${ledState}`} title={ledLabel} aria-label={ledLabel}/>
    {!active && (
      <button type="button" className="secondary" disabled={busy || (!local && remote.loading)} onClick={share}>Share</button>
    )}
    {active && liveShare && (
      <button type="button" className="secondary share-copy-button" onClick={async () => {
        try {
          await navigator.clipboard.writeText(withViewLocation(liveShare.url, getViewLocation?.()));
          setCopied(true);
          if (copiedReset.current) clearTimeout(copiedReset.current);
          copiedReset.current = setTimeout(() => {
            setCopied(false);
            copiedReset.current = null;
          }, 2000);
        } catch {
          if (copiedReset.current) {
            clearTimeout(copiedReset.current);
            copiedReset.current = null;
          }
          setCopied(false);
          showError("Could not copy link. Try again.");
        }
      }}>{copied ? "Copied" : "Copy link"}</button>
    )}
    {active && canStop && (
      <button type="button" className="secondary" disabled={busy} onClick={stop}>Stop sharing</button>
    )}
  </span>;
}

const SHARE_STATE_TEXT: Record<string, string> = {
  all: "Every dataset and volume in this project has a public link.",
  partial: "Part of this project has a public link.",
  shared: "A public link is live.",
  none: "Nothing in this project is publicly shared.",
  not_shared: "Nothing here is publicly shared.",
};

/** The same state `ShareControl` shows, with no controls.
 *
 * Overview says what is currently shared; the buttons that change it live in
 * Settings. Reading it in two places is fine — it is one small GET — but the
 * verb belongs in exactly one. */
export function ShareSummary({
  scope,
  projectId,
  datasetId,
  volumeId,
}: {
  scope: PublicShare["scope"];
  projectId: number;
  datasetId?: number;
  volumeId?: number;
}) {
  const params = { scope, project_id: projectId, dataset_id: datasetId, volume_id: volumeId };
  const share = useAsync(() => getEntityShare(params), [scope, projectId, datasetId, volumeId]);
  if (share.loading || share.error || !share.data) return null;
  const state = share.data.aggregate_state ?? "not_shared";
  return (
    <p className="muted share-summary">
      <span className={`share-led share-led-${state}`} aria-hidden="true" />{" "}
      {SHARE_STATE_TEXT[state] ?? SHARE_STATE_TEXT.not_shared}
    </p>
  );
}
