/**
 * Test environment setup for the editor suite.
 *
 * jsdom does not provide a usable 2-D canvas context (`getContext` returns
 * null), which the overlay renderer treats as "no canvas". A minimal stub is
 * enough for mount/smoke tests that never assert pixels on screen.
 *
 * `fake-indexeddb` remains installed for any residual browser-storage helpers
 * used by other modules under test.
 */

import "fake-indexeddb/auto";

// --- working Web Storage ----------------------------------------------------
// This environment exposes `window.localStorage`/`sessionStorage` as bare
// objects with no `getItem`, so anything that reads them — the API client's
// auth token, the brush-cursor preference — throws instead of returning null.
// Production code guards for that, but a test asserting that a preference
// *persists* needs storage that actually stores. Replaced only when broken, so
// a future jsdom that provides the real thing keeps it.
class MemoryStorage implements Storage {
  private entries = new Map<string, string>();
  get length() {
    return this.entries.size;
  }
  clear() {
    this.entries.clear();
  }
  getItem(key: string) {
    return this.entries.has(key) ? (this.entries.get(key) as string) : null;
  }
  key(index: number) {
    return [...this.entries.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.entries.delete(key);
  }
  setItem(key: string, value: string) {
    this.entries.set(key, String(value));
  }
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  const existing = globalThis.window?.[name] as Storage | undefined;
  if (typeof existing?.getItem === "function") continue;
  Object.defineProperty(globalThis.window, name, {
    configurable: true,
    writable: true,
    value: new MemoryStorage(),
  });
}

// --- minimal 2-D context so canvas-bearing components can mount -------------
const stubContext = () =>
  ({
    canvas: null,
    clearRect: () => {},
    fillRect: () => {},
    strokeRect: () => {},
    beginPath: () => {},
    closePath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    arc: () => {},
    fill: () => {},
    stroke: () => {},
    save: () => {},
    restore: () => {},
    translate: () => {},
    scale: () => {},
    setTransform: () => {},
    drawImage: () => {},
    putImageData: () => {},
    getImageData: (_x: number, _y: number, w: number, h: number) => ({
      data: new Uint8ClampedArray(Math.max(0, w * h * 4)),
      width: w,
      height: h,
    }),
    createImageData: (w: number, h: number) => ({
      data: new Uint8ClampedArray(Math.max(0, w * h * 4)),
      width: w,
      height: h,
    }),
    measureText: () => ({ width: 0 }),
    fillText: () => {},
    set fillStyle(_v: unknown) {},
    set strokeStyle(_v: unknown) {},
    set lineWidth(_v: unknown) {},
    set globalAlpha(_v: unknown) {},
    set imageSmoothingEnabled(_v: unknown) {},
  }) as unknown as CanvasRenderingContext2D;

if (typeof HTMLCanvasElement !== "undefined") {
  // Cast through `unknown`: getContext is heavily overloaded (2d / webgl /
  // bitmaprenderer) and the stub only satisfies the 2-D signature, which is the
  // only one the editor asks for.
  HTMLCanvasElement.prototype.getContext = (function getContext() {
    return stubContext();
  } as unknown) as typeof HTMLCanvasElement.prototype.getContext;
}

// --- BroadcastChannel fallback ---------------------------------------------
if (typeof globalThis.BroadcastChannel === "undefined") {
  const channels = new Map<string, Set<(e: MessageEvent) => void>>();
  class LocalBroadcastChannel {
    onmessage: ((e: MessageEvent) => void) | null = null;
    private listeners = new Set<(e: MessageEvent) => void>();
    constructor(readonly name: string) {
      if (!channels.has(name)) channels.set(name, new Set());
      channels.get(name)!.add((e) => {
        this.onmessage?.(e);
        this.listeners.forEach((l) => l(e));
      });
    }
    postMessage(data: unknown) {
      for (const [name, subs] of channels) {
        if (name !== this.name) continue;
        subs.forEach((s) => s({ data } as MessageEvent));
      }
    }
    addEventListener(_t: string, l: (e: MessageEvent) => void) {
      this.listeners.add(l);
    }
    removeEventListener(_t: string, l: (e: MessageEvent) => void) {
      this.listeners.delete(l);
    }
    close() {
      this.listeners.clear();
    }
  }
  (globalThis as { BroadcastChannel?: unknown }).BroadcastChannel =
    LocalBroadcastChannel;
}
