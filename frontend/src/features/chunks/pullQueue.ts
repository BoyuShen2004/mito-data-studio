export enum PullPriority {
  CURRENT = 0,
  REFINE = 1,
  NEAR = 2,
  PREFETCH = 3,
}

export class PullQueueError extends Error {}
export class PullCancelledError extends PullQueueError {}
export class StaleGenerationError extends PullQueueError {}
export class PullQueueDisposedError extends PullQueueError {}
export class PullQueueCapacityError extends PullQueueError {}

export interface PullQueueMetrics {
  event(
    name:
      | "enqueued"
      | "started"
      | "deduplicated"
      | "cancelled"
      | "stale"
      | "retry"
      | "completed",
    detail: { key: string; waitMs?: number; attempt?: number },
  ): void;
}

const NOOP_METRICS: PullQueueMetrics = { event() {} };

export interface PullRequest<T> {
  key: string;
  volumeScope: string;
  viewport: string;
  generation: number;
  priority: PullPriority;
  run(signal: AbortSignal): Promise<T>;
  shouldRetry?(error: unknown): boolean;
}

export interface PullHandle<T> {
  readonly key: string;
  readonly promise: Promise<T>;
  cancel(): void;
  reprioritize(priority: PullPriority): void;
}

export interface PullQueueOptions {
  maxActive?: number;
  maxActivePerVolume?: number;
  maxPending?: number;
  maxRetries?: number;
  backoffMs?: (attempt: number) => number;
  metrics?: PullQueueMetrics;
  now?: () => number;
}

interface Consumer<T> {
  id: number;
  viewport: string;
  generation: number;
  priority: PullPriority;
  resolve(value: T): void;
  reject(error: unknown): void;
  settled: boolean;
}

interface Work<T> {
  key: string;
  volumeScope: string;
  run(signal: AbortSignal): Promise<T>;
  shouldRetry(error: unknown): boolean;
  sequence: number;
  enqueuedAt: number;
  consumers: Map<number, Consumer<T>>;
  controller: AbortController | null;
  state: "pending" | "active";
}

function positiveInt(value: number, name: string): number {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new RangeError(`${name} must be a positive safe integer`);
  }
  return value;
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new PullCancelledError("request cancelled"));
      },
      { once: true },
    );
  });
}

/** Framework-independent, bounded scheduler. One instance is one viewer scope. */
export class PullQueue {
  private readonly maxActive: number;
  private readonly maxActivePerVolume: number;
  private readonly maxPending: number;
  private readonly maxRetries: number;
  private readonly backoffMs: (attempt: number) => number;
  private readonly metrics: PullQueueMetrics;
  private readonly now: () => number;
  private readonly work = new Map<string, Work<unknown>>();
  private readonly activeByVolume = new Map<string, number>();
  private readonly generations = new Map<string, number>();
  private active = 0;
  private sequence = 0;
  private consumerSequence = 0;
  private disposed = false;

  constructor(options: PullQueueOptions = {}) {
    this.maxActive = positiveInt(options.maxActive ?? 6, "maxActive");
    this.maxActivePerVolume = positiveInt(
      options.maxActivePerVolume ?? 4,
      "maxActivePerVolume",
    );
    this.maxPending = positiveInt(options.maxPending ?? 512, "maxPending");
    this.maxRetries = Math.max(0, options.maxRetries ?? 2);
    this.backoffMs = options.backoffMs ?? ((attempt) => 50 * 2 ** (attempt - 1));
    this.metrics = options.metrics ?? NOOP_METRICS;
    this.now = options.now ?? (() => performance.now());
  }

