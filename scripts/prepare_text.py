"""Prepare a text for the reading library: fetch -> segment -> translate.

Takes one or more bokselskap.no chapter/tale URLs (each becomes a section),
extracts the Norwegian prose, splits it into sentences, translates them with
a local Ollama model (paragraph-at-a-time for context, sentence-aligned),
and writes a staging JSON to data/texts_staging/<slug>.json for review.
Nothing touches the database; that's scripts/import_text.py's job.

Usage:
    uv run python -m scripts.prepare_text \
        https://www.bokselskap.no/boker/folkeeventyr/askeladdensomkapaad \
        --slug askeladden-trollet --kind folktale --orthography dano-1904

    # multi-chapter: pass each chapter URL in reading order
    uv run python -m scripts.prepare_text URL1 URL2 URL3 --slug ... --kind novel
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# The gate module owns the shared patterns and the quality checks; the
# extractor warns about the same faults before they reach a translation.
from scripts.translation_gates import (
    GLUED_NUMBER_RE,
    check_all,
    check_sentence,
    looks_untranslated,
)

STAGING_DIR = Path(__file__).parent.parent / "data" / "texts_staging"
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:12b"

PROMPT = (
    "Translate these Norwegian sentences into English. The text may use "
    "19th-century Dano-Norwegian orthography. Translate faithfully, keeping "
    "the register of the original; this is for language learners reading the "
    "Norwegian and English side by side, so keep each translation aligned "
    "with its sentence. Reply with exactly one numbered line per sentence, "
    "same numbers, and nothing else.\n\n"
)


# --- fetching & extraction ---------------------------------------------------


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "langkey-prepare"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


# Print apparatus that sits inside the prose: bokselskap marks the original
# page breaks with <span class="origPage">12</span> mid-sentence. Stripping
# tags alone keeps the digit and glues it to the next word ("1Ifjor"), and a
# number landing after a full stop also stops the sentence splitter seeing a
# boundary, so the whole span has to go, not just its markup.
APPARATUS_RE = re.compile(r'<span class="origPage">.*?</span>', re.S)



def strip_tags(fragment: str) -> str:
    fragment = APPARATUS_RE.sub("", fragment)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def extract(page: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """Return (book_title, heading, byline_author, paragraphs).

    bokselskap gives every page two <h1>s: a plain one naming the book or
    collection, and one with class="page_title" naming this page. Which is
    wanted depends on the text: a single tale is titled by its page ("Jomfru
    Marjas gullsko"), while a novel split over chapters is titled by the book
    ("Mysterier") with the page titles becoming section headings.

    Paragraphs come from class="para" / "paraFirstletter", which cleanly
    excludes navigation, facsimile notes, author bios, and social links. Fall
    back to all <p> for other sites.
    """
    page_h1 = re.search(r'<h1 class="page_title">(.*?)</h1>', page, re.S)
    plain_h1 = re.search(r'<h1(?![^>]*class=)[^>]*>(.*?)</h1>', page, re.S)
    heading = strip_tags(page_h1.group(1)) if page_h1 else None
    book_title = strip_tags(plain_h1.group(1)) if plain_h1 else None
    if heading is None:
        heading = book_title
    para_re = re.compile(r'<p class="para[^"]*">(.*?)</p>', re.S)
    matches = para_re.findall(page) or re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)
    paras = [t for t in (strip_tags(m) for m in matches) if t]
    byline = re.search(r"<p[^>]*>\s*av (.*?)</p>", page, re.S)
    author = strip_tags(byline.group(1)) if byline else None
    return book_title, heading, author, paras


def sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[.!?»]) (?=[A-ZÆØÅ«])", paragraph)
    return [s.strip() for s in parts if s.strip()]


# --- translation -------------------------------------------------------------


def ollama(model: str, prompt: str) -> str:
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 8192},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read())["response"].strip()


def translate_paragraph(model: str, sents: list[str]) -> list[str]:
    """Translate a paragraph's sentences together (context) but aligned."""
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sents, 1))
    reply = ollama(model, PROMPT + numbered)
    out: dict[int, str] = {}
    for line in reply.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    if sorted(out) == list(range(1, len(sents) + 1)):
        return [out[i] for i in range(1, len(sents) + 1)]
    # misaligned reply: fall back to one call per sentence
    def strip_number(reply: str) -> str:
        m = re.match(r"\s*\d+[.)]\s*(.+)", reply, re.S)
        return (m.group(1) if m else reply).strip()

    return [strip_number(ollama(model, PROMPT + f"1. {s}")) for s in sents]


TITLE_PROMPT = (
    "Translate this Norwegian book or folktale title into English. It may use "
    "19th-century Dano-Norwegian orthography. Reply with only the English "
    "title: no quotation marks, no explanation, no alternatives.\n\n"
    "Norwegian title: {title}\n"
)


