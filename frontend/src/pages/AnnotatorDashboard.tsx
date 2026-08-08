import { useSearchParams } from "react-router-dom";
import { listMyCompletedTasks, listMyTasks } from "../api/tasks";
import { useAsync } from "../hooks/useAsync";
import TaskTable from "../components/TaskTable";
import SectionTabs from "../components/SectionTabs";

type TaskTab = "todo" | "done";

/** Annotator home: manager-assigned work only (one volume ↔ one assignee). */
export default function AnnotatorDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const myTasks = useAsync(listMyTasks, []);
  const completed = useAsync(listMyCompletedTasks, []);
  const active: TaskTab = searchParams.get("tab") === "done" ? "done" : "todo";

  return (
    <div className="role-home">
      <header className="page-header">
        <h1>My Tasks</h1>
        <p className="muted">Open work assigned to you, or revisit submitted and completed tasks.</p>
      </header>

      <SectionTabs
        tabs={[
          { id: "todo", label: "To do", count: myTasks.data?.length ?? 0 },
          { id: "done", label: "Done", count: completed.data?.length ?? 0 },
        ]}
        active={active}
        onChange={(tab) => setSearchParams({ tab })}
        label="My task lists"
      />

      <section className="workspace-panel task-workspace" role="tabpanel">
        {active === "todo" ? (
          <TaskPane
            title="To annotate"
            description="Tasks sent to you by a manager, including revisions returned for more work."
            loading={myTasks.loading}
            tasks={myTasks.data ?? []}
          />
        ) : (
          <TaskPane
            title="Submitted, completed & withdrawn"
            description="Work already handed in or approved, plus assignments cancelled by a manager."
            loading={completed.loading}
            tasks={completed.data ?? []}
          />
        )}
      </section>
    </div>
  );
}

function TaskPane({
  title,
  description,
  loading,
  tasks,
}: {
  title: string;
  description: string;
  loading: boolean;
  tasks: Parameters<typeof TaskTable>[0]["tasks"];
}) {
  return <>
    <div className="section-heading"><h2>{title}</h2><p className="muted">{description}</p></div>
    {loading ? <p className="muted">Loading…</p> : (
      <TaskTable tasks={tasks} showAssignee={false} showProject />
    )}
  </>;
}
