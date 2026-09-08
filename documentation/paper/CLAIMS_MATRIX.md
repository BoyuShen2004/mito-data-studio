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
| Tasks, hard cases, and projects share one list presentation, and review occurs on the task's own page | `components/WorkList.tsx`, `pages/TaskDetailPage.tsx`, `components/ReviewBox.tsx`, UI tests | May be described as implemented interface design |
| A task's history is derived from durable records rather than a per-event table | `features/worklist/timeline.ts` is a pure function over `AnnotationTaskSerializer` output; no event table exists; unit tests | May be described as an implementation method with its storage rationale |
| The interface reorganisation improved usability, task-completion time, or error rate | No prespecified usability study, no baseline, no participants, no measurements | **Requires a controlled usability study.** Do not infer this from the redesign itself |
| The system has been operated on a real annotation project | Not recorded here. Deployment figures must be read from the deployment by its operator, with a capture date | May be described as deployment scale once the author supplies the figures. It is **not** evidence of accuracy, throughput, or fitness for purpose |
| Review turnaround, annotator throughput, or hard-case rates from the deployment | Operational database counts only; no protocol, no inclusion rules, no denominators, no consent for reporting participant activity | Must not be reported as a Result without a prespecified analysis and approvals |
| The list endpoints scale to large queues | An embedded-serialiser N+1 in `SubmissionListView` / `MyCompletedTasksView` was fixed; see the CHANGELOG for the measured before/after | May be described as an implementation fix, quoting a benchmark the author reproduces; not a scalability claim |

Operational records of the running deployment are annotator activity, not study
data. Read them only as the operator, with a stated purpose, and do not import
them into this repository: aggregate counts still describe identifiable people's
work in a twenty-person lab, and reporting them needs a prespecified analysis
and participant consent.

For every Results claim, link the archived dataset, protocol, raw output,
analysis script, statistical result, software tag, environment, and hardware
record. If evidence is unavailable, describe the item as future work or a
limitation rather than converting implementation intent into a result.

