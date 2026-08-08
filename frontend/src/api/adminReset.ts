import { api, authenticatedFetch } from "./client";

export interface ResetStatus {
  phrase: string;
  maintenance: boolean;
  backup: { valid: boolean; reason?: string; verified_at?: string };
  identity: { fingerprint: string; checkout: string; data_root: string; database: { name: string }; service: { release: string } };
  clear: Record<string, number>;
  retain: string[];
  storage: Array<{ resolved_path: string; classification: string; action: string }>;
}

function csrfToken(): string {
  const item = document.cookie.split(";").map((v) => v.trim()).find((v) => v.startsWith("csrftoken="));
  return item ? decodeURIComponent(item.slice("csrftoken=".length)) : "";
}

async function protectedPost<T>(path: string, body: unknown): Promise<T> {
  const response = await authenticatedFetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data as T;
}

export const getResetStatus = () => api.get<ResetStatus>("/admin/reset/status/");
export const requestResetConfirmation = (password: string, phrase: string) =>
  protectedPost<{ confirmation_token: string }>("/admin/reset/confirm/", { password, phrase });
export const executeReset = (confirmation_token: string, phrase: string) =>
  protectedPost<Record<string, unknown>>("/admin/reset/execute/", { confirmation_token, phrase });
