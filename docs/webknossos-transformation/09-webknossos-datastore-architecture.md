# 09 — WEBKNOSSOS Datastore Architecture

## Role

Standalone Play/Scala service (`webknossos-datastore/`) for dataset bytes. Deployable on storage servers (**DOC**: external storage; **CODE**: README AGPL).

## Capabilities (**CODE**)

`BinaryDataController` / `BinaryDataService`:

- Multi-bucket requests from WK frontend (`requestViaWebknossos`)
- Raw cuboid GET/POST
- Knossos-compatible paths
- JPEG thumbnails
- Mappings, histograms, find-data
- Ad-hoc mesh requests
- Cache clear per org/dataset/layer

Supporting:

- Format readers: Zarr3, Zarr, N5, WKW, Neuroglancer Precomputed (`datareaders/`)
- `ChunkCacheService`
- JNI native bucket scanner (`webknossos-jni`)
- DatasetArray / BucketProvider abstractions

## Trace: Z slider → pixels (**INFER** composed from CODE modules)

```
User moves Z
 → flycam update
 → layer_rendering_manager determines needed buckets at selected mag
 → PullQueue enqueues with priorities; aborts obsolete pulls
 → wkstore_adapter batches HTTP to datastore
 → BinaryDataService reads compressed chunks (cache hit?)
 → bytes returned
 → DataCube stores bucket; TextureBucketManager uploads GPU textures
 → WebGL recompose of viewport
```

## Formats

Official README/docs: WKW, Zarr, N5, Neuroglancer Precomputed, image stacks (converted). Python libs default new datasets to **Zarr3**.

## Implication for mito

Django should **not** remain the sole chunk server. Target options (ranked in `20-target-volume-infrastructure.md`):

1. Dedicated async Python/Zarr chunk service (TensorStore)
2. Compliant reuse/adaptation of WK datastore (AGPL)
3. Direct browser→object-storage for immutable layers + auth gateway
