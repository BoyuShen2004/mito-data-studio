import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { roleLabel } from "../labels";
import { backFallbackFor } from "../routes/backNavigation";
import { homePathForRole } from "../routes/roles";
import BackButton from "./BackButton";

/**
 * Global top bar for every authenticated page (including View/Annotate).
 *
 * Navigation ownership (keep this the single place — don't re-add Done/Home
 * duplicates on page topbars):
 * - Brand is display-only (not a link)
 * - Role home (My Tasks / Dashboard / …) → dedicated control, not the brand
 * - ← Back → previous page when possible, else hierarchical parent
 */
export default function Navbar() {
  const { user, isManager, isRequester, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const homePath = homePathForRole(user?.role);
  const fallback = backFallbackFor(pathname, user?.role);

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <nav className="navbar">
      <span className="brand">🧬 Mito Data Studio</span>
      {isManager ? (
        <>
          <NavLink to="/manager" className="nav-link" end title="Cross-project overview">
            Dashboard
          </NavLink>
          <NavLink to="/projects" className="nav-link" title="Create and open project containers">
            Projects
          </NavLink>
          <NavLink to="/register-data" className="nav-link" title="Ingest data, metadata, and label Type">
            Register Data
          </NavLink>
        </>
      ) : isRequester ? (
        <>
          <NavLink to="/requester" className="nav-link" end>
            My Projects
          </NavLink>
          <NavLink to="/register-data" className="nav-link">
            Register Data
          </NavLink>
        </>
      ) : (
        <button
          type="button"
          className="secondary"
          onClick={() => navigate(homePath)}
          title="Go to My Tasks"
        >
          My Tasks
        </button>
      )}
      {/* The collaboration half of the app: who you work with, and what the
          team has flagged. Same two entries for every role — the pages scope
          themselves server-side, so there is nothing role-specific here. */}
      <NavLink to="/people" className="nav-link" title="Access, teams, and assignment eligibility">
        People
      </NavLink>
      <NavLink to="/hard-cases" className="nav-link" title="Difficult-label inbox">
        Hard Cases
      </NavLink>
      <span className="spacer" />
      {fallback && <BackButton fallback={fallback} />}
      {/* The identity readout is the way into your own account — the profile
          (and the annotate shortcuts on it) had no entry point at all before,
          and "click who you are" is where people look for one. */}
      <NavLink to="/profile" className="nav-link navbar-identity" title="Your profile and annotate shortcuts">
        {user?.username} ({roleLabel(user?.role)})
      </NavLink>
      <button type="button" className="secondary" onClick={onLogout}>
        Log out
      </button>
    </nav>
  );
}
