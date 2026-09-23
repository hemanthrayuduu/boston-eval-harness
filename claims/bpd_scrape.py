"""Scrape BPD's crime-statistics posts and the PDF reports they link to.

    uv run python -m claims.bpd_scrape --out claims/sources/bpd

BPD publishes its weekly year-to-date statistics as WordPress posts in the
"Crime Stats" category of police.boston.gov. The numbers are not in the posts:
each post links two or three PDFs (Part One crime by district, shooting victims,
firearm arrests and recoveries), rendered by SQL Server Reporting Services with
extractable text.

The archive is not continuous. As of 2026-09-23 the category holds 639 posts:
2005-2011 ("UPDATED CRIME DATA"), whose content was lost in a site migration and
links nothing, and 2023 onward ("Crime Statistics: January 1 - ..."), which link
the PDFs. Nothing exists for 2012-2022. Every post is recorded anyway, with its
dates -- a post with no reports is a fact about the archive, not noise to drop.

Output, under ``--out``:

* ``posts.jsonl`` -- one record per post: WordPress id, publication and
  modification dates (local and GMT), link, title, the rendered content, and
  each linked PDF's URL, local filename, size and SHA-256. Committed.
* ``pdf/`` -- the PDFs, named ``{date}_{post id}_{n}.pdf``. Gitignored; the
  hashes in posts.jsonl let anyone re-fetch and verify them.

Re-running is cheap: a PDF already on disk is not downloaded again, and its
recorded hash is recomputed from the file rather than trusted -- unless the post's
``modified_gmt`` differs from the previous run's, in which case BPD edited it and
its PDFs are fetched afresh.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingest.http import HttpError, RequestsTransport, RetryingTransport, Transport

__all__ = ["BASE_URL", "CATEGORY_ID", "Post", "PdfRef", "fetch_posts", "pdf_links", "scrape"]

BASE_URL = "https://police.boston.gov"
CATEGORY_ID = 21  # "Crime Stats"
PAGE_SIZE = 100
USER_AGENT = (
    "boston-eval-harness/0.1 (research; "
    "+https://github.com/hemanthrayuduu/boston-eval-harness)"
)

_HREF = re.compile(r'href="([^"]+)"', re.IGNORECASE)


@dataclass
class PdfRef:
    url: str
    file: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    error: str | None = None
    """Why the download failed, if it did. Recorded, never silently skipped."""


@dataclass
class Post:
    id: int
    date: str
    date_gmt: str
    modified: str
    modified_gmt: str
    link: str
    title: str
    content_html: str
    pdfs: list[PdfRef] = field(default_factory=list)
    retrieved_at: str = ""

    @classmethod
    def from_api(cls, payload: dict[str, Any], retrieved_at: str) -> Post:
        content = payload["content"]["rendered"]
        return cls(
            id=payload["id"],
            date=payload["date"],
            date_gmt=payload["date_gmt"],
            modified=payload["modified"],
            modified_gmt=payload["modified_gmt"],
            link=payload["link"],
            title=html.unescape(payload["title"]["rendered"]),
            content_html=content,
            pdfs=[PdfRef(url=url) for url in pdf_links(content)],
            retrieved_at=retrieved_at,
        )


def pdf_links(content_html: str) -> list[str]:
    """PDF links in a post, in order, without duplicates. Share buttons and other
    non-PDF links are ignored."""
    seen: dict[str, None] = {}
    for href in _HREF.findall(content_html):
        url = html.unescape(href)
        if url.lower().split("?", 1)[0].endswith(".pdf"):
            seen.setdefault(url, None)
    return list(seen)


def fetch_posts(
    transport: Transport,
    *,
    base_url: str = BASE_URL,
    category: int = CATEGORY_ID,
    sleep=time.sleep,
    delay_s: float = 1.0,
) -> list[dict[str, Any]]:
    """Every post in the category, newest first, via the WordPress REST API.

    Pages until a short page rather than trusting a total header: posts published
    mid-scrape would otherwise shift the paging.
    """
    posts: list[dict[str, Any]] = []
    page = 1
    while True:
        try:
            response = transport.get(
                f"{base_url}/wp-json/wp/v2/posts",
                {
                    "categories": category,
                    "per_page": PAGE_SIZE,
                    "page": page,
                    "orderby": "date",
                    "order": "desc",
                    "_fields": "id,date,date_gmt,modified,modified_gmt,link,title,content",
                },
            )
        except HttpError as err:
            # WordPress answers a page past the end with 400 rest_post_invalid_page_number.
            if err.status == 400 and page > 1:
                break
            raise
        batch = response.json()
        posts.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        sleep(delay_s)

    ids = [p["id"] for p in posts]
    if len(set(ids)) != len(ids):
        raise RuntimeError("the same post came back on two pages; the archive changed mid-scrape, re-run")
    return posts


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scrape(
    out_dir: str | Path,
    transport: Transport | None = None,
    *,
    base_url: str = BASE_URL,
    sleep=time.sleep,
    delay_s: float = 1.0,
) -> list[Post]:
    """Fetch every post and download its PDFs into ``out_dir``. Returns the posts."""
    transport = transport or RetryingTransport(RequestsTransport(user_agent=USER_AGENT))
    out = Path(out_dir)
    pdf_dir = out / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    index = out / "posts.jsonl"
    previous: dict[int, str] = {}
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            previous[record["id"]] = record["modified_gmt"]

    retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
    posts = [
        Post.from_api(payload, retrieved_at)
        for payload in fetch_posts(transport, base_url=base_url, sleep=sleep, delay_s=delay_s)
    ]

    for post in posts:
        edited = post.id in previous and previous[post.id] != post.modified_gmt
        for n, ref in enumerate(post.pdfs, start=1):
            name = f"{post.date[:10]}_{post.id}_{n}.pdf"
            path = pdf_dir / name
            if edited:
                path.unlink(missing_ok=True)
            if not path.exists():
                try:
                    data = transport.get(ref.url).body
                except HttpError as err:
                    ref.error = str(err)
                    continue
                path.write_bytes(data)
                sleep(delay_s)
            data = path.read_bytes()
            ref.file, ref.bytes, ref.sha256 = f"pdf/{name}", len(data), _sha256(data)

    with index.open("w", encoding="utf-8") as handle:
        for post in sorted(posts, key=lambda p: (p.date, p.id)):
            handle.write(json.dumps(asdict(post), ensure_ascii=False, sort_keys=True) + "\n")
    return posts


def summarize(posts: list[Post]) -> str:
    by_year: dict[str, list[Post]] = {}
    for post in posts:
        by_year.setdefault(post.date[:4], []).append(post)
    lines = [f"{len(posts)} posts"]
    for year in sorted(by_year):
        group = by_year[year]
        pdfs = [ref for post in group for ref in post.pdfs]
        failed = [ref for ref in pdfs if ref.error]
        lines.append(
            f"  {year}: {len(group):>3} posts, {len(pdfs):>3} PDFs"
            + (f", {len(failed)} FAILED" if failed else "")
        )
    failures = [(post, ref) for post in posts for ref in post.pdfs if ref.error]
    if failures:
        lines.append("  failures:")
        lines.extend(f"    {post.link}: {ref.url}: {ref.error}" for post, ref in failures)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape BPD crime-statistics posts and PDFs.")
    parser.add_argument("--out", type=Path, default=Path("claims/sources/bpd"))
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = parser.parse_args(argv)

    posts = scrape(args.out, delay_s=args.delay)
    print(summarize(posts))
    return 1 if any(ref.error for post in posts for ref in post.pdfs) else 0


if __name__ == "__main__":
    sys.exit(main())
