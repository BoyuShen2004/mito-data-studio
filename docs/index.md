# Mito Data Studio documentation

This is the documentation entry point for the repository. New team members
should learn the annotation workflow before exploring the code. Using an
existing lab instance does not require a local development environment.
Documentation is maintained in English; UI labels and code identifiers retain
the names used by the application.

## Start with your task

| Goal | Start here | Next step |
| --- | --- | --- |
| Join the project | [Getting started](getting-started/README.md) | [Glossary](getting-started/glossary.md) |
| Learn to use the application | [User guide](user-guide.md) | Choose chapters for your role |
| Discover research tools | [Extensions](user-guide/extensions.md) | [Add or disable an extension](engineering/extensions.md) |
| Understand Measurements | [Measurement workflow](user-guide/09-measurements.md) | [Method and implementation](engineering/measurements.md) |
| Explore the code before developing | [Code map](engineering/code-map.md) | [Feature walkthrough](engineering/feature-walkthrough.md) |
| Make a first contribution | [First contribution](getting-started/first-contribution.md) | [Development environment](development.md) |
| Install or maintain a service | [Operations](operations/README.md) | Choose Docker or the existing-host workflow |
| Prepare a release or manuscript | [Release checklist](release/checklist.md) | [Research materials](research/README.md) |

## Documentation tree

```text
docs/
  index.md                  Documentation entry point
  overview.md               Product scope and roles
  getting-started/           Onboarding, glossary, first contribution
  user-guide.md             User guide entry point
  user-guide/               Instructions organized by UI workflow
  engineering/              Code map, architecture, data, algorithms, measurements
  development.md            Development environment and routine commands
  product-invariants.md     Product requirements changes must preserve
  operations/               Installation, production operations, reference hardware
  release/                  Release checks and dated validation evidence
  research/                 Methods, research evidence, reproducibility
  attribution.md            Third-party code and attribution
```

The legacy `documentation/` tree and some old `docs/*.md` pages only forward to
maintained pages. Do not maintain a second copy of a topic. Implementation,
migrations and tests determine actual behavior; a validation record applies
only to its stated date and version.

## Reference topics

- Engineering: [root files](engineering/root-files.md), [architecture](engineering/architecture.md), [data and storage](engineering/data-and-storage.md), [AI and algorithms](engineering/ai-and-algorithms.md), [measurements](engineering/measurements.md), [extensions](engineering/extensions.md), [software audit](engineering/software-audit.md).
- Release: [dated validation history](release/validation-history.md), [third-party attribution](attribution.md).

## Current implementation audit

The [documentation audit](research/documentation-audit.md) records differences
between documentation and main, implementation/test evidence, and validation
limits. Findings include autosave, Track previews, CUDA requirements and
exceptions to approval-based official-label promotion.

## Maintain the documentation

Put workflow instructions in `user-guide/`, internal design in `engineering/`,
and deployment steps in `operations/`. Maintain one complete page per topic and
link to it elsewhere. Update documentation when behavior changes. Never include
real passwords, tokens or production data.

Run `python scripts/docs/check_links.py` from the repository root to check local
links. Project policies remain at the root: [CONTRIBUTING](../CONTRIBUTING.md),
[LICENSE](../LICENSE), and [SECURITY](../SECURITY.md).
