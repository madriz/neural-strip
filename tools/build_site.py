#!/usr/bin/env python3
"""Stamp the shared head tags, nav and footer into every site page.

The nav, footer and canonical/JS-class head block are defined once, in tools/partials/.
Each page carries marker comments, and this script replaces whatever sits between them:

    <!-- partial:head -->   ... <!-- /partial:head -->
    <!-- partial:nav -->    ... <!-- /partial:nav -->
    <!-- partial:footer --> ... <!-- /partial:footer -->

The stamped HTML is committed, so every page works as plain HTML with JavaScript off.
pages.yml runs this before deploying, so a page edited without rebuilding still ships
with the current nav. Deterministic: running it twice changes nothing.

Usage:
    python tools/build_site.py           # rewrite pages in place
    python tools/build_site.py --check   # exit 1 if any page is out of date
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"
PARTIALS = Path(__file__).resolve().parent / "partials"
BASE_URL = "https://neuralstrip.com/"

# Nav order. Page file -> label. Canonical URL is BASE_URL + file ("" for the home page).
PAGES = [
    ("index.html", "Home"),
    ("about.html", "About"),
    ("characters.html", "Characters"),
    ("how-it-works.html", "How it works"),
]


def canonical(page: str) -> str:
    return BASE_URL if page == "index.html" else BASE_URL + page


def nav_items(current: str) -> str:
    out = []
    for page, label in PAGES:
        cur = ' aria-current="page"' if page == current else ""
        out.append(f'            <li><a href="{page}"{cur}>{html.escape(label)}</a></li>')
    return "\n".join(out)


def render(name: str, page: str) -> str:
    text = (PARTIALS / f"{name}.html").read_text(encoding="utf-8").rstrip("\n")
    return text.replace("{{items}}", nav_items(page)).replace("{{canonical}}", canonical(page))


def stamp(source: str, page: str) -> str:
    for name in ("head", "nav", "footer"):
        pat = re.compile(rf"(<!-- partial:{name} -->)(.*?)(<!-- /partial:{name} -->)", re.S)
        if not pat.search(source):
            raise SystemExit(f"{page}: missing <!-- partial:{name} --> markers")
        source = pat.sub(lambda m: m.group(1) + "\n" + render(name, page) + "\n" + m.group(3), source, count=1)
    return source


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for page, _ in PAGES:
        path = SITE / page
        if not path.exists():
            continue
        before = path.read_text(encoding="utf-8")
        after = stamp(before, page)
        if after != before:
            stale.append(page)
            if not check:
                path.write_text(after, encoding="utf-8", newline="\n")
    if check:
        print("out of date: " + ", ".join(stale) if stale else "all pages up to date")
        return 1 if stale else 0
    print("stamped: " + (", ".join(stale) if stale else "nothing changed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
