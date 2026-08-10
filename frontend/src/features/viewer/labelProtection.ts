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
