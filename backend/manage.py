#!/usr/bin/env python
"""Compatibility entry point; use the repository-root ``manage.py``."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    root_manage = Path(__file__).resolve().parent.parent / "manage.py"
    runpy.run_path(str(root_manage), run_name="__main__")

