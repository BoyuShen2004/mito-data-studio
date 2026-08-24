# Claims and evidence matrix

Use this table when drafting abstracts, Results, figure legends, software
descriptions, and release announcements. “Implemented” means supported by code
and tests; it does not automatically mean scientifically validated.

| Candidate statement | Current evidence | Publication status |
| --- | --- | --- |
| The system supports role-based registration, assignment, annotation, submission, review, and sharing | Models, services, API/UI tests, workflow documentation | May be described as implemented functionality |
| Registered images and ROI masks are not modified during annotation | Separate source/working paths, ownership rules, reset/review tests | May be described as a design and tested invariant |
| TIFF, HDF5, and NIfTI registration is supported | Registration extensions, format adapters, parity tests; optional packages required | May be described with stated dimensionality/metadata limits |
| Zarr v3 pyramids are bounded, additive, and checksum validated | Pyramid builder, validation code, tests | May be described as an implementation method |
| Pyramids are OME-NGFF or compatible with Neuroglancer/Fileglancer | No NGFF multiscales contract or viewer validation | Must not be claimed |
| EfficientSAM and SAM2 are integrated as human-reviewed assistive tools | Vendored assets, inference adapters, UI/API tests | May be described as implemented integration |
| The models are accurate for mitochondrial EM segmentation | No frozen study dataset/reference-standard analysis in this repository | Requires an accuracy experiment |
| Track reduces annotation time | Timing instrumentation exists, but no controlled comparative study is archived | Requires a prespecified usability/productivity study |
| Track batch throughput is improved | Timing logs and a contiguous slab optimization exist | Requires matched before/after benchmarks on frozen data/hardware |
| The software uses all GPUs automatically | Probe detects GPUs but one Track batch is not sharded | Must not be claimed |
| The software is hardware adaptive | Probe-derived worker/device/memory/crop recommendations and Docker env wiring | May be described with the exact scope and limitations |
| The software is open source | Root license currently grants no first-party reuse permission | Must not be claimed until licensing changes |
| Unit tests establish biological validity | Tests establish software behavior only | Must not be claimed |

For every Results claim, link the archived dataset, protocol, raw output,
analysis script, statistical result, software tag, environment, and hardware
record. If evidence is unavailable, describe the item as future work or a
limitation rather than converting implementation intent into a result.

