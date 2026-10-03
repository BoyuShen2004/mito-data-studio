#!/usr/bin/env python3
"""Check local inline Markdown links in tracked and new repository documents."""
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)")


def prose(path):
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$", "", text, flags=re.M | re.S)


def anchors(path):
    found, counts = set(), {}
    for line in prose(path).splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"<[^>]*>", "", match[1]).lower()
        slug = "".join(c for c in title if c.isalnum() or c in "_- ")
        slug = slug.replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(f"{slug}-{count}" if count else slug)
    found.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', prose(path)))
    return found


def main():
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.md"],
        cwd=ROOT,
    )
    files = sorted({ROOT / p.decode() for p in output.split(b"\0") if p})
    errors, checked = [], 0
    for source in files:
        if not source.is_file():
            continue
        for match in LINK.finditer(prose(source)):
            url = urlsplit(match[1])
            if url.scheme or url.netloc:
                continue
            target = (source.parent / unquote(url.path)).resolve() if url.path else source
            checked += 1
            if not target.exists():
                errors.append(f"{source.relative_to(ROOT)}: missing {match[1]}")
            elif url.fragment and target.suffix == ".md" and unquote(url.fragment) not in anchors(target):
                errors.append(f"{source.relative_to(ROOT)}: missing anchor {match[1]}")
    for error in errors:
        print(error)
    print(f"Checked {checked} local inline links in {len(files)} Markdown files; {len(errors)} errors.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
