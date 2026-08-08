export type ChunkDType =
  | "uint8"
  | "int8"
  | "uint16"
  | "int16"
  | "uint32"
  | "int32"
  | "float32"
  | "float64";

export type ChunkTypedArray =
  | Uint8Array
  | Int8Array
  | Uint16Array
  | Int16Array
  | Uint32Array
  | Int32Array
  | Float32Array
  | Float64Array;

/** Read-only layers a volume can stream. Editable labels are not one of them. */
export type ChunkLayer = "image" | "region";

export interface ChunkRequestIdentity {
  deployment: string;
  volumeId: number;
  buildIdentity: string;
  /** Omitted means the image layer, which is what every Phase 12 caller meant. */
  layer?: ChunkLayer;
  mag: string;
  chunk: readonly [number, number, number];
  dtype: ChunkDType;
  representation: "raw-le";
  authorizationScope: string;
}

function field(value: string | number): string {
  const text = String(value);
  return `${text.length}:${text}`;
}

export function chunkRequestScopePrefix(
  deployment: string,
  volumeId: number,
  buildIdentity: string,
  layer: ChunkLayer = "image",
): string {
  return [deployment, volumeId, layer, buildIdentity].map(field).join("|") + "|";
}

/** Collision-safe and deliberately path-free cache/request key.
 *
 * The layer is part of the *scope prefix*, not a trailing field: a build
 * identity only identifies a build within one layer, so two layers rebuilt at
 * the same instant would otherwise share cache entries. */
export function chunkRequestKey(identity: ChunkRequestIdentity): string {
  return (
    chunkRequestScopePrefix(
      identity.deployment,
      identity.volumeId,
      identity.buildIdentity,
      identity.layer ?? "image",
    ) +
    [
      identity.mag,
      ...identity.chunk,
      identity.dtype,
      identity.representation,
      identity.authorizationScope,
    ]
      .map(field)
      .join("|")
  );
}

export interface ChunkLevel {
  mag: string;
  shape: [number, number, number];
  chunks: [number, number, number];
  grid: [number, number, number];
  factors: [number, number, number];
  dtype: ChunkDType;
}

export interface ChunkCapabilities {
  volume_id: number;
  build_identity: string;
  /** Absent on a pre-layer server response, which only ever meant the image. */
  layer?: ChunkLayer;
  /** Every layer this volume can currently stream. */
  layers?: ChunkLayer[];
  mags: ChunkLevel[];
}

export interface DecodedChunk {
  identity: ChunkRequestIdentity;
  shape: [number, number, number];
  voxelOffset: [number, number, number];
  etag: string;
  values: ChunkTypedArray;
  byteLength: number;
  timing: {
    networkMs: number;
    decodeMs: number;
  };
}
