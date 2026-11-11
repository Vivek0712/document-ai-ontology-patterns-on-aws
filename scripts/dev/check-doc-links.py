#!/usr/bin/env python3
"""Check the Markdown docs: relative links, heading anchors and cited run ids.

- Every relative link target in a tracked *.md file must exist in the repository.
- Every #anchor (same page or another page) must match a heading, using GitHub's slug rules.
- Every run id cited in the docs (r- followed by 12 hex digits) must have a record in runs/local/.

External links (http, https, mailto) are not fetched. Links inside fenced code blocks are ignored.

Usage: scripts/dev/check-doc-links.py [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
RUN_ID = re.compile(r"\br-[0-9a-f]{12}\b")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def slug(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def prose_lines(text: str):
    fence = False
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            yield n, line


def anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        seen: dict[str, int] = {}
        out = set()
        for _, line in prose_lines(path.read_text(encoding="utf-8")):
            m = HEADING.match(line)
            if not m:
                continue
            s = slug(m.group(2))
            k = seen.get(s, 0)
            out.add(s if k == 0 else f"{s}-{k}")
            seen[s] = k + 1
        cache[path] = out
    return cache[path]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    root = Path(ap.parse_args().root).resolve()
    try:
        git = ["git", "ls-files", "*.md"]
        files = subprocess.run(git, cwd=root, capture_output=True, text=True, check=True).stdout.split()  # noqa: S603, S607
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
    if not files:
        files = [str(p.relative_to(root)) for p in root.rglob("*.md") if "node_modules" not in p.parts]
    cache: dict[Path, set[str]] = {}
    errors = []
    runs = root / "runs" / "local"
    for rel in sorted(files):
        md = root / rel
        text = md.read_text(encoding="utf-8")
        for n, line in prose_lines(text):
            for m in LINK.finditer(line):
                target = m.group(1) or m.group(2)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                    continue
                path_part, _, frag = target.partition("#")
                dest = md if not path_part else (md.parent / path_part).resolve()
                if path_part and not dest.exists():
                    errors.append(f"{rel}:{n}: missing link target {path_part}")
                    continue
                if frag and dest.suffix == ".md" and frag not in anchors(dest, cache):
                    errors.append(f"{rel}:{n}: missing anchor #{frag} in {dest.relative_to(root)}")
        if runs.is_dir():
            for n, line in enumerate(text.split("\n"), 1):
                for rid in RUN_ID.findall(line):
                    if not (runs / f"{rid}.json").is_file():
                        errors.append(f"{rel}:{n}: cited run {rid} has no record in runs/local/")
    for e in errors:
        print(e, file=sys.stderr)
    print(f"check-doc-links: {len(files)} files, {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
