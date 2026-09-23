"""The BPD scraper, offline. Every post is kept with its dates, every PDF is
hashed, and a failed download is recorded rather than dropped."""

from __future__ import annotations

import hashlib
import json

from claims.bpd_scrape import fetch_posts, pdf_links, scrape
from ingest.http import HttpError
from tests.test_ckan_client import FakeTransport


def api_post(post_id: int, date: str, content: str, modified: str | None = None) -> dict:
    modified = modified or date
    return {
        "id": post_id,
        "date": date,
        "date_gmt": date,
        "modified": modified,
        "modified_gmt": modified,
        "link": f"https://police.boston.gov/{date[:10].replace('-', '/')}/post-{post_id}/",
        "title": {"rendered": "Crime Statistics: January 1, 2026 &#8211; September 20, 2026 vs. 2025"},
        "content": {"rendered": content},
    }


def pages(*batches: list[dict]):
    """A posts endpoint that serves one batch per page and 400s past the end."""
    def endpoint(params: dict):
        page = params["page"]
        if page > len(batches):
            raise HttpError("HTTP 400 rest_post_invalid_page_number", status=400)
        return batches[page - 1]
    return endpoint


WEEKLY = (
    '<p><a href="https://police.boston.gov/wp-content/uploads/2026/09/Part1.pdf">Part 1 Crime</a></p>'
    '<p><a href="https://police.boston.gov/wp-content/uploads/2026/09/Shootings.pdf">Shootings</a></p>'
    '<a href="https://www.facebook.com/sharer.php?u=https%3A%2F%2Fpolice.boston.gov">share</a>'
)


def no_sleep(_seconds: float) -> None:
    pass


class TestLinks:
    def test_pdf_links_in_order_without_share_buttons(self) -> None:
        assert pdf_links(WEEKLY) == [
            "https://police.boston.gov/wp-content/uploads/2026/09/Part1.pdf",
            "https://police.boston.gov/wp-content/uploads/2026/09/Shootings.pdf",
        ]

    def test_duplicate_links_collapse(self) -> None:
        html = '<a href="https://x/a.pdf">a</a> <a href="https://x/a.pdf">again</a>'
        assert pdf_links(html) == ["https://x/a.pdf"]

    def test_entities_in_hrefs_are_unescaped(self) -> None:
        assert pdf_links('<a href="https://x/a.pdf?v=1&amp;x=2">a</a>') == ["https://x/a.pdf?v=1&x=2"]


class TestPaging:
    def test_pages_until_a_short_page(self, monkeypatch) -> None:
        import claims.bpd_scrape as module

        monkeypatch.setattr(module, "PAGE_SIZE", 2)
        transport = FakeTransport(
            {"wp-json/wp/v2/posts": pages(
                [api_post(3, "2026-09-21T14:46:08", ""), api_post(2, "2026-09-14T15:17:38", "")],
                [api_post(1, "2006-01-01T10:00:00", "")],
            )}
        )
        posts = fetch_posts(transport, sleep=no_sleep)
        assert [p["id"] for p in posts] == [3, 2, 1]

    def test_page_past_the_end_stops_cleanly(self, monkeypatch) -> None:
        import claims.bpd_scrape as module

        monkeypatch.setattr(module, "PAGE_SIZE", 1)
        transport = FakeTransport(
            {"wp-json/wp/v2/posts": pages([api_post(1, "2026-09-21T14:46:08", "")])}
        )
        assert [p["id"] for p in fetch_posts(transport, sleep=no_sleep)] == [1]


class TestScrape:
    def transport(self, **extra) -> FakeTransport:
        routes = {
            "wp-json/wp/v2/posts": pages([
                api_post(10, "2026-09-21T14:46:08", WEEKLY),
                api_post(9, "2006-03-01T10:00:00", "<p>Click to view summary chart</p>"),
            ]),
            "/Part1.pdf": b"%PDF-part-one",
            "/Shootings.pdf": b"%PDF-shootings",
        }
        routes.update(extra)
        return FakeTransport(routes)

    def test_posts_and_pdfs_are_recorded_with_dates_and_hashes(self, tmp_path) -> None:
        scrape(tmp_path, self.transport(), sleep=no_sleep)
        records = [json.loads(line) for line in (tmp_path / "posts.jsonl").read_text().splitlines()]

        # Oldest first, and the linkless 2006 post is kept.
        assert [r["id"] for r in records] == [9, 10]
        assert records[0]["pdfs"] == []

        weekly = records[1]
        assert weekly["date"] == "2026-09-21T14:46:08"
        assert weekly["title"].startswith("Crime Statistics: January 1, 2026 –")
        assert [p["file"] for p in weekly["pdfs"]] == [
            "pdf/2026-09-21_10_1.pdf",
            "pdf/2026-09-21_10_2.pdf",
        ]
        assert weekly["pdfs"][0]["sha256"] == hashlib.sha256(b"%PDF-part-one").hexdigest()
        assert (tmp_path / "pdf" / "2026-09-21_10_2.pdf").read_bytes() == b"%PDF-shootings"

    def test_rerun_does_not_download_again(self, tmp_path) -> None:
        scrape(tmp_path, self.transport(), sleep=no_sleep)
        second = self.transport()
        scrape(tmp_path, second, sleep=no_sleep)
        assert not any(url.endswith(".pdf") for url, _ in second.calls)

    def test_edited_post_is_fetched_afresh(self, tmp_path) -> None:
        """Same URL, new content: BPD corrected the report. The edit shows up as a
        new modified date, and the stale PDF must not survive it."""
        scrape(tmp_path, self.transport(), sleep=no_sleep)
        edited = FakeTransport({
            "wp-json/wp/v2/posts": pages([
                api_post(10, "2026-09-21T14:46:08", WEEKLY, modified="2026-09-22T09:00:00"),
                api_post(9, "2006-03-01T10:00:00", ""),
            ]),
            "/Part1.pdf": b"%PDF-part-one-corrected",
            "/Shootings.pdf": b"%PDF-shootings",
        })
        scrape(tmp_path, edited, sleep=no_sleep)
        assert (tmp_path / "pdf" / "2026-09-21_10_1.pdf").read_bytes() == b"%PDF-part-one-corrected"

    def test_failed_download_is_recorded_not_dropped(self, tmp_path) -> None:
        transport = self.transport(**{"/Shootings.pdf": HttpError("HTTP 404", status=404)})
        posts = scrape(tmp_path, transport, sleep=no_sleep)
        weekly = next(p for p in posts if p.id == 10)
        assert weekly.pdfs[0].sha256 is not None
        assert weekly.pdfs[1].sha256 is None
        assert "404" in weekly.pdfs[1].error

    def test_cli_exits_nonzero_on_a_failed_download(self, tmp_path, monkeypatch) -> None:
        import claims.bpd_scrape as module

        transport = self.transport(**{"/Shootings.pdf": HttpError("HTTP 404", status=404)})
        monkeypatch.setattr(module, "RetryingTransport", lambda inner: transport)
        monkeypatch.setattr(module.time, "sleep", no_sleep)
        assert module.main(["--out", str(tmp_path), "--delay", "0"]) == 1
