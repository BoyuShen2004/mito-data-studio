import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../auth/AuthContext";
import Layout from "../components/Layout";
import LoginPage from "../pages/LoginPage";
import RegisterPage from "../pages/RegisterPage";
import HomePage from "../pages/HomePage";
import RegisterDataPage from "../pages/RegisterDataPage";
import ProjectListPage from "../pages/ProjectListPage";
import NewProjectPage from "../pages/NewProjectPage";
import ProjectDetailPage from "../pages/ProjectDetailPage";
import VolumeDetailPage from "../pages/VolumeDetailPage";
import TaskDetailPage from "../pages/TaskDetailPage";
import { TaskViewerPage, VolumeViewerPage } from "../pages/ViewerPage";
import HardCaseSharePage from "../pages/HardCaseSharePage";
import TaskSharePage from "../pages/TaskSharePage";
import PublicSharePage from "../pages/PublicSharePage";
import HardCaseDetailPage from "../pages/HardCaseDetailPage";
import PeoplePage from "../pages/PeoplePage";
import ProfilePage from "../pages/ProfilePage";
import PersonPage from "../pages/PersonPage";
import AdminSettingsPage from "../pages/AdminSettingsPage";
import SubmitTaskPage from "../pages/SubmitTaskPage";
import ReviewSubmissionPage from "../pages/ReviewSubmissionPage";
import { effectiveRole, homePathForRole, homeLabelForRole } from "./roles";
import type { HomeRole } from "./roles";

export { effectiveRole, homePathForRole, homeLabelForRole };
export type { HomeRole };

// `roles` restricts a route to specific roles; omit to allow any authenticated user.
// `fullBleed` keeps the global navbar but skips the centered max-width container
// so viewer/editor pages can fill the remaining window under the navbar.
function RequireAuth({
  children,
  roles,
  fullBleed = false,
}: {
  children: ReactNode;
  roles?: HomeRole[];
  fullBleed?: boolean;
}) {
  const { user, loading } = useAuth();
  if (loading) return <div className="center">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(effectiveRole(user.role))) {
    return <Navigate to={homePathForRole(user.role)} replace />;
  }
  return <Layout fullBleed={fullBleed}>{children}</Layout>;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/admin/settings" element={<RequireAuth><AdminSettingsPage /></RequireAuth>} />

      {/* Public read-only "hard case" share — no auth, no app navbar. See
          progress/history/02-share-hard-case.md. */}
      <Route path="/share/hard-case/:token" element={<HardCaseSharePage />} />
      <Route path="/share/task/:token" element={<TaskSharePage />} />
      <Route path="/share/public/:token" element={<PublicSharePage />} />
      {/* One personal home for every role — the role decides the tabs, not
          the URL. See pages/HomePage.tsx. */}
      <Route path="/" element={<RequireAuth><HomePage /></RequireAuth>} />

      {/* The project list is scoped server-side for every role (managers see
          all, requesters their own, annotators the ones they work on). */}
      <Route
        path="/projects"
        element={
          <RequireAuth>
            <ProjectListPage />
          </RequireAuth>
        }
      />

      {/* Register data — shared by requesters and managers */}
      <Route
        path="/register-data"
        element={
          <RequireAuth roles={["manager", "requester"]}>
            <RegisterDataPage />
          </RequireAuth>
        }
      />

      {/* Step 1 of new work: create the project, then register data into it.
          Requesters own projects too, so both roles may create one. */}
      <Route
        path="/projects/new"
        element={
          <RequireAuth roles={["manager", "requester"]}>
            <NewProjectPage />
          </RequireAuth>
        }
      />

      {/* Shared project + volume detail; APIs restrict each role to visible work. */}
      <Route
        path="/projects/:id"
        element={
          <RequireAuth roles={["manager", "requester", "annotator"]}>
            <ProjectDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/volumes/:id"
        element={
          <RequireAuth roles={["manager", "requester", "annotator"]}>
            <VolumeDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/submissions/:id/review"
        element={
          <RequireAuth roles={["manager"]}>
            <ReviewSubmissionPage />
          </RequireAuth>
        }
      />

      <Route
        path="/tasks/:id/submit"
        element={
          <RequireAuth>
            <SubmitTaskPage />
          </RequireAuth>
        }
      />

      {/* Shared */}
      <Route
        path="/tasks/:id"
        element={
          <RequireAuth>
            <TaskDetailPage />
          </RequireAuth>
        }
      />

      {/* Your own account. Every role can open it; the shortcuts section
          inside is the part that only annotators and managers see. */}
      <Route
        path="/profile"
        element={
          <RequireAuth>
            <ProfilePage />
          </RequireAuth>
        }
      />

      {/* People — one section, role-specific panels inside the page. Every
          authenticated role gets it; the backend scopes what's in it. */}
      <Route
        path="/people"
        element={
          <RequireAuth>
            <PeoplePage />
          </RequireAuth>
        }
      />
      <Route
        path="/people/:username"
        element={
          <RequireAuth>
            <PersonPage />
          </RequireAuth>
        }
      />

      {/* One hard case — this app's issue page. Visibility is project
          membership, decided server-side, so no route-level role gate belongs
          here. It is an ordinary page now, not fullBleed: the canvas is a
          block inside it and the discussion is the rest. The cross-project
          inbox is gone — Home surfaces the cases that concern you and a
          project's Cases tab holds its own. */}
      <Route
        path="/hard-cases/:id"
        element={
          <RequireAuth>
            <HardCaseDetailPage />
          </RequireAuth>
        }
      />

      {/* Visualization — any role that can view. Editing entry is gated in the
          page (managers + assigned annotator); requesters only reach /viewer.
          fullBleed keeps the global navbar but lets the canvas fill the rest. */}
      <Route
        path="/viewer/volumes/:id"
        element={
          <RequireAuth fullBleed>
            <VolumeViewerPage />
          </RequireAuth>
        }
      />
      <Route
        path="/viewer/tasks/:id"
        element={
          <RequireAuth fullBleed>
            <TaskViewerPage />
          </RequireAuth>
        }
      />
      <Route
        path="/editor/tasks/:id"
        element={
          <RequireAuth fullBleed roles={["manager", "annotator"]}>
            <TaskViewerPage editable />
          </RequireAuth>
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
