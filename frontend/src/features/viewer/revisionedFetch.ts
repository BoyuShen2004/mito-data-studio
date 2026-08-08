/**
 * Prevents an async read that started before a write from being accepted
 * after that write invalidates its result.
 *
 * A plain `cache.delete(key)` is insufficient: an old request can finish
 * later and put its stale response straight back into the cache. `loadLatest`
 * notices that an invalidation happened while awaiting and repeats the read.
 */
export class RevisionedFetch {
  private revision = 0;

  invalidate() {
    this.revision += 1;
  }

  async loadLatest<T>(load: () => Promise<T>): Promise<T> {
    for (;;) {
      const startedAt = this.revision;
      const value = await load();
      if (startedAt === this.revision) return value;
    }
  }
}
