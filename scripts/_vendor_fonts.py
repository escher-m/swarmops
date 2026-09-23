#!/usr/bin/env python3
"""Rewrite a Google Fonts stylesheet into self-hosted @font-face rules."""
import pathlib
import re
import subprocess
import sys

css = pathlib.Path(sys.argv[1]).read_text()
out = []
for subset, block in re.findall(r"/\*\s*([\w\-\[\]]+)\s*\*/\s*(@font-face\s*\{.*?\})", css, re.S):
    if subset != "latin":
        continue  # the other subsets triple the payload for no benefit here
    fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
    weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
    url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
    name = f"{fam.replace(' ', '')}-{weight}.woff2"
    subprocess.run(["curl", "-fsSL", url, "-o", f"dashboard/static/fonts/{name}"], check=True)
    out.append(block.replace(url, f"/static/fonts/{name}"))

pathlib.Path("dashboard/static/fonts.css").write_text(
    "/* Self-hosted so the dashboard makes no third-party requests.\n"
    " * Latin subset only; regenerate with scripts/fetch_assets.sh.\n */\n\n"
    + "\n\n".join(out) + "\n"
)
print(f"vendored {len(out)} font files")
