import { api } from "./client";

/**
 * Deployment identity, as reported by the running instance.
 *
 * Used by the SPA to confirm which upgrade profile and feature flags the
 * running process actually loaded (and by project dashboards for fingerprint).
 */
export interface DeploymentIdentity {
  fingerprint: string;
  checkout: string;
  data_root: string;
  database: { engine: string; name: string; host: string | null; port: string | null };
  service: { bind: string | null; release: string | null; declared: boolean };
  git: { branch: string | null; commit: string | null };
  upgrade_profile: "legacy" | "webknossos" | "production_integrated_v1";
  features: Record<string, boolean>;
}

/** Cached for the session: identity cannot change without a restart. */
let cached: Promise<DeploymentIdentity | null> | null = null;

export function getDeploymentIdentity(): Promise<DeploymentIdentity | null> {
  if (!cached) {
    cached = api
      .get<DeploymentIdentity>("/deployment/identity/")
      .catch(() => null);
  }
  return cached;
}

/** Safe signed-out subset used by the login footer. */
export function getDeploymentRelease(): Promise<string | null> {
  return api
    .get<{release: string | null}>("/deployment/release/")
    .then(response => response.release)
    .catch(() => null);
}

/** Test seam — drops the cached identity. */
export function resetDeploymentIdentityCache(): void {
  cached = null;
}
