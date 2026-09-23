# Vendored from Watermelon UI

Source: https://ui.watermelon.sh · https://github.com/WatermelonCorp/watermelon-platform
Licence: MIT

Pulled from the shadcn registry, e.g.:

    curl https://registry.watermelon.sh/r/command-search.json

These are copy-paste components, which the library intends you to edit. Local
changes made, and why:

* **command-search.tsx** — the upstream hotkey is a bare `f`, which collides
  with the dashboard's existing single-letter navigation (`g`/`i`/`a`/`v`) and
  fires while reading. Changed to ⌘K / Ctrl-K. Tailwind's zinc palette was
  swapped for the dashboard's CSS custom properties so the palette matches the
  rest of the app in both themes, and the `section` union type was widened so
  the ledger can supply its own groupings.

Keep this file updated when these are re-pulled, otherwise the next upgrade
silently reverts the changes above.
