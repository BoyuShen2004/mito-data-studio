import type { LabelIdsResponse } from "../../api/viewer";

/**
 * A cached plane stays valid when another plane of the same axis is saved, but
 * its revision does not: the token describes the whole working volume.
 *
 * Strip it before caching so revisiting an unchanged plane cannot roll the
 * save token back to the revision that happened to accompany that old read.
 */
export function labelIdsForCache(response: LabelIdsResponse): LabelIdsResponse {
  const { revision: _staleVolumeRevision, ...plane } = response;
  return plane;
}
