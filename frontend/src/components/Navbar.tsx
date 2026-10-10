import { useState } from "react";
import { switchWorkspace } from "../api/auth";
import { showError } from "../errorPopup";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { roleLabel } from "../labels";
import { backFallbackFor } from "../routes/backNavigation";
import BackButton from "./BackButton";

/**
 * Global top bar for every authenticated page (including View/Annotate).
 *
 * Navigation ownership (keep this the single place — don't re-add Done/Home
 * duplicates on page topbars):
 * - Brand is display-only (not a link)
 * - Home / Projects / People are the only places; nothing here is a verb
 * - ← Back → previous page when possible, else hierarchical parent
 */
export default function Navbar() {
  const { user, logout } = useAuth();
  const [switching, setSwitching] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const fallback = backFallbackFor(pathname, user?.role);

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <nav className="navbar">
      <span className="brand">Mito Data Studio</span>
      {/* Four entries, and every one of them is a place. `Register Data` is an
          action, so it lives on the pages that own it (Home, the Projects
          list, and a project's Data tab). Hard Cases is not here either: a
          case belongs to a project, and Home surfaces the ones that concern
          you. */}
      <NavLink to="/" className="nav-link" end title="What is waiting on you">
        Home
      </NavLink>
      <NavLink to="/projects" className="nav-link" title="Every project you can see">
        Projects
      </NavLink>
      <NavLink to="/people" className="nav-link" title="Access, teams, and assignment eligibility">
        People
      </NavLink>
      <span className="spacer" />
      {fallback && <BackButton fallback={fallback} />}
      {/* The identity readout is the way into your own account — the profile
          (and the annotate shortcuts on it) had no entry point at all before,
          and "click who you are" is where people look for one. */}
      <details className="account-dropdown">
        <summary className="nav-link navbar-identity">{user?.username} ({roleLabel(user?.role)})</summary>
        <div className="account-dropdown-panel">
          <NavLink to="/profile" onClick={event => event.currentTarget.closest("details")?.removeAttribute("open")}>Your profile</NavLink>
          {user?.is_assistant_manager && user.available_roles?.map(role => (
            <button type="button" key={role} disabled={switching || role === user.role}
              aria-pressed={role === user.role} onClick={async () => {
                if (!window.confirm(`Switch to ${roleLabel(role)}? Save pending changes first. Unsaved changes will be discarded.`)) return;
                setSwitching(true);
                try {
                  await switchWorkspace(role);
                  // Fresh role-scoped data: never retain manager lists in Annotator mode.
                  window.location.assign("/");
                } catch (error) { showError(error instanceof Error ? error.message : "Could not switch role."); setSwitching(false); }
              }}>Use {roleLabel(role)} workspace</button>
          ))}
        </div>
      </details>
      <button type="button" className="secondary" onClick={onLogout}>
        Log out
      </button>
    </nav>
  );
}
