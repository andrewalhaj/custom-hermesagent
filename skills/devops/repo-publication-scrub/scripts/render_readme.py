#!/usr/bin/env python3
"""Render a README (with ```mermaid blocks) to PDF for review.

Usage: render_readme.py <README.md> <assets-dir> <out.pdf> [css]

For each ```mermaid block: write diagram-N.mmd, render to PNG with mermaid-cli (headless chrome needs --no-sandbox
as root), substitute an ABSOLUTE image path (a relative one resolves against the HTML dir and wkhtmltopdf embeds
nothing), then pandoc -> HTML -> wkhtmltopdf. Re-renders diagrams from the current README text on every run so
labels track prose edits. Verify afterwards: `pdfimages -list out.pdf` shows one image per diagram.
"""
import json, pathlib, re, subprocess, sys

src, assets, out = sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), sys.argv[3]
css = sys.argv[4] if len(sys.argv) > 4 else None
assets.mkdir(parents=True, exist_ok=True)
pp = assets / "pp.json"
pp.write_text(json.dumps({"args": ["--no-sandbox"]}))

md = open(src).read()
blocks = re.findall(r"```mermaid\n(.*?)```", md, flags=re.S)
pngs = []
for i, mmd in enumerate(blocks, 1):
    m = assets / f"diagram-{i}.mmd"; p = assets / f"diagram-{i}.png"
    m.write_text(mmd)
    subprocess.run(["npx", "-y", "@mermaid-js/mermaid-cli", "-i", str(m), "-o", str(p), "-b", "white", "-s", "2", "-p", str(pp)],
                   check=True, capture_output=True)
    pngs.append(p)
it = iter(pngs)
md = re.sub(r"```mermaid\n.*?```", lambda _: f"![diagram]({next(it)})", md, flags=re.S)

tmp = assets / "README-temp.md"; tmp.write_text(md)
html = assets / "README.html"
cmd = ["pandoc", str(tmp), "-o", str(html), "--standalone", "--toc", "--metadata", "title= "]
if css:
    cmd += ["--css", css]
subprocess.run(cmd, check=True)
subprocess.run(["wkhtmltopdf", "--quiet", "--enable-local-file-access", "-s", "Letter",
                "-B", "15mm", "-T", "15mm", "-L", "15mm", "-R", "15mm", str(html), out], check=True)
info = subprocess.run(["pdfinfo", out], capture_output=True, text=True).stdout
pages = re.search(r"Pages:\s+(\d+)", info)
print(f"{pages.group(1) if pages else '?'} pages, {len(pngs)} diagrams -> {out}")
