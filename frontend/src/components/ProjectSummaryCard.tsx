import type { ProjectProgress } from "../types/project";

export default function ProjectSummaryCard({
  progress,
}: {
  progress: ProjectProgress;
}) {
  return (
    <div className="summary-strip" aria-label="Project progress">
      <div className="summary-metric"><strong>{progress.volumes}</strong><span>Volumes</span></div>
      <div className="summary-metric"><strong>{progress.total_tasks}</strong><span>Total tasks</span></div>
      <div className="summary-metric"><strong>{progress.approved_tasks}</strong><span>Approved</span></div>
      <div className="summary-metric"><strong>{progress.percent_complete}%</strong><span>Complete</span></div>
    </div>
  );
}
