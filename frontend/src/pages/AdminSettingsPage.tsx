import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { executeReset, getResetStatus, requestResetConfirmation, type ResetStatus } from "../api/adminReset";

export default function AdminSettingsPage() {
  const { user } = useAuth();
  const [status, setStatus] = useState<ResetStatus | null>(null);
  const [password, setPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (user?.is_superuser) getResetStatus().then(setStatus).catch((e) => setMessage(e.message)); }, [user]);
  if (!user?.is_superuser) return <Navigate to="/" replace />;

  const clear = async () => {
    if (!status || phrase !== status.phrase) return;
    setBusy(true); setMessage("");
    try {
      const issued = await requestResetConfirmation(password, phrase);
      const result = await executeReset(issued.confirmation_token, phrase);
      setMessage(`Reset completed. Manifest: ${JSON.stringify(result)}`);
      setPassword(""); setPhrase("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Reset failed");
    } finally { setBusy(false); }
  };

  return <section className="admin-reset-page">
    <h1>Administrator settings</h1>
    {!status ? <p>Loading protected reset status…</p> : <>
      <div className="warning-panel">
        <h2>Clear all existing files</h2>
        <p>This clears application business data and app-owned generated files. External source/reference files are unregistered but never deleted.</p>
        <p><strong>Deployment:</strong> {status.identity.service.release} / {status.identity.fingerprint}</p>
        <p><strong>Database:</strong> {status.identity.database.name}</p>
        <p><strong>Data root:</strong> {status.identity.data_root}</p>
        <h3>Will clear</h3>
        <pre>{JSON.stringify(status.clear, null, 2)}</pre>
        <h3>Will retain</h3><ul>{status.retain.map((item) => <li key={item}>{item}</li>)}</ul>
        {!status.maintenance && <p className="error">Maintenance/write-freeze mode is not active.</p>}
        {!status.backup.valid && <p className="error">Backup gate: {status.backup.reason}</p>}
        <label className="field"><span>Current administrator password</span><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        <label className="field"><span>Type {status.phrase}</span><input value={phrase} onChange={(e) => setPhrase(e.target.value)} /></label>
        <button className="danger-button" disabled={busy || !password || phrase !== status.phrase || !status.maintenance || !status.backup.valid} onClick={clear}>
          {busy ? "Clearing…" : "Clear all existing files"}
        </button>
      </div>
    </>}
    {message && <div role="status" className={message.startsWith("Reset completed") ? "success" : "error"}>{message}</div>}
  </section>;
}
