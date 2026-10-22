#!/usr/bin/env python3
"""Inspect a macOS .pkg (xar archive) on Linux without xar/bsdtar.

Usage:
  python3 xar_inspect.py <pkg> list          # file listing from TOC
  python3 xar_inspect.py <pkg> identifiers   # pkg identifiers from Distribution/PackageInfo

Verified 2026-07-22 against NinjaOne agent pkg (74 MB, xar v1, zlib TOC).
Presence of a 'Distribution' entry = product archive (do NOT productbuild-wrap).
"""
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib


def read_toc(path):
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"xar!":
            sys.exit(f"Not a xar archive (magic={magic!r})")
        hdr_size, _ver = struct.unpack(">HH", f.read(4))
        toc_clen, _toc_ulen = struct.unpack(">QQ", f.read(16))
        f.seek(hdr_size)
        toc = zlib.decompress(f.read(toc_clen))
    return toc, hdr_size + toc_clen


def walk(elem, prefix=""):
    for fe in elem.findall("file"):
        name = fe.findtext("name")
        full = f"{prefix}/{name}" if prefix else name
        yield full, fe
        yield from walk(fe, full)


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in ("list", "identifiers"):
        sys.exit(__doc__)
    pkg, mode = sys.argv[1], sys.argv[2]
    toc, heap_off = read_toc(pkg)
    root = ET.fromstring(toc)
    files = list(walk(root.find("toc")))

    if mode == "list":
        for full, _ in files:
            print(full)
        return

    with open(pkg, "rb") as f:
        for full, fe in files:
            if not (full.endswith("PackageInfo") or full.split("/")[-1] == "Distribution"):
                continue
            data = fe.find("data")
            if data is None:
                continue
            off = int(data.findtext("offset"))
            length = int(data.findtext("length"))
            enc = data.find("encoding").get("style", "")
            f.seek(heap_off + off)
            raw = f.read(length)
            if "gzip" in enc or "zlib" in enc:
                raw = zlib.decompress(raw)
            text = raw.decode("utf-8", "replace")
            ids = set(re.findall(r'identifier="([^"]+)"', text))
            print(f"===== {full} =====")
            print("identifiers:", ids or "(none)")


if __name__ == "__main__":
    main()
