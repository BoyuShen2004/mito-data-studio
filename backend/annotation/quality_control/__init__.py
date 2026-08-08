"""Quality-control provider boundary.

Basic submission QC lives behind a small provider interface so the checks can be
swapped or extended (e.g. connected-component scientific QA) without touching
the submission/review services. The service layer calls
:func:`registry.get_qc_provider`; it never imports an adapter directly.

To change QA → edit files in this package (interface, registry, adapters/).
"""
