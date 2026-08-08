/**
 * Client-side SDF linear-blend interpolation (same idea as backend
 * annotation.interpolation.core). Operates on flat row-major Int32 label
 * planes so unsaved pending edits can be interpolated without a server round
 * trip or shipping full-slice RLE.
 */

const INF = 1e20;

/** Squared Euclidean distance transform of a binary mask (1 = feature). */
function squaredEdt(mask: Uint8Array, h: number, w: number): Float64Array {
  const n = h * w;
  const f = new Float64Array(n);
  for (let i = 0; i < n; i++) f[i] = mask[i] ? 0 : INF;

  const d = new Float64Array(n);
  const z = new Float64Array(Math.max(h, w) + 1);
  const v = new Int32Array(Math.max(h, w));

  // Columns
  const col = new Float64Array(h);
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) col[y] = f[y * w + x];
    const out = edt1d(col, h, z, v);
    for (let y = 0; y < h; y++) d[y * w + x] = out[y];
  }

  // Rows
  const row = new Float64Array(w);
  const result = new Float64Array(n);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) row[x] = d[y * w + x];
    const out = edt1d(row, w, z, v);
    for (let x = 0; x < w; x++) result[y * w + x] = out[x];
  }
  return result;
}

/** 1-D squared distance transform (Felzenszwalb & Huttenlocher). */
function edt1d(
  f: Float64Array,
  n: number,
  z: Float64Array,
  v: Int32Array,
): Float64Array {
  const d = new Float64Array(n);
  let k = 0;
  v[0] = 0;
  z[0] = -INF;
  z[1] = INF;
  for (let q = 1; q < n; q++) {
    let s =
      (f[q] + q * q - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
    while (s <= z[k]) {
      k -= 1;
      s = (f[q] + q * q - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
    }
    k += 1;
    v[k] = q;
    z[k] = s;
    z[k + 1] = INF;
  }
  k = 0;
  for (let q = 0; q < n; q++) {
    while (z[k + 1] < q) k += 1;
    const r = v[k];
    d[q] = (q - r) * (q - r) + f[r];
  }
  return d;
}

/**
 * Signed distance matching backend `interpolation.core.signed_distance`:
 * outside positive, inside negative — so `weighted < 0` selects the object
 * interior. Our squared EDT measures distance-to-feature (zeros), therefore:
 *   edt(~mask) ≡ squaredEdt(mask)       // dist to object (outside field)
 *   edt(mask)  ≡ squaredEdt(~mask)      // dist to background (inside field)
 */
function signedDistance(mask: Uint8Array, h: number, w: number): Float64Array {
  const distOutside = squaredEdt(mask, h, w);
  const distInside = squaredEdt(invertMask(mask), h, w);
  const out = new Float64Array(h * w);
  for (let i = 0; i < out.length; i++) {
    out[i] = Math.sqrt(distOutside[i]) - Math.sqrt(distInside[i]);
  }
  return out;
}

function invertMask(mask: Uint8Array): Uint8Array {
  const out = new Uint8Array(mask.length);
  for (let i = 0; i < mask.length; i++) out[i] = mask[i] ? 0 : 1;
  return out;
}

function labelBBox(
  plane: Int32Array,
  h: number,
  w: number,
  label: number,
): { y0: number; y1: number; x0: number; x1: number } | null {
  let y0 = h, y1 = -1, x0 = w, x1 = -1;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) {
      if (plane[row + x] !== label) continue;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
    }
  }
  if (y1 < 0) return null;
  return { y0, y1, x0, x1 };
}

function unionBox(
  a: { y0: number; y1: number; x0: number; x1: number },
  b: { y0: number; y1: number; x0: number; x1: number },
  h: number,
  w: number,
  pad: number,
) {
  return {
    y0: Math.max(0, Math.min(a.y0, b.y0) - pad),
    y1: Math.min(h - 1, Math.max(a.y1, b.y1) + pad),
    x0: Math.max(0, Math.min(a.x0, b.x0) - pad),
    x1: Math.min(w - 1, Math.max(a.x1, b.x1) + pad),
  };
}

