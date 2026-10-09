# Python release dependencies

- `release.in`: direct dependencies and version constraints edited by maintainers.
- `release.txt`: generated lock containing transitive dependencies and download
  hashes, used for release installation.

After dependency changes, run the compilation command recorded at the top of
`release.txt` from the repository root, then review the diff. Moving these files
did not upgrade or resolve dependencies again.

Install into an isolated release virtual environment:

```bash
uv pip install --python /path/to/release/venv/bin/python \
  --index-strategy unsafe-best-match --require-hashes -r requirements/release.txt
```

The lock uses both PyPI and the PyTorch index, with versions and hashes fixed by
the lock. `environment.yml` separately describes the Conda development
environment, including Python, Node and CUDA. `ops/docker/requirements-*.txt`
defines core/CPU/GPU container dependency layers; those manifests are not copies
of the release lock and currently omit kimimaro. See
[development](../docs/development.md) and
[measurement prerequisites](../docs/engineering/measurements.md#deployment-prerequisites).
