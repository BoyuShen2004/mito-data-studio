# 26 — License Compliance Plan

## Before any copy from WEBKNOSSOS

1. Record decision: reuse / adapt / port / independent / API.
2. Open attribution row in `evidence/source-map.md` + `attribution/REGISTER.md`.
3. Copy LICENSE/NOTICE snippets as required.
4. Mark modified files with provenance headers.

## If AGPL code is incorporated into the served product

- Provide Corresponding Source offer (AGPL §13) for network users.
- Align mito-data-agent distribution license or isolate AGPL processes with documented boundaries.
- Do not mix incompatible proprietary dependencies into AGPL-covered modules without review.

## Permissive-safe building blocks

- `cluster_tools` (MIT)
- TensorStore / Zarr / NumPy / SciPy / scikit-image (respective licenses — verify versions)
- Independent SDF interpolation reimplementation

## Checklist before production

- [ ] LICENSE chosen for mito-data-agent
- [ ] NOTICE generated
- [ ] REGISTER complete for all WK-derived files
- [ ] Vendor model licenses recorded (EfficientSAM, SAM2)
- [ ] Source offer mechanism if AGPL applies
- [ ] Legal owner sign-off