def translate_title(model: str, title: str) -> str | None:
    """Translate a title, or return None rather than store something wrong.

    A bad title is worse than no title: it is the one string a learner sees
    before choosing what to read, and unlike a sentence it has no Norwegian
    beside it to correct the impression. So the same gates that check the
    prose check this, and anything they flag is dropped.
    """
    reply = ollama(model, TITLE_PROMPT.format(title=title)).strip()
    # Models like to answer a one-line question with a paragraph.
    candidate = reply.splitlines()[0].strip() if reply else ""
    candidate = candidate.strip("\"'«»“”").strip()
    if not candidate:
        return None
    # A title does not get three times longer in translation; that is the
    # model explaining itself.
    if len(candidate.split()) > 3 * len(title.split()) + 4:
        return None
    # The echo gate needs four English words before it will speak, and titles
    # are shorter than that, so handle the echo here. A one-word title handed
    # back unchanged is usually a name that needs no translation (Victoria,
    # Pan); two or more words is the model giving up. Dropping a correct
    # identical title costs nothing -- the reading list then shows the one
    # title, which is what it would have shown anyway.
    def flat(x: str) -> str:
        return re.sub(r"[^\w\s]", "", x).lower().strip()

    if len(title.split()) > 1 and flat(candidate) == flat(title):
        return None
    if check_sentence(0, title, candidate):
        return None
    return candidate


# --- main --------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", nargs="+", help="page URLs, one per section, in order")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--kind", required=True, help="folktale | novel | nonfiction | article")
    ap.add_argument("--title", help="override; default from first page <h1>")
    ap.add_argument(
        "--title-en",
        help="override the English title; default is translated and gate-checked",
    )
    ap.add_argument("--author", help="override; default from byline")
    ap.add_argument("--orthography", default="nb", help="nb | dano-1904 | ...")
    ap.add_argument("--license", default="public domain")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, help="max sentences (for a quick trial run)")
    args = ap.parse_args()

    sections, all_sents = [], []
    seq = paragraph_no = 0
    title = author = None
    multi = len(args.urls) > 1
    for si, url in enumerate(args.urls, 1):
        book_title, heading, byline, paras = extract(fetch(url))
        if si == 1:
            # One tale is named by its own page; a novel by the book, with the
            # chapter headings kept per section.
            title = (book_title if multi else heading) or heading or book_title
            author = byline
        sections.append({"seq": si, "heading": heading if multi else None})
        for p in paras:
            paragraph_no += 1
            batch = []
            for s in sentences(p):
                seq += 1
                batch.append({"seq": seq, "section": si, "paragraph": paragraph_no, "no": s})
            all_sents.append(batch)
    flat = [s for b in all_sents for s in b]
    if args.limit:
        flat = flat[: args.limit]
        all_sents = [[s for s in b if s["seq"] <= args.limit] for b in all_sents]
        all_sents = [b for b in all_sents if b]
    print(f"{title!r} by {author!r}: {len(flat)} sentences in {paragraph_no} paragraphs")

    # Extraction sanity: print apparatus leaking into the prose is invisible
    # once translated, so catch it here rather than in the reading view. A
    # bare number stuck to a word is the signature.
    glued = [s for s in flat if GLUED_NUMBER_RE.search(s["no"])]
    if glued:
        print(f"  WARNING: {len(glued)} sentences have a number glued to a word —")
        print("  page markers are probably leaking into the text. First few:")
        for s in glued[:3]:
            print(f"    {s['seq']}: {s['no'][:80]}")
    if not title or re.fullmatch(r"[IVXLC]+\.?", title or ""):
        print(f"  WARNING: title is {title!r} — looks like a chapter number, not a book.")
        print("  Pass --title explicitly.")

    final_title = args.title or title or args.slug
    title_en = args.title_en or translate_title(args.model, final_title)
    if title_en:
        print(f"  English title: {title_en!r}")
    else:
        print(f"  WARNING: no usable English title for {final_title!r} — the reading")
        print("  list will show the Norwegian alone. Pass --title-en to set one.")

    t0 = time.time()
    done = retried = 0
    for batch in all_sents:
        for s, en in zip(batch, translate_paragraph(args.model, [s["no"] for s in batch])):
            for _ in range(2):
                if not looks_untranslated(s["no"], en):
                    break
                retried += 1  # model echoed the Norwegian; ask again, alone
                en = ollama(
                    args.model,
                    "Translate this Norwegian sentence into English. Reply with "
                    "only the English translation.\n\n" + s["no"],
                )
            s["en"] = en
        done += len(batch)
        rate = done / (time.time() - t0)
        remaining = (len(flat) - done) / rate if rate else 0
        print(f"\r  translated {done}/{len(flat)} (~{remaining/60:.0f} min left) ", end="")
    print()
    still_bad = [s["seq"] for b in all_sents for s in b if looks_untranslated(s["no"], s["en"])]
    if retried:
        print(f"  {retried} echoed-Norwegian replies retried; still suspect: {still_bad or 'none'}")

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    out = STAGING_DIR / f"{args.slug}.json"
    out.write_text(
        json.dumps(
            {
                "slug": args.slug,
                "title": final_title,
                "title_en": title_en,
                "author": args.author or author,
                "language": "nb",
                "kind": args.kind,
                "orthography": args.orthography,
                "source_url": args.urls[0],
                "license": args.license,
                "translator": args.model,
                "prepared_at": datetime.now(UTC).isoformat(),
                "sections": sections,
                "sentences": flat,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"Wrote {out}")

    # Run the gates now, while the translation is fresh and the staging file is
    # still the only copy. Reporting here is not a substitute for
    # check_translation: that one can also repair, and import_text refuses a
    # file with unresolved findings.
    findings = check_all(flat)
    if findings:
        counts = Counter(f.gate for f in findings)
        print(f"  {len(findings)} quality findings:")
        for name, n in counts.most_common():
            print(f"    {n:4} {name}")
        print(f"  uv run python -m scripts.check_translation {out} --repair")
    else:
        print("  quality gates: clean")
    print(f"Then: uv run python -m scripts.import_text {out}")


if __name__ == "__main__":
    sys.exit(main())
