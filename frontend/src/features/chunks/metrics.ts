import type { ByteLruMetrics } from "./byteLru";
import type { PullQueueMetrics } from "./pullQueue";

export interface Phase13MetricsSnapshot {
  hits: number;
  misses: number;
  deduplicated: number;
  cancelled: number;
  stale: number;
  retries: number;
  tokenRefreshes: number;
  evictions: number;
  evictedBytes: number;
  queueWaitMs: number[];
  networkMs: number[];
  decodeMs: number[];
}

export class Phase13Metrics {
  private data: Phase13MetricsSnapshot = {
    hits: 0,
    misses: 0,
    deduplicated: 0,
    cancelled: 0,
    stale: 0,
    retries: 0,
    tokenRefreshes: 0,
    evictions: 0,
    evictedBytes: 0,
    queueWaitMs: [],
    networkMs: [],
    decodeMs: [],
  };

  readonly cache: ByteLruMetrics = {
    hit: () => {
      this.data.hits += 1;
    },
    miss: () => {
      this.data.misses += 1;
    },
    eviction: (bytes) => {
      this.data.evictions += 1;
      this.data.evictedBytes += bytes;
    },
  };

  readonly queue: PullQueueMetrics = {
    event: (name, detail) => {
      if (name === "deduplicated") this.data.deduplicated += 1;
      if (name === "cancelled") this.data.cancelled += 1;
      if (name === "stale") this.data.stale += 1;
      if (name === "retry") this.data.retries += 1;
      if (name === "started" && detail.waitMs !== undefined) {
        this.data.queueWaitMs.push(detail.waitMs);
      }
    },
  };

  tokenRefresh(): void {
    this.data.tokenRefreshes += 1;
  }

  chunkTiming(networkMs: number, decodeMs: number): void {
    this.data.networkMs.push(networkMs);
    this.data.decodeMs.push(decodeMs);
  }

  snapshot(): Phase13MetricsSnapshot {
    return {
      ...this.data,
      queueWaitMs: [...this.data.queueWaitMs],
      networkMs: [...this.data.networkMs],
      decodeMs: [...this.data.decodeMs],
    };
  }
}
