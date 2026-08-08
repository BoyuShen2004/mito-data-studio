import { describe, expect, it } from "vitest";
import { grayscaleRgba, validateWindow } from "./intensity";
import { ChunkClientError } from "../chunks";
import {
  chunkFallbackMessage,
  phase14ChunkRendererEnabled,
} from "./chunkRenderedImageSource";

describe("stable intensity conversion", () => {
  it("maps and clamps uint16 against a volume-wide window", () => {
    const out = grayscaleRgba(new Uint16Array([0, 50, 100, 200]), { lo: 0, hi: 100 });
    expect([...out]).toEqual([
      0, 0, 0, 255,
      128, 128, 128, 255,
      255, 255, 255, 255,
      255, 255, 255, 255,
    ]);
  });

  it("handles signed values, NaN and infinities explicitly", () => {
    const out = grayscaleRgba(
      new Float32Array([-Infinity, -1, 0, 1, Infinity, NaN]),
      { lo: -1, hi: 1 },
    );
    expect([out[0], out[4], out[8], out[12], out[16], out[20]])
      .toEqual([0, 0, 128, 255, 255, 0]);
  });

  it("rejects unstable or non-finite windows", () => {
    expect(() => validateWindow({ lo: 1, hi: 1 })).toThrow(RangeError);
    expect(() => validateWindow({ lo: 2, hi: 1 })).toThrow(RangeError);
    expect(() => validateWindow({ lo: 0, hi: Infinity })).toThrow(RangeError);
  });

  it("keeps the production chunk renderer disabled by default", () => {
    expect(phase14ChunkRendererEnabled({ VITE_FEATURE_CHUNK_RENDERER: undefined })).toBe(false);
    expect(phase14ChunkRendererEnabled({ VITE_FEATURE_CHUNK_RENDERER: "false" })).toBe(false);
    expect(
      phase14ChunkRendererEnabled({
        VITE_FEATURE_CHUNK_RENDERER: "true",
        VITE_FEATURE_CHUNK_PULL_QUEUE: "true",
      }),
    ).toBe(true);
  });

  it("will not mount the renderer over a disabled transport", () => {
    // renderer ⇒ PullQueue, the mirror of the backend's chunk ⇒ pyramids check.
    expect(
      phase14ChunkRendererEnabled({
        VITE_FEATURE_CHUNK_RENDERER: "true",
        VITE_FEATURE_CHUNK_PULL_QUEUE: "false",
      }),
    ).toBe(false);
    expect(
      phase14ChunkRendererEnabled({
        VITE_FEATURE_CHUNK_RENDERER: "true",
        VITE_FEATURE_CHUNK_PULL_QUEUE: undefined,
        VITE_MITO_UPGRADE_PROFILE: "legacy",
      }),
    ).toBe(false);
  });

  it("enables the renderer through the coherent upgrade profile unless overridden", () => {
    expect(phase14ChunkRendererEnabled({
      VITE_FEATURE_CHUNK_RENDERER: undefined,
      VITE_MITO_UPGRADE_PROFILE: "webknossos",
    })).toBe(true);
    expect(phase14ChunkRendererEnabled({
      VITE_FEATURE_CHUNK_RENDERER: undefined,
      VITE_MITO_UPGRADE_PROFILE: "production_integrated_v1",
    })).toBe(true);
    expect(phase14ChunkRendererEnabled({
      VITE_FEATURE_CHUNK_RENDERER: "false",
      VITE_MITO_UPGRADE_PROFILE: "webknossos",
    })).toBe(false);
  });

  it("distinguishes permission, missing, corrupt and temporary fallback", () => {
    expect(chunkFallbackMessage(new ChunkClientError("x", "unauthorized"))).toMatch(/permitted/);
    expect(chunkFallbackMessage(new ChunkClientError("x", "missing"))).toMatch(/pyramid/);
    expect(chunkFallbackMessage(new ChunkClientError("x", "malformed"))).toMatch(/corrupt/);
    expect(chunkFallbackMessage(new ChunkClientError("x", "network"))).toMatch(/temporarily/);
  });
});
