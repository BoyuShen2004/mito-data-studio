import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { homePathForRole } from "../routes/AppRoutes";
import BackButton from "../components/BackButton";
import { showError } from "../errorPopup";

type RegRole = "annotator" | "requester";

export default function RegisterPage() {
  const { register, user } = useAuth();
  const navigate = useNavigate();
  const [role, setRole] = useState<RegRole>("requester");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [institution, setInstitution] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) {
    navigate(homePathForRole(user.role), { replace: true });
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const u = await register({
        username,
        password,
        email,
        role,
        institution_name: institution,
      });
      navigate(homePathForRole(u.role), { replace: true });
    } catch (err) {
      showError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <main className="login-form-panel">
        <header className="login-brand">
          <h1>Mito Data Studio</h1>
          <p>3-D microscopy annotation and review</p>
        </header>
        <div className="login-card">
          <BackButton fallback="/login" />
          <h2>Sign up</h2>
          <p className="subtitle">Choose the type of account to create</p>

          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={role === "requester"}
              className={`tab ${role === "requester" ? "tab-active" : ""}`}
              onClick={() => setRole("requester")}
            >
              Register as Requester
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={role === "annotator"}
              className={`tab ${role === "annotator" ? "tab-active" : ""}`}
              onClick={() => setRole("annotator")}
            >
              Register as Annotator
            </button>
          </div>

          <form onSubmit={onSubmit}>
            <label className="field">
              <span>Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label className="field">
              <span>Email (optional)</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
              />
            </label>
            {role === "requester" && (
              <label className="field">
                <span>Institution / lab (optional)</span>
                <input
                  value={institution}
                  onChange={(e) => setInstitution(e.target.value)}
                />
              </label>
            )}
            <button type="submit" className="btn-block" disabled={busy}>
              {busy ? "Creating account…" : "Create account"}
            </button>
          </form>

          <div className="login-hint">
            Already have an account? <Link to="/login">Sign in</Link>
          </div>
        </div>
      </main>
    </div>
  );
}
