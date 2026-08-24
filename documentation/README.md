# Mito Data Studio documentation

This directory is the release-facing documentation portal for Mito Data
Studio. It separates product behavior, operating instructions, implementation
details, deployment evidence, manuscript material, and release controls so a
reader can identify whether a statement describes the software, one deployment,
or an experimental result.

## Start here

| Audience | Document |
| --- | --- |
| New user or evaluator | [Product overview](product/OVERVIEW.md) |
| Requester, manager, or annotator | [User guide](../docs/user-guide.md) and [end-to-end workflows](user/WORKFLOWS.md) |
| Developer or reviewer | [System architecture](technical/ARCHITECTURE.md) |
| Microscopy data specialist | [Data and storage contract](technical/DATA_AND_STORAGE.md) |
| AI/model reviewer | [AI models and algorithms](technical/AI_AND_ALGORITHMS.md) |
| Deployment operator | [Hardware-adaptive deployment](../docs/hardware-adaptive-deployment.md) |
| Manuscript author | [Methods description](paper/METHODS.md) |
| Claims reviewer | [Claims and evidence matrix](paper/CLAIMS_MATRIX.md) |
| Reproducibility reviewer | [Reproducibility record](paper/REPRODUCIBILITY.md) |
| Release manager | [Release checklist](release/RELEASE_CHECKLIST.md) |
| Release verifier | [Current validation status](release/VALIDATION_STATUS.md) |

## Documentation authority

This portal describes the current development branch. The executable code,
database migrations, tests, locked dependency files, and vendored model
manifests remain the primary evidence. Existing operational references under
`docs/` are retained to avoid breaking established links and runbooks.

Claims in `paper/` are deliberately conservative. They describe implemented
methods and the recorded development environment; they do not claim biological
accuracy, annotation-time savings, throughput improvements, or inter-rater
agreement unless a separately archived experiment establishes those results.
