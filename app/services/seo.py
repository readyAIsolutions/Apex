"""Deterministic on-page SEO audit (blueprint §4 SEO Auditor agent / §11 P1).

Keyless, dependency-free (stdlib html.parser). Every finding is a verifiable fact
about the HTML; the auditor never guesses. Good for landing pages before/after
publishing and for content-QA tickets.
"""
from html.parser import HTMLParser
from typing import Optional


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_buf: list[str] = []
        self._in_title = False
        self._in_body = False
        self._in_json = False
        self.meta_desc: Optional[str] = None
        self.canonical: Optional[str] = None
        self.viewport = False
        self.noindex = False
        self.h1_count = 0
        self.jsonld: list[str] = []
        self.imgs = 0
        self.imgs_no_alt = 0
        self.words = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs or [])
        name = a.get("name", "").lower()
        if tag == "title":
            self._in_title = True
        elif tag == "body":
            self._in_body = True
        elif tag == "meta":
            if name == "description":
                self.meta_desc = (a.get("content") or "").strip()
            if name == "viewport":
                self.viewport = True
            if name == "robots" and "noindex" in (a.get("content") or "").lower():
                self.noindex = True
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href")
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "script" and "ld+json" in (a.get("type") or "").lower():
            self._in_json = True
        elif tag == "img":
            self.imgs += 1
            if not a.get("alt"):
                self.imgs_no_alt += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_json = False

    def handle_data(self, data):
        if self._in_title:
            self.title_buf.append(data)
        elif self._in_json and data.strip():
            self.jsonld.append(data.strip())
        elif self._in_body:
            self.words += len(data.split())


def audit_seo(html: str, url: str = "") -> dict:
    c = _Collector()
    c.feed(html or "")
    title = " ".join("".join(c.title_buf).split())

    checks = [
        ("title", bool(title) and 10 <= len(title) <= 70,
         f"missing" if not title else f"len={len(title)}"),
        ("meta.description", bool(c.meta_desc) and 50 <= len(c.meta_desc) <= 170,
         "missing/short"),
        ("h1", c.h1_count == 1, f"count={c.h1_count}"),
        ("canonical", bool(c.canonical), c.canonical or "missing"),
        ("viewport", c.viewport, "missing (not mobile-friendly)"),
        ("noindex", not c.noindex, "robots noindex present"),
        ("structured_data.jsonld", len(c.jsonld) >= 1, "no JSON-LD structured data"),
        ("img.alt", c.imgs_no_alt == 0, f"{c.imgs_no_alt}/{c.imgs} imgs missing alt"),
        ("content.words", c.words >= 120, f"words={c.words}"),
    ]
    failures = [f for f, ok, _ in checks if not ok]
    return {
        "url": url,
        "title": title,
        "checks": [{"field": f, "ok": ok, "detail": det} for f, ok, det in checks],
        "ok_all": not failures,
        "recommended_next_actions":
            [f"fix {f}" for f, ok, _ in checks if not ok]
            + (["submit draft landing page for approval"] if failures else
               ["page passes on-page checks — monitor GSC clicks/traffic after publish"]),
    }