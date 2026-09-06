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
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const fallback = backFallbackFor(pathname, user?.role);

  const onLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <nav className="navbar">
      <span className="brand">🧬 Mito Data Studio</span>
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
      <NavLink to="/profile" className="nav-link navbar-identity" title="Your profile and annotate shortcuts">
        {user?.username} ({roleLabel(user?.role)})
      </NavLink>
      <button type="button" className="secondary" onClick={onLogout}>
        Log out
      </button>
    </nav>
  );
}
