// Thin fetch wrapper. Auth is token-based: the token is stored in
// localStorage and sent as `Authorization: Token <token>`.

const TOKEN_KEY = "mito_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** Raw fetch with the same token contract as the decoded API client. */
export function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Token ${token}`);
  }
  return fetch(input, { ...init, headers });
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  // When true, `body` is sent as-is (FormData) without JSON headers.
  isForm?: boolean;
  // Lets a caller cancel an in-flight request (e.g. a superseded AI predict
  // — see AnnotationCanvas.tsx) — the browser drops the response, this
  // function still rejects with an AbortError the caller can filter out.
  signal?: AbortSignal;
  responseType?: "json" | "blob" | "arrayBuffer";
}

function extractMessage(data: unknown, fallback: string): string {
  if (typeof data === "string" && data.trim()) {
    const trimmed = data.trim();
    if (/^(?:<!doctype\s+html|<html|<head|<body)/i.test(trimmed)) return fallback;
    return trimmed.length <= 500 ? trimmed : fallback;
  }
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (typeof obj.detail === "string") return obj.detail;
    // Surface the first field error DRF returns.
    const first = Object.values(obj)[0];
    if (Array.isArray(first) && typeof first[0] === "string") return first[0];
    if (typeof first === "string") return first;
  }
  return fallback;
}

export async function apiRequest<T>(
  path: string,
  {
    method = "GET",
    body,
    isForm = false,
    signal,
    responseType = "json",
  }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Token ${token}`;

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    if (isForm) {
      payload = body as FormData;
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }

  const apiPath = path.startsWith("/api/") ? path : `/api${path}`;
  const res = await fetch(apiPath, { method, headers, body: payload, signal });

  if (res.status === 204) return undefined as T;

  if (res.ok && responseType === "blob") return (await res.blob()) as T;
  if (res.ok && responseType === "arrayBuffer") {
    return (await res.arrayBuffer()) as T;
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const fallback = res.status >= 500
      ? `Server error (${res.status})`
      : (res.statusText || `Request failed (${res.status})`);
    throw new ApiError(res.status, extractMessage(data, fallback), data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal }),
  blob: (path: string, signal?: AbortSignal) =>
    apiRequest<Blob>(path, { signal, responseType: "blob" }),
  arrayBuffer: (path: string, signal?: AbortSignal) =>
    apiRequest<ArrayBuffer>(path, { signal, responseType: "arrayBuffer" }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<T>(path, { method: "POST", body, signal }),
  postArrayBuffer: (path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<ArrayBuffer>(path, {
      method: "POST",
      body,
      signal,
      responseType: "arrayBuffer",
    }),
  postForm: <T>(path: string, body: FormData) =>
    apiRequest<T>(path, { method: "POST", body, isForm: true }),
  put: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, { method: "PATCH", body }),
  patchForm: <T>(path: string, body: FormData) =>
    apiRequest<T>(path, { method: "PATCH", body, isForm: true }),
  del: <T>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
};
