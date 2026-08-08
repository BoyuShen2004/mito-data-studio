import type { CurrentUser } from "../types";
import { api, authenticatedFetch, setToken } from "./client";

interface AuthResponse {
  token: string;
  user: CurrentUser;
}

export interface MockAccount {
  username: string;
  role: string | null;
  password: string;
}

export async function fetchMockAccounts(): Promise<MockAccount[]> {
  try {
    const result = await api.get<{ enabled: boolean; accounts: MockAccount[] }>("/auth/mock-login/");
    return result.enabled ? result.accounts : [];
  } catch {
    return [];
  }
}

export interface DevelopmentResetStatus {
  enabled: boolean;
  confirmation: string;
  clear: Record<string, number>;
}

function csrfToken(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("csrftoken="));
  return item ? decodeURIComponent(item.slice("csrftoken=".length)) : "";
}

export const getDevelopmentResetStatus = () =>
  api.get<DevelopmentResetStatus>("/auth/development-reset/");

export async function clearDevelopmentData(confirmation: string): Promise<Record<string, unknown>> {
  const response = await authenticatedFetch("/api/auth/development-reset/", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify({ confirmation }),
  });
  const text = await response.text();
  let data: Record<string, unknown> = {};
  try {
    data = text ? JSON.parse(text) as Record<string, unknown> : {};
  } catch {
    // Never expose a proxy/server HTML response inside the login UI.
  }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    throw new Error(detail || `Could not clear development data (${response.status})`);
  }
  return data;
}

export type LoginPortal = "requester" | "annotator";

export async function login(
  username: string,
  password: string,
  portal?: LoginPortal,
): Promise<CurrentUser> {
  const res = await api.post<AuthResponse>("/auth/login/", {
    username,
    password,
    ...(portal ? { portal } : {}),
  });
  setToken(res.token);
  return res.user;
}

export interface RegisterInput {
  username: string;
  password: string;
  email?: string;
  role: "annotator" | "requester";
  institution_name?: string;
}

export async function register(data: RegisterInput): Promise<CurrentUser> {
  const res = await api.post<AuthResponse>("/auth/register/", data);
  setToken(res.token);
  return res.user;
}

export async function logout(): Promise<void> {
  try {
    await api.post("/auth/logout/");
  } finally {
    setToken(null);
  }
}

export function fetchMe(): Promise<CurrentUser> {
  return api.get<CurrentUser>("/auth/me/");
}
