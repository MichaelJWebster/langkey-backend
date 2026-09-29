"""Find public-domain Norwegian texts to add to the reading library.

Searches the bokselskap.no catalogue (473 proofread Norwegian classics,
public domain) and optionally Project Gutenberg's Norwegian shelf. Prints
candidates with the URL to feed into scripts/prepare_text.py.

Usage:
    uv run python -m scripts.find_texts                # full bokselskap list
    uv run python -m scripts.find_texts ibsen          # filter title/author
    uv run python -m scripts.find_texts --gutenberg troll
"""

import argparse
import html
import json
import re
import urllib.parse
import urllib.request

BOKSELSKAP_CATALOGUE = "https://www.bokselskap.no/boker"
GUTENDEX = "https://gutendex.com/books?languages=no"

ENTRY_START_RE = re.compile(
    r'<a href="(https://www\.bokselskap\.no/boker/[^"]+)"[^>]*class="bok_lenke"'
)
TITLE_RE = re.compile(r'<span class="book_title">([^<]*)</span>')
AUTHOR_RE = re.compile(r'<span class="book_author">av <em>([^<]*)</em>')
YEAR_RE = re.compile(r'<span class="utg_ar">Utgitt ([^<]*)</span>')


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "langkey-finder"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def bokselskap(query: str | None) -> list[dict]:
    page = fetch(BOKSELSKAP_CATALOGUE)
    books = []
    starts = list(ENTRY_START_RE.finditer(page))
    for i, m in enumerate(starts):
        # fields live between this entry's <a> tag and the next one's
        block = page[m.end() : starts[i + 1].start() if i + 1 < len(starts) else m.end() + 1500]
        title = TITLE_RE.search(block)
        if not title:
            continue
        author, year = AUTHOR_RE.search(block), YEAR_RE.search(block)
        books.append(
            {
                "title": html.unescape(title.group(1).strip()),
                "author": html.unescape(author.group(1).strip()) if author else "",
                "year": year.group(1).strip() if year else "",
                "url": m.group(1),
            }
        )
    if query:
        q = query.lower()
        books = [b for b in books if q in b["title"].lower() or q in b["author"].lower()]
    return books


def gutenberg(query: str | None) -> list[dict]:
    url = GUTENDEX + (f"&search={urllib.parse.quote(query)}" if query else "")
    books = []
    while url:
        data = json.loads(fetch(url))
        for b in data["results"]:
            if "no" not in b.get("languages", []):
                continue
            text_url = next(
                (u for k, u in b.get("formats", {}).items() if k.startswith("text/plain")),
                b.get("formats", {}).get("text/html", ""),
            )
            books.append(
                {
                    "title": b["title"],
                    "author": "; ".join(a["name"] for a in b.get("authors", [])),
                    "year": str(b.get("authors", [{}])[0].get("death_year") or ""),
                    "url": text_url,
                }
            )
        url = data.get("next") if query is None else None  # one page when searching
    return books


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="?", help="filter by title/author substring")
    ap.add_argument("--gutenberg", action="store_true", help="search Project Gutenberg instead")
    args = ap.parse_args()

    books = gutenberg(args.query) if args.gutenberg else bokselskap(args.query)
    if not books:
        print("No matches.")
        return
    width = max(len(b["title"]) for b in books)
    for b in sorted(books, key=lambda b: (b["author"], b["title"])):
        print(f"{b['title']:<{width}}  {b['author']:<30}  {b['year']:<6}  {b['url']}")
    print(f"\n{len(books)} texts. Next: uv run python -m scripts.prepare_text <url>")


if __name__ == "__main__":
    main()
