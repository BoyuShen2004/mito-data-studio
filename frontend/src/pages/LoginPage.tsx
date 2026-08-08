import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { homePathForRole } from "../routes/AppRoutes";
import type { LoginPortal } from "../api/auth";
import {
  clearDevelopmentData,
  fetchMockAccounts,
  getDevelopmentResetStatus,
  type MockAccount,
} from "../api/auth";
import { getDeploymentRelease } from "../api/deployment";

function releaseLabel(release: string): string {
  return release.match(/v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)/)?.[1] ?? release;
}

export default function LoginPage() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const [portal, setPortal] = useState<LoginPortal>("requester");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const [mockAccounts, setMockAccounts] = useState<MockAccount[]>([]);
  const [release, setRelease] = useState<string | null>(null);
  useEffect(() => { fetchMockAccounts().then(setMockAccounts); }, []);
  useEffect(() => { getDeploymentRelease().then(setRelease); }, []);

  // Product invariant: account chips only fill the ordinary login form. They
  // must never authenticate or navigate by themselves; the user explicitly
  // presses Sign in. See docs/product-invariants.md.
  const selectDevelopmentAccount = (account: MockAccount) => {
    setPortal(account.role === "requester" ? "requester" : "annotator");
    setUsername(account.username);
    setPassword(account.password);
    setError(null);
  };

  const resetDevelopmentData = async () => {
    setResetError(null);
    setResetMessage(null);
    setError(null);
    setBusy(true);
    try {
      // GET both proves that this deployment has explicitly enabled the
      // disposable dev reset and places the CSRF cookie used by the POST.
      const status = await getDevelopmentResetStatus();
      const total = Object.entries(status.clear)
        .filter(([key]) => !key.startsWith("users"))
        .reduce((sum, [, count]) => sum + count, 0);
      const accepted = window.confirm(
        `Clear all development data and files?\n\n` +
        `This will permanently remove ${total} application records. ` +
        `Development account identities will be kept.`,
      );
      if (!accepted) return;
      await clearDevelopmentData(status.confirmation);
      setResetMessage("All development data and files were cleared.");
    } catch (err) {
      setResetError(err instanceof Error ? err.message : "Development reset failed");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!user) return;
    navigate(homePathForRole(user.role), { replace: true });
  }, [navigate, user]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password, portal);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      {release && <span className="login-release" aria-label="Release version">{releaseLabel(release)}</span>}
      <aside className="login-brand">
        <div className="login-vspacer" aria-hidden="true" />
        <div className="login-brand-inner">
          <div className="brand-mark">🧬 Mito Data Agent</div>
          <div className="brand-hero">
            <h1>
              EM annotation,
              <br />
              from volume to approval.
            </h1>
            <p>
              Register EM volumes, assign each one to an annotator, and annotate
              in the browser. Submit the latest work for review, collaborate on
              hard cases, and track people and progress across each project.
            </p>
          </div>
          <ul className="brand-features">
            <li>
              <span className="tick">✓</span> View &amp; Annotate with paint,
              EfficientSAM &amp; SAM2
            </li>
            <li>
              <span className="tick">✓</span> Submit/review: latest wins;
              approve to lock or continue
            </li>
            <li>
              <span className="tick">✓</span> Project Hard Cases, People &amp;
              live progress
            </li>
          </ul>
        </div>
        <div className="login-vspacer" aria-hidden="true" />
      </aside>

      <main className="login-form-panel">
        <div className="login-vspacer" aria-hidden="true" />
        <div className="login-card">
          <div className="login-mobile-brand">🧬 Mito Data Agent</div>
          <h2>Welcome back</h2>
          <p className="subtitle">Sign in to your workspace</p>

          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={portal === "requester"}
              className={`tab ${portal === "requester" ? "tab-active" : ""}`}
              onClick={() => setPortal("requester")}
            >
              Requester Login
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={portal === "annotator"}
              className={`tab ${portal === "annotator" ? "tab-active" : ""}`}
              onClick={() => setPortal("annotator")}
            >
              Annotator Login
            </button>
          </div>

          {portal === "annotator" && (
            <p className="subtitle">
              Managers sign in here using their manager account.
            </p>
          )}

          {error && <div className="error">{error}</div>}

          <form className="login-form" onSubmit={onSubmit}>
            <label className="field">
              <span>Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button type="submit" className="btn-block" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="login-hint">
            Need an account? <Link to="/register">Create one</Link> as an
            annotator or a requester.
          </div>

          {mockAccounts.length > 0 && (
            <div className="dev-accounts">
              <div className="dev-accounts-title">Development accounts</div>
              <div className="dev-accounts-list">
                {mockAccounts.map((account) => (
                  <button
                    type="button"
                    key={account.username}
                    className="dev-chip"
                    onClick={() => selectDevelopmentAccount(account)}
                    disabled={busy}
                  >
                    {account.username}
                    <span className="dev-chip-role">{account.role}</span>
                  </button>
                ))}
              </div>
              <div className="dev-accounts-note">
                Select an account to fill the form, then press Sign in yourself.
                Clearing application data retains these reusable identities.
              </div>
              <div className="dev-reset">
                {resetError && <div className="error">{resetError}</div>}
                {resetMessage && <div className="success">{resetMessage}</div>}
                <button
                  type="button"
                  className="dev-reset-btn"
                  onClick={resetDevelopmentData}
                  disabled={busy}
                >
                  {busy ? "Clearing…" : "Clear all existing files"}
                </button>
                <div className="dev-accounts-note">
                  One confirmation clears disposable application data and files;
                  Development account identities are retained.
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="login-vspacer login-vspacer--after-card" />
      </main>
    </div>
  );
}
