# 22 — Target Persistence and Recovery

## Current weakness

- Explicit Save only; refresh loses unsaved strokes.
- Undo = up to 20 full-slice client snapshots; undo re-PUTs slices.
- Working labels are memmap TIFFs (good start) but not chunk-delta op logs.
- Latest-only submission deletion (ReviewRecord survives).

## Target model

```
AnnotationInstance
  ├── Snapshot (periodic compact label chunks / zarr group)
  ├── OperationLog[] (id, prev_id, type, payload, client_ts, server_ts, user)
  ├── ChunkDelta[] (chunk_coord → bytes or RLE, version)
  └── SaveCursor (last_acked_op_id)
```

### Write path

1. Client applies op locally (paint brush stroke → dirty chunks).
2. Enqueue autosave batch `{ops, chunk_versions}`.
3. Server transaction: verify versions; write deltas; append ops; return ack.
4. Conflict → client rebase or reload chunk + notify.

### Undo/redo

- Prefer op-log invert functions (brush → restore previous chunk region).
- Fallback snapshot boundaries every N ops.

### Recovery matrix

| Event | Behavior |
|---|---|
| Browser refresh | Load snapshot + ops after cursor; restore unsaved local queue if IndexedDB present |
| Network drop | Queue locally; exponential backoff; conflict check on flush |
| Server restart | Durable DB + chunk store; clients resume |
| Tab crash | IndexedDB draft ops if implemented; else last ack |

### Approval promotion

Keep mito semantics: working overlay → official on approve, with checksum + optional lock.

### Submission history

Migrate to append-only submissions; deprecate destructive supersede after backfill.
