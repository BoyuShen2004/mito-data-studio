/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MITO_UPGRADE_PROFILE?:
    | "legacy"
    | "webknossos"
    | "production_integrated_v1";
  readonly VITE_FEATURE_CHUNK_PULL_QUEUE?: string;
  readonly VITE_FEATURE_CHUNK_RENDERER?: string;
  /** When "true", LoginPage shows click-to-fill seed_dev account chips. */
  readonly VITE_SHOW_DEMO_ACCOUNTS?: string;
}
