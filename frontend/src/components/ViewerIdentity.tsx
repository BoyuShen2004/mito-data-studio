/** Scientific identity stays ahead of task/actions in every authenticated viewer. */
export default function ViewerIdentity({
  volume,
  dataset,
  taskId,
  status,
}: {
  volume: string;
  dataset?: string;
  taskId?: number;
  status?: string;
}) {
  return (
    <div className="viewer-identity">
      <h1 title={volume}>{volume}</h1>
      <div className="editor-topbar-meta" title={dataset || "Dataset not recorded"}>
        <span>{dataset || "Dataset not recorded"}</span>
        {taskId != null && <span>Task #{taskId}</span>}
        {status && <span className="viewer-workflow-state">{status.replace(/_/g, " ")}</span>}
      </div>
    </div>
  );
}
