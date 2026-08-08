#!/usr/bin/env python3
"""Emit the installed release dependency licence inventory as JSON.

Run with the release venv after ``npm ci``.  The command is deliberately
read-only and exits non-zero when any installed package lacks licence evidence.
"""

from __future__ import annotations

import argparse
from importlib.metadata import distributions
import json
from pathlib import Path
import re


def _python_inventory() -> list[dict[str, str]]:
    rows = []
    for dist in distributions():
        metadata = dist.metadata
        licence = (metadata.get("License-Expression") or metadata.get("License") or "").strip()
        source = "metadata"
        if not licence:
            classifiers = [
                value.split(" :: ")[-1]
                for value in metadata.get_all("Classifier", [])
                if value.startswith("License ::")
            ]
            licence = "; ".join(classifiers)
            source = "classifier"
        if not licence:
            candidates = [
                dist.locate_file(item)
                for item in (dist.files or [])
                if re.search(r"(^|/)(licen[cs]e|copying)(\.|$)", str(item), re.I)
            ]
            if candidates:
                text = Path(candidates[0]).read_text(encoding="utf-8", errors="replace")[:4096]
                if "Apache License" in text:
                    licence = "Apache-2.0"
                elif "Redistribution and use in source and binary forms" in text:
                    licence = "BSD-family (see installed licence file)"
                else:
                    licence = "SEE-INSTALLED-LICENCE-FILE"
                source = str(candidates[0])
        rows.append({
            "name": metadata.get("Name", "UNKNOWN"),
            "version": dist.version,
            "license": " ".join(licence.split()),
            "evidence": source,
        })
    return sorted(rows, key=lambda row: row["name"].lower())


def _javascript_inventory(node_modules: Path) -> list[dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for manifest in node_modules.glob("*/package.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("name") and data.get("version"):
            key = (data["name"], data["version"])
            rows[key] = {"name": key[0], "version": key[1], "license": data.get("license", "")}
    for manifest in node_modules.glob("@*/*/package.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("name") and data.get("version"):
            key = (data["name"], data["version"])
            rows[key] = {"name": key[0], "version": key[1], "license": data.get("license", "")}
    return sorted(rows.values(), key=lambda row: row["name"].lower())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-modules", type=Path, default=Path("frontend/node_modules"))
    args = parser.parse_args()
    python = _python_inventory()
    javascript = _javascript_inventory(args.node_modules)
    missing = [
        f"{ecosystem}:{row['name']}@{row['version']}"
        for ecosystem, rows in (("python", python), ("javascript", javascript))
        for row in rows
        if not row["license"]
    ]
    agpl = [
        f"{ecosystem}:{row['name']}@{row['version']}"
        for ecosystem, rows in (("python", python), ("javascript", javascript))
        for row in rows
        if "AGPL" in row["license"].upper()
    ]
    print(json.dumps({
        "schema": 1,
        "python": python,
        "javascript": javascript,
        "summary": {
            "python_count": len(python),
            "javascript_count": len(javascript),
            "missing_license_evidence": missing,
            "agpl_dependencies": agpl,
        },
    }, indent=2, sort_keys=True))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
