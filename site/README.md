# swarmops.dev — marketing site

Static site. No framework, no runtime dependencies, no third-party requests.

```bash
../.venv/bin/python build.py          # -> site/dist/
../.venv/bin/python build.py --serve  # build + serve on :8100
```

## Layout

```
site/
├── build.py          ~140 lines; renders templates, posts, sitemap, RSS, headers
├── templates/        Jinja2 — base.html carries all the SEO
├── content/blog/     Markdown posts with front matter
├── assets/           site.css, site.js, og.png
└── dist/             build output — deploy this, do not edit it
```

Fonts are copied from `dashboard/static/fonts/` at build time, so the site and the
product share one typeface and neither makes a request to Google.

## Adding a post

Create `content/blog/your-slug.md`:

```markdown
---
title: A headline that reads well on the page
seo_title: A shorter one for search results
description: 110–160 characters. Shown in search results and social cards.
published: 2026-09-23
tags: [postgres, integrity]
---

Body in Markdown.
```

`seo_title` is optional and falls back to `title`. It exists because a good headline is
often longer than the ~60 characters a search result shows, and compromising both to fit
one is the wrong trade.

The sitemap, the RSS feed and the resources index all pick it up automatically — none of
them are hand-maintained, so none of them can drift from what was actually built.

## Before you launch

Three things are deliberately left unfinished, because they need a decision rather than
code:

1. **The waitlist form posts nowhere.** `templates/waitlist.html` has
   `action="#REPLACE_WITH_YOUR_FORM_ENDPOINT"`. Point it at Formspree, Netlify Forms, or
   your own endpoint. All three work without JavaScript. A waitlist collects personal
   data — say where it goes, and honour deletion requests.

2. **The comparison grid has unverified cells.** `/compare/` marks them `verify`.
   The narrative claims are sourced; the per-vendor grid is not. Confirm every cell
   against current vendor documentation before this goes in front of a customer. This
   space changes monthly.

3. **The investor page has placeholder metrics.** `/for-investors/` shows
   `fill in` where numbers belong. They are blank on purpose — invented traction is the
   fastest way to lose a term sheet in diligence. Fill them or delete the rows.

Also worth a look: the homepage terminal shows an illustrative ledger at ~2,500 records,
captioned as illustrative directly beneath. That is honest as it stands, but replace it
with a real figure from a design partner as soon as you have one.

## Deploying

The output is plain static files. Any host works.

**Vercel** — build command `python site/build.py`, output directory `site/dist`.

**Netlify** — same, and `_headers` is picked up automatically for the security headers.

**Cloudflare Pages** — same; configure headers via `_headers` or a Pages rule.

**Anything else** — `rsync -a site/dist/ user@host:/var/www/swarmops/`.

Set `BASE_URL` in `build.py` to your real domain before the first deploy; it is baked
into canonical tags, the sitemap and the RSS feed.

## What the build emits

- One directory per route, each containing `index.html`, so URLs end in `/` with no
  extension and no server rewrite rules
- `sitemap.xml` and `robots.txt`, generated from the pages actually built
- `blog/feed.xml` (RSS 2.0)
- `_headers` with CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`.
  The CSP is strict because the site genuinely loads nothing external — stating that in a
  header lets the browser enforce it.
