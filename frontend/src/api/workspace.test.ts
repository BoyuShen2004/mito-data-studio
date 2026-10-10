import { beforeEach, expect, it, vi } from "vitest";
import { authenticatedFetch, api, setToken, setWorkspaceRole } from "./client";
import { fetchMe, switchWorkspace } from "./auth";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); });
it("sends the workspace with decoded and raw authenticated requests", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{}'));
  setToken('test'); setWorkspaceRole('manager');
  await api.get('/auth/me/');
  expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ Authorization: 'Token test', 'X-Mito-Role': 'manager' });
  await authenticatedFetch('/api/volumes/');
  expect(new Headers(fetch.mock.calls[1][1]?.headers).get('X-Mito-Role')).toBe('manager');
});
it("does not change workspace when the server refuses a switch", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"detail":"Denied"}', { status: 403 }));
  setWorkspaceRole('annotator');
  await expect(switchWorkspace('manager')).rejects.toThrow('Denied');
  expect(sessionStorage.getItem('mito_workspace_role')).toBe('annotator');
});
it("recovers a revoked manager workspace without dropping authentication", async () => {
  const fetch = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response('{"detail":"Revoked"}', { status: 403 }))
    .mockResolvedValueOnce(new Response('{"role":"annotator"}'));
  setToken('test'); setWorkspaceRole('manager');
  expect((await fetchMe()).role).toBe('annotator');
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(localStorage.getItem('mito_token')).toBe('test');
  expect(sessionStorage.getItem('mito_workspace_role')).toBeNull();
});
