# scripts/build_manifest.py
"""Render clients/word/manifest.prod.xml from manifest.template.xml + $ADDIN_ORIGIN.

Usage: ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py

Validates the output: well-formed XML, the origin substituted, no localhost left.
Exits non-zero on any failure.
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
    if not origin.startswith("https://"):
        print("ERROR: set ADDIN_ORIGIN to the https origin (e.g. https://host.internal)", file=sys.stderr)
        return 2

    text = TEMPLATE.read_text(encoding="utf-8").replace("${ADDIN_ORIGIN}", origin)
    OUT.write_text(text, encoding="utf-8")

    # Validate: well-formed, origin present, no localhost left.
    ET.parse(OUT)  # raises on malformed XML
    if "localhost" in text:
        print("ERROR: localhost still present in rendered manifest", file=sys.stderr)
        return 1
    if origin not in text:
        print("ERROR: origin not present in rendered manifest", file=sys.stderr)
        return 1
    print(f"wrote {OUT} with origin {origin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
