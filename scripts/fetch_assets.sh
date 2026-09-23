#!/usr/bin/env bash
# Re-download the vendored front-end assets.
#
# These are committed rather than fetched at runtime because the dashboard must
# make no third-party requests: an internal compliance tool calling out to a CDN
# leaks which audit pages were viewed and when, and fails closed in the
# air-gapped environments this product is most likely to run in.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
mkdir -p dashboard/static/vendor dashboard/static/fonts
curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js" \
  -o dashboard/static/vendor/three.min.js
curl -fsSL -A "$UA" "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" \
  -o /tmp/swarmops-gf.css
.venv/bin/python scripts/_vendor_fonts.py /tmp/swarmops-gf.css
echo "vendored assets refreshed"
