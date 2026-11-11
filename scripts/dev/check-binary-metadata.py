#!/usr/bin/env python3
"""Check the metadata of PDF and image files for personal or account identifiers.

Text scanners (gitleaks, the private denylist) skip binary files. This script reads only the
metadata that travels with a file and that a reader of the repository cannot see on the page:

- PDF: the document information dictionary (/Author, /Creator, /Producer, /Title, /Subject,
  /Keywords) and any uncompressed XMP packet.
- PNG: tEXt, zTXt and iTXt chunks.
- JPEG: COM segments and the readable strings of APP1 (EXIF/XMP) segments.

It flags home-directory paths, e-mail addresses outside example domains, 12-digit numbers that are
not documented placeholders, and any string from the private denylist (see check-denylist.sh).
Output names files and the kind of finding, never the matched value.

Usage:
  scripts/dev/check-binary-metadata.py [file ...]   # default: every tracked *.pdf, *.png, *.jpg, *.jpeg
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

EXTS = (".pdf", ".png", ".jpg", ".jpeg")
CHECKS = {
    "home path": re.compile(r"(?:/Users|/home|C:\\\\?Users)[/\\]+[A-Za-z][A-Za-z0-9._-]{1,31}[/\\]"),
    "e-mail": re.compile(
        r"[A-Za-z0-9._%+-]+@(?!(?:[A-Za-z0-9-]+\.)*example\.(?:com|org|net)\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    ),
    "12-digit number": re.compile(
        r"(?<![0-9])(?!000000000000|111111111111|123456789012|999999999999)[0-9]{12}(?![0-9])"
    ),
}
PDF_KEYS = re.compile(rb"/(Author|Creator|Producer|Title|Subject|Keywords)\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>)")
XMP = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.S)


def denylist() -> list[str]:
    raw = os.environ.get("DENYLIST")
    if not raw:
        path = os.environ.get("DENYLIST_FILE") or str(Path.home() / ".config/doc-ai-patterns/denylist.txt")
        raw = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
    return [s.strip().lower() for s in raw.splitlines() if s.strip() and not s.strip().startswith("#")]


def pdf_text(data: bytes) -> str:
    out = []
    for _key, val in PDF_KEYS.findall(data):
        if val.startswith(b"<"):
            hexs = re.sub(rb"\s", b"", val[1:-1])
            raw = bytes.fromhex(hexs.decode()) if len(hexs) % 2 == 0 else b""
            out.append(raw.decode("utf-16") if raw.startswith(b"\xfe\xff") else raw.decode("latin-1"))
        else:
            out.append(val[1:-1].decode("latin-1"))
    out += [m.decode("utf-8", "replace") for m in XMP.findall(data)]
    return "\n".join(out)


def png_text(data: bytes) -> str:
    out, i = [], 8
    while i + 8 <= len(data):
        (n,) = struct.unpack(">I", data[i : i + 4])
        kind, body = data[i + 4 : i + 8], data[i + 8 : i + 8 + n]
        if kind == b"tEXt":
            out.append(body.decode("latin-1"))
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            out.append(key.decode("latin-1") + " " + zlib.decompress(rest[1:]).decode("latin-1", "replace"))
        elif kind == b"iTXt":
            key, _, rest = body.partition(b"\0")
            compressed, rest = rest[0], rest[2:]
            _lang, _, rest = rest.partition(b"\0")
            _tkey, _, text = rest.partition(b"\0")
            text = zlib.decompress(text) if compressed else text
            out.append(key.decode("latin-1") + " " + text.decode("utf-8", "replace"))
        i += 12 + n
    return "\n".join(out)


def jpeg_text(data: bytes) -> str:
    out, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):
            break
        (n,) = struct.unpack(">H", data[i + 2 : i + 4])
        seg = data[i + 4 : i + 2 + n]
        if marker == 0xFE or 0xE1 <= marker <= 0xEF:
            out += [m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e]{4,}", seg)]
        i += 2 + n
    return "\n".join(out)


def metadata(path: Path) -> str:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_text(data)
    if suffix == ".png":
        return png_text(data)
    return jpeg_text(data)


def main(argv: list[str]) -> int:
    if not argv:
        git = ["git", "ls-files"]
        argv = subprocess.run(git, capture_output=True, text=True, check=True).stdout.split()  # noqa: S603, S607
    files = argv
    files = [f for f in files if f.lower().endswith(EXTS) and Path(f).is_file()]
    deny = denylist()
    bad = 0
    for f in files:
        try:
            text = metadata(Path(f))
        except Exception as exc:  # malformed file: report, do not crash the hook
            print(f"check-binary-metadata: cannot read {f}: {type(exc).__name__}", file=sys.stderr)
            bad += 1
            continue
        kinds = [k for k, rx in CHECKS.items() if rx.search(text)]
        if any(d in text.lower() for d in deny):
            kinds.append("private denylist entry")
        if kinds:
            print(f"check-binary-metadata: {f}: {', '.join(kinds)}", file=sys.stderr)
            bad += 1
    if bad:
        print("Fix the generator or strip the metadata before committing.", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
