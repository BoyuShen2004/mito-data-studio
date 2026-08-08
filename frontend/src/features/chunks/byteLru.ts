export interface SizedValue {
  readonly byteLength: number;
}

export interface ByteLruMetrics {
  hit(): void;
  miss(): void;
  eviction(bytes: number): void;
}

const NOOP_METRICS: ByteLruMetrics = {
  hit() {},
  miss() {},
  eviction() {},
};

/** Deterministic LRU bounded by retained bytes, not merely entry count. */
export class ByteLru<K, V extends SizedValue> {
  private readonly entries = new Map<K, V>();
  private retainedBytes = 0;

  constructor(
    readonly maxBytes: number,
    private readonly metrics: ByteLruMetrics = NOOP_METRICS,
  ) {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
      throw new RangeError("maxBytes must be a non-negative safe integer");
    }
  }

  get size(): number {
    return this.entries.size;
  }

  get bytes(): number {
    return this.retainedBytes;
  }

  get(key: K): V | undefined {
    const value = this.entries.get(key);
    if (value === undefined) {
      this.metrics.miss();
      return undefined;
    }
    this.entries.delete(key);
    this.entries.set(key, value);
    this.metrics.hit();
    return value;
  }

  peek(key: K): V | undefined {
    return this.entries.get(key);
  }

  set(key: K, value: V): void {
    if (!Number.isSafeInteger(value.byteLength) || value.byteLength < 0) {
      throw new RangeError("value.byteLength must be a non-negative safe integer");
    }
    const previous = this.entries.get(key);
    if (previous !== undefined) {
      this.retainedBytes -= previous.byteLength;
      this.entries.delete(key);
    }
    if (value.byteLength > this.maxBytes) {
      return;
    }
    this.entries.set(key, value);
    this.retainedBytes += value.byteLength;
    this.evictToBudget();
  }

  delete(key: K): boolean {
    const value = this.entries.get(key);
    if (value === undefined) return false;
    this.entries.delete(key);
    this.retainedBytes -= value.byteLength;
    return true;
  }

  deleteWhere(predicate: (key: K, value: V) => boolean): number {
    let removed = 0;
    for (const [key, value] of this.entries) {
      if (predicate(key, value) && this.delete(key)) removed += 1;
    }
    return removed;
  }

  clear(): void {
    this.entries.clear();
    this.retainedBytes = 0;
  }

  keys(): K[] {
    return [...this.entries.keys()];
  }

  private evictToBudget(): void {
    while (this.retainedBytes > this.maxBytes) {
      const oldest = this.entries.entries().next().value as [K, V] | undefined;
      if (!oldest) break;
      this.entries.delete(oldest[0]);
      this.retainedBytes -= oldest[1].byteLength;
      this.metrics.eviction(oldest[1].byteLength);
    }
  }
}
