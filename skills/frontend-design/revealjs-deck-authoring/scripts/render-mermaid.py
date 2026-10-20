#!/usr/bin/env python3
"""Render a Mermaid file to SVG via mermaid.ink's pako endpoint (Kroki-flakiness fallback).

Usage: python3 render-mermaid.py input.mmd output.svg

- Uses the PAKO route (zlib-compressed JSON state). Do NOT use mermaid.ink's plain
  svg/base64: route — it 400s even on trivial valid flowcharts (verified 2026-08-14).
- Injects the foreignObject overflow/font fix right after the opening <svg> tag so
  labels don't clip their last character when embedded in a deck.
- Exit 0 only when the output is a real SVG of plausible size.
"""
import base64
import json
import sys
import urllib.request
import zlib

INJECT = ('<style>foreignObject{overflow:visible!important}'
          'foreignObject div,foreignObject span{font-family:"Trebuchet MS",'
          'Verdana,Arial,sans-serif!important}</style>')


def render(src: str) -> str:
    payload = json.dumps({"code": src, "mermaid": {"theme": "neutral"}}).encode()
    comp = zlib.compress(payload, 9)
    b = base64.urlsafe_b64encode(comp).decode().rstrip("=")
    url = f"https://mermaid.ink/svg/pako:{b}"
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode()
    if not body.lstrip().startswith("<svg") or len(body) < 500:
        raise RuntimeError(f"mermaid.ink returned non-SVG or tiny body ({len(body)} bytes): {body[:120]}")
    i = body.index(">", body.index("<svg")) + 1
    return body[:i] + INJECT + body[i:]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = open(sys.argv[1]).read()
    svg = render(src)
    open(sys.argv[2], "w").write(svg)
    print(f"OK {sys.argv[2]} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