  enqueue<T>(request: PullRequest<T>): PullHandle<T> {
    if (this.disposed) throw new PullQueueDisposedError("queue is disposed");
    this.assertRequest(request);
    const existing = this.work.get(request.key) as Work<T> | undefined;
    if (!existing && this.pendingCount >= this.maxPending) {
      throw new PullQueueCapacityError("pending request limit reached");
    }

    let resolvePromise!: (value: T) => void;
    let rejectPromise!: (error: unknown) => void;
    const promise = new Promise<T>((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    const consumer: Consumer<T> = {
      id: ++this.consumerSequence,
      viewport: request.viewport,
      generation: request.generation,
      priority: request.priority,
      resolve: resolvePromise,
      reject: rejectPromise,
      settled: false,
    };

    let item: Work<T>;
    if (existing) {
      item = existing;
      item.consumers.set(consumer.id, consumer);
      this.metrics.event("deduplicated", { key: request.key });
    } else {
      item = {
        key: request.key,
        volumeScope: request.volumeScope,
        run: request.run,
        shouldRetry: request.shouldRetry ?? (() => false),
        sequence: ++this.sequence,
        enqueuedAt: this.now(),
        consumers: new Map([[consumer.id, consumer]]),
        controller: null,
        state: "pending",
      };
      this.work.set(request.key, item as Work<unknown>);
      this.metrics.event("enqueued", { key: request.key });
    }

    this.pump();
    return {
      key: request.key,
      promise,
      cancel: () => this.cancelConsumer(item, consumer.id),
      reprioritize: (priority) => {
        consumer.priority = priority;
        this.pump();
      },
    };
  }

  /** Record the current viewport generation and optionally cancel older consumers. */
  setGeneration(viewport: string, generation: number, cancelOlder = true): void {
    if (!Number.isSafeInteger(generation) || generation < 0) {
      throw new RangeError("generation must be a non-negative safe integer");
    }
    const previous = this.generations.get(viewport);
    if (previous !== undefined && generation < previous) {
      throw new RangeError(
        `generation cannot move backwards (${generation} < ${previous})`,
      );
    }
    this.generations.set(viewport, generation);
    if (cancelOlder) {
      for (const item of this.work.values()) {
        for (const consumer of [...item.consumers.values()]) {
          if (consumer.viewport === viewport && consumer.generation < generation) {
            this.cancelConsumer(item, consumer.id);
          }
        }
      }
    }
    this.pump();
  }

  cancelGeneration(viewport: string, generation: number): void {
    for (const item of this.work.values()) {
      for (const consumer of [...item.consumers.values()]) {
        if (consumer.viewport === viewport && consumer.generation === generation) {
          this.cancelConsumer(item, consumer.id);
        }
      }
    }
  }

  cancel(key: string): void {
    const item = this.work.get(key);
    if (!item) return;
    for (const consumer of [...item.consumers.values()]) {
      this.cancelConsumer(item, consumer.id);
    }
  }

  reprioritize(key: string, priority: PullPriority): void {
    const item = this.work.get(key);
    if (!item) return;
    for (const consumer of item.consumers.values()) consumer.priority = priority;
    this.pump();
  }

  get pendingCount(): number {
    let count = 0;
    for (const item of this.work.values()) if (item.state === "pending") count += 1;
    return count;
  }

  inspectQueue(): ReadonlyArray<{
    key: string;
    priority: PullPriority;
    sequence: number;
    consumers: number;
  }> {
    return [...this.work.values()]
      .filter((item) => item.state === "pending")
      .sort((a, b) => this.compare(a, b))
      .map((item) => ({
        key: item.key,
        priority: this.itemPriority(item),
        sequence: item.sequence,
        consumers: item.consumers.size,
      }));
  }

  inspectInFlight(): ReadonlyArray<{ key: string; volumeScope: string; consumers: number }> {
    return [...this.work.values()]
      .filter((item) => item.state === "active")
      .map((item) => ({
        key: item.key,
        volumeScope: item.volumeScope,
        consumers: item.consumers.size,
      }));
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const item of [...this.work.values()]) {
      item.controller?.abort();
      for (const consumer of item.consumers.values()) {
        this.rejectConsumer(consumer, new PullQueueDisposedError("queue is disposed"));
      }
      item.consumers.clear();
    }
    this.work.clear();
    this.activeByVolume.clear();
  }

  private assertRequest<T>(request: PullRequest<T>): void {
    if (!request.key || !request.volumeScope || !request.viewport) {
      throw new TypeError("key, volumeScope and viewport are required");
    }
    if (!Number.isSafeInteger(request.generation) || request.generation < 0) {
      throw new RangeError("generation must be a non-negative safe integer");
    }
  }

  private itemPriority(item: Work<unknown>): PullPriority {
    let priority = PullPriority.PREFETCH;
    for (const consumer of item.consumers.values()) {
      if (consumer.priority < priority) priority = consumer.priority;
    }
    return priority;
  }

  private compare(a: Work<unknown>, b: Work<unknown>): number {
    return this.itemPriority(a) - this.itemPriority(b) || a.sequence - b.sequence;
  }

  private cancelConsumer<T>(item: Work<T>, id: number): void {
    const consumer = item.consumers.get(id);
    if (!consumer) return;
    item.consumers.delete(id);
    this.rejectConsumer(consumer, new PullCancelledError("request cancelled"));
    this.metrics.event("cancelled", { key: item.key });
    if (item.consumers.size === 0) {
      if (item.state === "active") {
        item.controller?.abort();
        // A new consumer for the same immutable key must create fresh work,
        // not attach to an AbortController that is already doomed.
        if (this.work.get(item.key) === item) this.work.delete(item.key);
      } else {
        this.work.delete(item.key);
      }
    }
    this.pump();
  }

  private rejectConsumer<T>(consumer: Consumer<T>, error: unknown): void {
    if (consumer.settled) return;
    consumer.settled = true;
    consumer.reject(error);
  }

  private pump(): void {
    if (this.disposed) return;
    while (this.active < this.maxActive) {
      const candidate = [...this.work.values()]
        .filter(
          (item) =>
            item.state === "pending" &&
            item.consumers.size > 0 &&
            (this.activeByVolume.get(item.volumeScope) ?? 0) <
              this.maxActivePerVolume,
        )
        .sort((a, b) => this.compare(a, b))[0];
      if (!candidate) break;
      this.start(candidate);
    }
  }

  private start(item: Work<unknown>): void {
    item.state = "active";
    item.controller = new AbortController();
    this.active += 1;
    this.activeByVolume.set(
      item.volumeScope,
      (this.activeByVolume.get(item.volumeScope) ?? 0) + 1,
    );
    this.metrics.event("started", {
      key: item.key,
      waitMs: this.now() - item.enqueuedAt,
    });
    void this.execute(item, item.controller.signal)
      .then((value) => this.resolveItem(item, value))
      .catch((error) => this.rejectItem(item, error))
      .finally(() => {
        // Cancellation can remove this item and allow a fresh one with the
        // same key before the aborted promise settles.
        if (this.work.get(item.key) === item) this.work.delete(item.key);
        this.active -= 1;
        const remaining = (this.activeByVolume.get(item.volumeScope) ?? 1) - 1;
        if (remaining <= 0) this.activeByVolume.delete(item.volumeScope);
        else this.activeByVolume.set(item.volumeScope, remaining);
        this.pump();
      });
  }

  private async execute(item: Work<unknown>, signal: AbortSignal): Promise<unknown> {
    let attempt = 0;
    for (;;) {
      try {
        return await item.run(signal);
      } catch (error) {
        if (
          signal.aborted ||
          attempt >= this.maxRetries ||
          !item.shouldRetry(error) ||
          item.consumers.size === 0
        ) {
          throw error;
        }
        attempt += 1;
        this.metrics.event("retry", { key: item.key, attempt });
        await delay(this.backoffMs(attempt), signal);
      }
    }
  }

  private resolveItem(item: Work<unknown>, value: unknown): void {
    for (const consumer of item.consumers.values()) {
      const current = this.generations.get(consumer.viewport);
      if (current !== undefined && current !== consumer.generation) {
        this.metrics.event("stale", { key: item.key });
        this.rejectConsumer(
          consumer,
          new StaleGenerationError(
            `generation ${consumer.generation} is stale; current is ${current}`,
          ),
        );
      } else if (!consumer.settled) {
        consumer.settled = true;
        consumer.resolve(value);
      }
    }
    this.metrics.event("completed", { key: item.key });
  }

  private rejectItem(item: Work<unknown>, error: unknown): void {
    const reported =
      item.controller?.signal.aborted && !(error instanceof PullQueueDisposedError)
        ? new PullCancelledError("request cancelled")
        : error;
    for (const consumer of item.consumers.values()) {
      this.rejectConsumer(consumer, reported);
    }
  }
}
