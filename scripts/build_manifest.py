# scripts/build_manifest.py
"""Render clients/word/manifest.prod.xml from manifest.template.xml + $ADDIN_ORIGIN.

Usage: ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py

Renders + validates IN MEMORY (well-formed XML, origin substituted, no localhost left),
and only writes the output file on success — a failed run never leaves a broken (or
overwrites a previously-good) manifest.prod.xml on disk. Exits non-zero on any failure.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TEMPLATE = Path("clients/word/manifest.template.xml")
OUT = Path("clients/word/manifest.prod.xml")


def main() -> int:
    origin = os.environ.get("ADDIN_ORIGIN", "").strip().rstrip("/")
    if not origin.startswith("https://") or origin == "https:":
        print("ERROR: set ADDIN_ORIGIN to the https origin (e.g. https://host.internal)", file=sys.stderr)
        return 2

    text = TEMPLATE.read_text(encoding="utf-8").replace("${ADDIN_ORIGIN}", origin)

    # Validate the rendered text IN MEMORY before touching the output file, so a
    # failed run never writes (or overwrites a previously-good) manifest.prod.xml.
    try:
        ET.fromstring(text)  # raises ParseError on malformed XML (e.g. unescaped & in origin)
    except ET.ParseError as e:
        print(f"ERROR: rendered manifest is not well-formed XML: {e}", file=sys.stderr)
        return 1
    if "localhost" in text:
        print("ERROR: localhost still present in rendered manifest (incomplete template?)", file=sys.stderr)
        return 1
    if origin not in text:
        print("ERROR: origin not present in rendered manifest", file=sys.stderr)
        return 1

    OUT.write_text(text, encoding="utf-8")  # write only after all checks pass
    print(f"wrote {OUT} with origin {origin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
