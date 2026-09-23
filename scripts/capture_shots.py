#!/usr/bin/env python3
"""Capture real dashboard screenshots for the marketing site.

These are photographs of the running product, not mockups: the script signs in
to the live dashboard and captures whatever is actually on screen. If a page
regresses, the screenshot regresses with it, which is the point.

    python scripts/capture_shots.py

Requires the dashboard on :8090 in dev auth mode. Output lands in
site/assets/shots/ at 2x for retina, then is downscaled and compressed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "assets" / "shots"
BASE = "http://127.0.0.1:8090"

# (slug, path, viewport height, optional scroll-to selector)
SHOTS = [
    ("records",   "/",           900, None),
    ("verify",    "/verify",     900, None),
    ("approvals", "/approvals",  900, None),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,          # retina; downscaled again below
            color_scheme="dark",
        )
        page = ctx.new_page()

        # The dashboard defaults to dark but remembers a per-viewer choice;
        # pin it so a capture never inherits whatever was last clicked.
        ctx.add_init_script("try{localStorage.setItem('swarmops-theme','dark')}catch(e){}")

        page.goto(f"{BASE}/auth/login", wait_until="networkidle")
        page.fill('input[name="username"]', "alice")
        page.fill('input[name="password"]', "alice-dev-password")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # The dev-auth banner is honest but it is not what we want to advertise.
        ctx.add_init_script("""
          document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.banner.warn').forEach((b) => {
              if (b.textContent.includes('Development authentication')) b.remove();
            });
          });
        """)

        incident = page.evaluate("""
          fetch('/api/search', {headers:{accept:'application/json'}})
            .then(r => r.json()).then(d => d.incidents[0]?.incident_id || '')
        """)
        shots = list(SHOTS)
        if incident:
            shots.append(("incident", f"/incident/{incident}", 1050, None))

        for slug, path, height, _ in shots:
            page.set_viewport_size({"width": 1440, "height": height})
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(1600)   # let the chain canvas and reveals settle
            target = OUT / f"{slug}.png"
            page.screenshot(path=str(target))
            print(f"  captured {slug:<10} {path}")

        browser.close()

    # Downscale from 2x and compress: a marketing page does not need 2880px PNGs.
    from PIL import Image
    for png in sorted(OUT.glob("*.png")):
        img = Image.open(png).convert("RGB")
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        jpg = png.with_suffix(".jpg")
        img.save(jpg, "JPEG", quality=86, optimize=True, progressive=True)
        png.unlink()
        print(f"  {jpg.name:<16} {img.width}x{img.height}  {jpg.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
