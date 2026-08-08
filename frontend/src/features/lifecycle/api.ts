// Lifecycle feature — New / To Proofread / Done views over projects.
//
// This folder owns the frontend lifecycle navigation. The mapping itself lives
// on the backend (`backend/core/lifecycle.py`); here we just fetch counts and
// render tabs.

import { api } from "../../api/client";
import type { Lifecycle } from "../../labels";

export type LifecycleCounts = Record<Lifecycle, number>;

export function getLifecycleCounts(): Promise<LifecycleCounts> {
  return api.get<LifecycleCounts>("/projects/lifecycle-counts/");
}
