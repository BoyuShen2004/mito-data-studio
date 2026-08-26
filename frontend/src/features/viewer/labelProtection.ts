/** Restore every changed pixel that belongs to, or would grow, a protected id. */
export function protectLabelIds(
  before: Int32Array,
  after: Int32Array,
  protectedIds: ReadonlySet<number>,
): number {
  if (before.length !== after.length) {
    throw new Error("Label protection requires equal-sized planes");
  }
  let restored = 0;
  for (let offset = 0; offset < before.length; offset++) {
    if (before[offset] === after[offset]) continue;
    if (!protectedIds.has(before[offset]) && !protectedIds.has(after[offset])) continue;
    after[offset] = before[offset];
    restored += 1;
  }
  return restored;
}

/**
 * Apply a server tool plan as a delta onto the browser's current plane.
 *
 * Absolute plan planes are unsafe here: the browser may have newer pending
 * pixels, and running the generic growth guard on an absolute plane can turn
 * an unchanged verified server neighbour into zero when the client plane was
 * incomplete. Unchanged verified server pixels are therefore refreshed from
 * the authoritative plan baseline, while only actual server delta pixels are
 * considered tool edits.
 */
export function applyProtectedLabelPlanDelta(
  clientBefore: Int32Array,
  serverBefore: Int32Array,
  serverAfter: Int32Array,
  protectedIds: ReadonlySet<number>,
): { after: Int32Array; blocked: number } {
  if (
    clientBefore.length !== serverBefore.length ||
    serverBefore.length !== serverAfter.length
  ) {
    throw new Error("Label plan delta requires equal-sized planes");
  }
  const after = clientBefore.slice();
  let blocked = 0;
  for (let offset = 0; offset < after.length; offset += 1) {
    if (serverBefore[offset] === serverAfter[offset]) {
      if (protectedIds.has(serverBefore[offset])) after[offset] = serverBefore[offset];
      continue;
    }
    if (
      protectedIds.has(clientBefore[offset]) ||
      protectedIds.has(serverBefore[offset]) ||
      protectedIds.has(serverAfter[offset])
    ) {
      blocked += 1;
      continue;
    }
    after[offset] = serverAfter[offset];
  }
  return { after, blocked };
}
