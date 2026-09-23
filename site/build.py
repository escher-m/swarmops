#!/usr/bin/env python3
"""Static site generator for swarmops.dev.

Deliberately about a hundred lines rather than a framework. The site is a dozen
pages of mostly-prose; the only thing a generator earns here is not duplicating
the nav and footer twelve times and not hand-maintaining the sitemap.

    python site/build.py          # -> site/dist/
    python site/build.py --serve  # build, then serve on :8100

Every page declares its own title, meta description and canonical path, because
a marketing site whose pages share one description is a marketing site that
ranks for nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
DIST = SITE / "dist"

BASE_URL = "https://swarmops.dev"
SITE_NAME = "SwarmOps"
TAGLINE = "The audit ledger for AI agent actions"


@dataclass
class Post:
    slug: str
    title: str
    seo_title: str
    description: str
    published: str
    reading_minutes: int
    tags: list[str]
    body_html: str
    toc: list[tuple[str, str]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"/blog/{self.slug}/"


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Minimal `key: value` front matter between --- fences."""
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta: dict = {}
    for line in fm.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value
    return meta, body


def load_posts() -> list[Post]:
    posts = []
    for path in sorted((SITE / "content" / "blog").glob("*.md")):
        meta, body = parse_front_matter(path.read_text())
        md = markdown.Markdown(extensions=["extra", "toc", "codehilite", "sane_lists"],
                               extension_configs={"codehilite": {"noclasses": False}})
        html = md.convert(body)
        # Words per minute is a guess dressed as a fact everywhere it appears;
        # 210 is a reasonable middle for technical prose.
        words = len(re.findall(r"\w+", body))
        toc = [(t["id"], t["name"]) for t in getattr(md, "toc_tokens", [])]
        posts.append(Post(
            slug=path.stem,
            title=meta.get("title", path.stem),
            # A headline that reads well on the page is often too long for a
            # search result, which truncates around 60 characters. Keeping them
            # separate beats compromising both.
            seo_title=meta.get("seo_title") or meta.get("title", path.stem),
            description=meta.get("description", ""),
            published=meta.get("published", str(date.today())),
            reading_minutes=max(1, round(words / 210)),
            tags=meta.get("tags", []),
            body_html=html,
            toc=toc,
        ))
    return sorted(posts, key=lambda p: p.published, reverse=True)


def build(serve: bool = False) -> int:
    env = Environment(
        loader=FileSystemLoader(SITE / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    posts = load_posts()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    shutil.copytree(SITE / "assets", DIST / "assets")
    # The dashboard already vendors these; sharing them keeps one copy of the
    # typeface and avoids a third-party request from the marketing site too.
    shutil.copytree(ROOT / "dashboard" / "static" / "fonts", DIST / "assets" / "fonts",
                    dirs_exist_ok=True)
    shutil.copy(ROOT / "dashboard" / "static" / "fonts.css", DIST / "assets" / "fonts.css")

    common = {
        "base_url": BASE_URL, "site_name": SITE_NAME, "tagline": TAGLINE,
        "posts": posts, "year": date.today().year, "today": date.today().isoformat(),
    }

    pages = [
        ("index.html", "/", "index.html"),
        ("how-it-works.html", "/how-it-works/", "how-it-works/index.html"),
        ("compare.html", "/compare/", "compare/index.html"),
        ("for-investors.html", "/for-investors/", "for-investors/index.html"),
        ("faq.html", "/faq/", "faq/index.html"),
        ("resources.html", "/resources/", "resources/index.html"),
        ("waitlist.html", "/waitlist/", "waitlist/index.html"),
    ]

    written: list[str] = []
    for template_name, url_path, out_path in pages:
        html = env.get_template(template_name).render(**common, canonical=url_path)
        target = DIST / out_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html)
        written.append(url_path)

    post_tpl = env.get_template("post.html")
    for post in posts:
        html = post_tpl.render(**common, post=post, canonical=post.url)
        target = DIST / "blog" / post.slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html)
        written.append(post.url)

    # RSS. Declared in every page's <head>, so it has to actually exist --
    # a dangling feed link is the kind of small broken promise that makes a
    # reader wonder what else is decorative.
    from email.utils import format_datetime
    from datetime import datetime, timezone
    from xml.sax.saxutils import escape

    def rfc822(iso: str) -> str:
        return format_datetime(
            datetime.fromisoformat(iso).replace(tzinfo=timezone.utc))

    items = "\n".join(
        f"""    <item>
      <title>{escape(p.title)}</title>
      <link>{BASE_URL}{p.url}</link>
      <guid isPermaLink="true">{BASE_URL}{p.url}</guid>
      <description>{escape(p.description)}</description>
      <pubDate>{rfc822(p.published)}</pubDate>
    </item>"""
        for p in posts
    )
    feed_dir = DIST / "blog"
    feed_dir.mkdir(parents=True, exist_ok=True)
    (feed_dir / "feed.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{SITE_NAME} — engineering notes</title>
    <link>{BASE_URL}/resources/</link>
    <description>{TAGLINE}</description>
    <language>en</language>
    <atom:link href="{BASE_URL}/blog/feed.xml" rel="self" type="application/rss+xml"/>
{items}
  </channel>
</rss>
"""
    )

    # sitemap.xml — generated, so it cannot drift from what was actually built.
    urls = "\n".join(
        f"  <url><loc>{BASE_URL}{u}</loc><lastmod>{date.today().isoformat()}</lastmod>"
        f"<changefreq>{'weekly' if u == '/' else 'monthly'}</changefreq>"
        f"<priority>{'1.0' if u == '/' else '0.7'}</priority></url>"
        for u in written
    )
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"
    )
    # Netlify/Vercel-style headers. Referrer-Policy and the CSP matter here:
    # the site loads nothing third-party, and saying so lets a browser enforce it.
    (DIST / "_headers").write_text(
        "/*\n"
        "  X-Frame-Options: DENY\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
        "form-action 'self' https:; frame-ancestors 'none'; base-uri 'self'\n"
    )

    print(f"built {len(written)} page(s) into {DIST.relative_to(ROOT)}")
    for u in written:
        print(f"  {u}")

    if serve:
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(DIST), **kw)

            def log_message(self, *a):
                pass

        with socketserver.TCPServer(("127.0.0.1", 8100), Handler) as httpd:
            print("\nserving http://127.0.0.1:8100 (ctrl-c to stop)")
            httpd.serve_forever()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    raise SystemExit(build(ap.parse_args().serve))