function cropMask(
  plane: Int32Array,
  w: number,
  label: number,
  box: { y0: number; y1: number; x0: number; x1: number },
): Uint8Array {
  const ch = box.y1 - box.y0 + 1;
  const cw = box.x1 - box.x0 + 1;
  const out = new Uint8Array(ch * cw);
  for (let y = 0; y < ch; y++) {
    const src = (box.y0 + y) * w + box.x0;
    const dst = y * cw;
    for (let x = 0; x < cw; x++) out[dst + x] = plane[src + x] === label ? 1 : 0;
  }
  return out;
}

export type LocalInterpSlice = { index: number; mask: Uint8Array };

/**
 * Plan intermediate 0/1 masks for `label` between two endpoint planes.
 * Returns empty array if the label is missing on either endpoint or depth < 2.
 */
export function planLocalInterpolation(
  firstPlane: Int32Array,
  lastPlane: Int32Array,
  h: number,
  w: number,
  firstIndex: number,
  lastIndex: number,
  label: number,
): LocalInterpSlice[] {
  const lo = Math.min(firstIndex, lastIndex);
  const hi = Math.max(firstIndex, lastIndex);
  const depth = hi - lo;
  if (depth < 2 || label < 1) return [];
  if (firstPlane.length !== h * w || lastPlane.length !== h * w) return [];

  const firstIsLo = firstIndex <= lastIndex;
  const planeLo = firstIsLo ? firstPlane : lastPlane;
  const planeHi = firstIsLo ? lastPlane : firstPlane;

  const b0 = labelBBox(planeLo, h, w, label);
  const b1 = labelBBox(planeHi, h, w, label);
  if (!b0 || !b1) return [];

  const box = unionBox(b0, b1, h, w, 8);
  const ch = box.y1 - box.y0 + 1;
  const cw = box.x1 - box.x0 + 1;
  const maskLo = cropMask(planeLo, w, label, box);
  const maskHi = cropMask(planeHi, w, label, box);
  if (!maskLo.some(Boolean) || !maskHi.some(Boolean)) return [];

  // Empty/full crops have no surface for SDF — refuse like the server.
  if (maskLo.every(Boolean) || maskHi.every(Boolean)) return [];

  const sdfLo = signedDistance(maskLo, ch, cw);
  const sdfHi = signedDistance(maskHi, ch, cw);

  const slices: LocalInterpSlice[] = [];
  for (let offset = 1; offset < depth; offset++) {
    const k = offset / depth;
    const full = new Uint8Array(h * w);
    let any = false;
    for (let y = 0; y < ch; y++) {
      for (let x = 0; x < cw; x++) {
        const i = y * cw + x;
        const weighted = sdfLo[i] * (1 - k) + sdfHi[i] * k;
        if (weighted < 0) {
          full[(box.y0 + y) * w + (box.x0 + x)] = 1;
          any = true;
        }
      }
    }
    if (any) slices.push({ index: lo + offset, mask: full });
  }
  return slices;
}

/** Run the expensive EDT/SDF work away from React's main thread when Web
 * Workers are available. Tests and older runtimes use the deterministic sync
 * implementation; production transfers the already-detached endpoint planes
 * so it does not duplicate their multi-megabyte buffers. */
export function planLocalInterpolationAsync(
  firstPlane: Int32Array,
  lastPlane: Int32Array,
  h: number,
  w: number,
  firstIndex: number,
  lastIndex: number,
  label: number,
): Promise<LocalInterpSlice[]> {
  if (typeof Worker === "undefined") {
    return Promise.resolve(
      planLocalInterpolation(firstPlane, lastPlane, h, w, firstIndex, lastIndex, label),
    );
  }
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./localInterpolate.worker.ts", import.meta.url), {
      type: "module",
    });
    const timeout = window.setTimeout(() => {
      worker.terminate();
      reject(new Error("Interpolation worker timed out"));
    }, 120_000);
    const finish = () => {
      window.clearTimeout(timeout);
      worker.terminate();
    };
    worker.onerror = () => {
      finish();
      reject(new Error("Interpolation worker failed"));
    };
    worker.onmessage = (event: MessageEvent<LocalInterpSlice[]>) => {
      finish();
      resolve(event.data);
    };
    worker.postMessage(
      { firstPlane, lastPlane, h, w, firstIndex, lastIndex, label },
      [firstPlane.buffer, lastPlane.buffer],
    );
  });
}
