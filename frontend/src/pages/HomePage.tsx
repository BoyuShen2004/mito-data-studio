import { useEffect, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { listHardCases } from "../api/hardCases";
import { listProjects } from "../api/projects";
import { listReviewLabelComments } from "../api/reviewLabelComments";
import { listSubmissions } from "../api/submissions";
import { listMyCompletedTasks, listMyTasks } from "../api/tasks";
import { useAsync } from "../hooks/useAsync";

import SectionTabs, { type SectionTab } from "../components/SectionTabs";
import PublicShareTree from "../components/PublicShareTree";
import ReviewLabelCommentList from "../components/ReviewLabelCommentList";
import WorkList, { useWorkFilter } from "../components/WorkList";
import type { AnnotationTask } from "../types/task";
import type { HardCase } from "../types/hardCase";
import type { Project } from "../types/project";
import { showError } from "../errorPopup";

/**
 * One personal home for every role.
 *
 * This replaces `ManagerDashboard` and `AnnotatorDashboard`, and it is not a
 * new screen: each tab is one of the calls those two already made, rendered by
 * the same `WorkList` the project tabs use. What used to be four unrelated
 * manager panels plus an "attention rail" that linked back to two of them is
 * now a tab strip whose counts *are* the attention signal.
 *
 * Nothing here fetches per tab — the role's queues are small and are needed for
 * the counts anyway, so switching tabs is instant and costs no request.
 */

type Tab = {
  id: string;
  label: string;
  count?: number;
  render: () => ReactNode;
};

export default function HomePage() {
  const { isManager, isRequester } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filter, setFilter] = useWorkFilter();

  const worksAQueue = !isRequester;

  const myTasks = useAsync<AnnotationTask[]>(
    () => (worksAQueue ? listMyTasks() : Promise.resolve([])),
    [worksAQueue],
  );
  const completed = useAsync<AnnotationTask[]>(
    () => (worksAQueue ? listMyCompletedTasks() : Promise.resolve([])),
    [worksAQueue],
  );
  const waiting = useAsync(
    () => (isManager ? listSubmissions("submitted") : Promise.resolve([])),
    [isManager],
  );
  const projects = useAsync<Project[]>(
    () => (isManager || isRequester ? listProjects() : Promise.resolve([])),
    [isManager, isRequester],
  );
  const cases = useAsync<HardCase[]>(
    () => (worksAQueue ? listHardCases() : Promise.resolve([])),
    [worksAQueue],
  );
  const feedback = useAsync(
    () => (worksAQueue && !isManager ? listReviewLabelComments() : Promise.resolve([])),
    [worksAQueue, isManager],
  );

  // Every one of these is a narrowing of a list already in memory.
  const needsRevision = (myTasks.data ?? []).filter(
    (task) => task.status === "revision_requested" || task.status === "rejected",
  );
  const toApprove = (projects.data ?? []).filter((project) => !project.manager_reviewed);
  const awaitingReview = (waiting.data ?? []).map((submission) => submission.task_detail);
  const openCases = (cases.data ?? []).filter((row) => row.status === "open");

  const taskList = (
    rows: AnnotationTask[],
    loading: boolean,
    label: string,
    emptyText: ReactNode,
  ) => (
    <WorkList
      kind="task"
      rows={rows}
      loading={loading}
      filter={filter}
      onFilterChange={setFilter}
      label={label}
      emptyText={emptyText}
    />
  );

  const projectList = (rows: Project[], label: string, emptyText: ReactNode) => (
    <WorkList
      kind="project"
      rows={rows}
      loading={projects.loading}
      filter={filter}
      onFilterChange={setFilter}
      label={label}
      emptyText={emptyText}
    />
  );

  const caseList = () => (
    <WorkList
      kind="case"
      rows={cases.data ?? []}
      loading={cases.loading}
      filter={filter}
      onFilterChange={setFilter}
      onChanged={cases.reload}
      label="Hard cases in your projects"
      emptyText={<>Nothing flagged in your projects. Cases are raised from Annotate with “Record hard case”.</>}
    />
  );

  // A tab is a label, a count, and a preloaded row set. Every row set above is
  // a narrowing of a list already in memory, so switching costs no request.
  const queue = (
    id: string,
    label: string,
    rows: AnnotationTask[],
    loading: boolean,
    empty: ReactNode,
  ): Tab => ({ id, label, count: rows.length, render: () => taskList(rows, loading, label, empty) });

  const casesTab = (label: string): Tab => ({
    id: "cases",
    label,
    count: openCases.length,
    render: caseList,
  });

  const managerTabs: Tab[] = [
    queue("review", "Awaiting review", awaitingReview, waiting.loading,
      <>Nothing is waiting on you. Submissions appear here the moment an annotator hands one in.</>),
    {
      id: "approve",
      label: "Projects to approve",
      count: toApprove.length,
      render: () => projectList(toApprove, "Projects awaiting your approval",
        <>Nothing is waiting for approval. A project lands here once its data is registered.</>),
    },
    queue("mine", "Assigned to me", myTasks.data ?? [], myTasks.loading,
      <>Nothing is assigned to you.</>),
    casesTab("Open cases"),
    { id: "shares", label: "Shares", render: () => <PublicShareTree /> },
  ];

  const annotatorTabs: Tab[] = [
    queue("mine", "Assigned to me", myTasks.data ?? [], myTasks.loading,
      <>Nothing is assigned to you yet. A manager pushes work here one volume at a time.</>),
    queue("revision", "Needs revision", needsRevision, myTasks.loading,
      <>Nothing has been handed back. A rejected or revised task appears here with the reviewer’s comment.</>),
    queue("done", "Done", completed.data ?? [], completed.loading,
      <>Nothing handed in yet.</>),
    {
      id: "feedback",
      label: "Feedback",
      count: feedback.data?.length,
      render: () =>
        feedback.error ? (
          <div className="error">{feedback.error}</div>
        ) : (
          <ReviewLabelCommentList
            comments={feedback.data ?? []}
            showProject
            emptyText="No manager label feedback yet."
          />
        ),
    },
    casesTab("Cases in my projects"),
  ];

  const requesterTabs: Tab[] = [
    {
      id: "projects",
      label: "My projects",
      count: projects.data?.length,
      render: () => projectList(projects.data ?? [], "Your projects",
        <>No projects yet. <Link to="/projects/new">Create one</Link>, then register data into it.</>),
    },
  ];

  const tabs = isManager ? managerTabs : isRequester ? requesterTabs : annotatorTabs;
  const requested = searchParams.get("tab");
  const active = tabs.find((tab) => tab.id === requested) ?? tabs[0];
  // Switching tabs drops the previous list's filters: a stale `?assignee=`
  // from one queue would silently empty the next.
  const selectTab = (id: string) => setSearchParams({ tab: id });

  const firstError = [myTasks.error, completed.error, waiting.error, projects.error, cases.error]
    .find(Boolean);
  useEffect(() => {
    if (firstError) showError(firstError);
  }, [firstError]);

  return (
    <div className="role-home">
      <header className="page-header row spread">
        <div>
          <h1>Home</h1>
          <p className="muted">
            {isManager
              ? "What is waiting on you, across every project."
              : isRequester
                ? "The projects you registered, and where each one stands."
                : "Your work, and what the team has flagged around it."}
          </p>
        </div>
        {(isManager || isRequester) && (
          <div className="row page-actions">
            <Link to="/register-data"><button type="button" className="secondary">Register data</button></Link>
            <Link to="/projects/new"><button type="button">+ New project</button></Link>
          </div>
        )}
      </header>

      <SectionTabs
        tabs={tabs.map(({ id, label, count }) => ({ id, label, count })) as SectionTab<string>[]}
        active={active.id}
        onChange={selectTab}
        label="Your queues"
      />

      <section className="workspace-panel" role="tabpanel">
        {active.render()}
      </section>

      <p className="muted home-hint">
        Filters live in the address bar — narrow a list, then send the link.
      </p>
    </div>
  );
}
