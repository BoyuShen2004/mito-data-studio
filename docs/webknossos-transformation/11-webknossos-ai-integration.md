# 11 — WEBKNOSSOS AI Integration

## Documented product features (**DOC**)

- **Quick Select**: threshold always; AI mode when available.
- **AI Analysis** (hosted webknossos.org): run pretrained models (neuron, **mitochondria/MitoNet**, soma/nuclei), train custom models, alignment.
- Credits / jobs monitoring on hosted platform.
- **DOC explicit:** automated analysis currently on webknossos.org; on-prem AI → contact sales.

## Open-source vs hosted (**DOC** + **INFER**)

| Capability | Self-hosted OSS WK | Hosted / commercial |
|---|---|---|
| Manual annotation tools | Yes | Yes |
| Volume interpolation | Yes (frontend) | Yes |
| Task system | Yes | Yes |
| Datastore | Yes | Yes |
| AI Quick Select backend | Depends on deployment wiring | Available |
| Neuron/MitoNet jobs | Not general OSS default | Hosted credits |
| Custom model training UI | Hosted emphasis | Sales/on-prem options |

## Python API (**CODE**: webknossos-libs)

- `RemoteAiModel` upload path reservation via WK API
- Not a local nnU-Net runner

## mito advantage to preserve

mito already embeds **EfficientSAM + SAM2** in-process for interactive annotation — stronger OSS interactive AI than stock self-hosted WK. Batch **nnU-Net/Slurm** is the gap to complete (scaffolding exists).
