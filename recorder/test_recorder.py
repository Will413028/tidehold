"""Run from this directory: python3 -m unittest -v test_recorder"""

import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import check
import record

ATOM_FEED = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>GFA #1</title><link type="application/cap+xml" href="https://example.test/a.cap"/></entry>
  <entry><title>GFA #2</title><link type="text/html" href="https://example.test/b.html"/>
    <link type="application/cap+xml" href="https://example.test/b.cap"/></entry>
</feed>"""

RSS_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Thunderstorm</title><link>https://example.test/X_alert.xml</link></item>
</channel></rss>"""

T0 = dt.datetime(2026, 10, 19, 0, 0, tzinfo=dt.timezone.utc)  # noqa: UP017


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, accept):
        self.calls.append(url)
        status, body = self.pages.get(url, (404, b"not found"))
        return record.Response(status, "application/xml", body)


def clock(start=T0, step=dt.timedelta(seconds=1)):
    state = {"t": start}

    def now():
        state["t"] += step
        return state["t"]

    return now


def read_all(root: Path):
    return [json.loads(gzip.decompress(p.read_bytes())) for p in sorted(root.glob("*/*/*.json.gz"))]


class FeedLinks(unittest.TestCase):
    def test_atom_and_rss_links_are_read_in_order(self):
        self.assertEqual([l.href for l in record.feed_links(ATOM_FEED)],  # noqa: E741
                         ["https://example.test/a.cap", "https://example.test/b.html", "https://example.test/b.cap"])
        self.assertEqual(record.feed_links(RSS_FEED), [record.Link("Thunderstorm", "https://example.test/X_alert.xml", "")])  # noqa: E501


class RecordSource(unittest.TestCase):
    source = record.Source("demo", "https://example.test/feed", pick=record._cap_link)

    def test_index_and_new_documents_are_recorded_with_metadata(self):
        http = FakeHttp({self.source.url: (200, ATOM_FEED), "https://example.test/a.cap": (200, b"<alert/>")})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            summary = record.record_source(self.source, out, http, clock())
            records = read_all(out)
        self.assertEqual(summary, {"source": "demo", "index_status": 200, "docs": 1, "doc_errors": 1})
        self.assertEqual([(r["kind"], r["url"], r["http_status"]) for r in records], [
            ("index", self.source.url, 200),
            ("document", "https://example.test/a.cap", 200),
            ("document", "https://example.test/b.cap", 404),  # non-200 is kept
        ])
        for r in records:
            self.assertEqual(r["source"], "demo")
            self.assertTrue(r["fetched_at"].endswith("Z"))

    def test_a_recorded_document_is_not_fetched_again_but_a_failed_one_is(self):
        http = FakeHttp({self.source.url: (200, ATOM_FEED), "https://example.test/a.cap": (200, b"<alert/>")})
        with tempfile.TemporaryDirectory() as tmp:
            record.record_source(self.source, Path(tmp), http, clock())
            http.calls.clear()
            record.record_source(self.source, Path(tmp), http, clock(T0 + dt.timedelta(minutes=30)))
        self.assertEqual(http.calls, [self.source.url, "https://example.test/b.cap"])

    def test_a_network_error_is_recorded_without_status(self):
        def down(url, accept):
            return record.Response(None, None, b"", error="URLError: timed out")

        with tempfile.TemporaryDirectory() as tmp:
            record.record_source(self.source, Path(tmp), down, clock())
            (only,) = read_all(Path(tmp))
            problems, _ = check.check(Path(tmp), 1, dt.timedelta(minutes=45), [])
        self.assertIsNone(only["http_status"])
        self.assertEqual(only["error"], "URLError: timed out")
        self.assertEqual(problems, [])

    def test_a_non_utf8_body_is_kept_as_base64(self):
        http = FakeHttp({self.source.url: (200, b"\xff\xfe")})
        with tempfile.TemporaryDirectory() as tmp:
            record.record_source(record.Source("demo", self.source.url), Path(tmp), http, clock())
            (only,) = read_all(Path(tmp))
        self.assertEqual((only["body_encoding"], only["body"]), ("base64", "//4="))


class Check(unittest.TestCase):
    def run_check(self, fetch_minutes, mutate=None):
        source = record.Source("demo", "https://example.test/feed")
        http = FakeHttp({source.url: (200, b"<feed/>")})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for minute in fetch_minutes:
                record.record_source(source, out, http, clock(T0 + dt.timedelta(minutes=minute)))
            if mutate:
                path = next(out.glob("*/*/*.json.gz"))
                data = json.loads(gzip.decompress(path.read_bytes()))
                mutate(data)
                path.write_bytes(gzip.compress(json.dumps(data).encode()))
            return check.check(out, 4, dt.timedelta(minutes=45), ["demo"])

    def test_four_fetches_thirty_minutes_apart_pass(self):
        problems, rows = self.run_check([0, 30, 60, 90])
        self.assertEqual(problems, [])
        self.assertEqual(rows[0]["longest_run"], 4)

    def test_a_gap_breaks_the_run(self):
        problems, rows = self.run_check([0, 30, 120, 150, 180])
        self.assertEqual(rows[0]["longest_run"], 3)
        self.assertEqual(len(problems), 1)

    def test_a_file_without_http_status_fails(self):
        problems, _ = self.run_check([0, 30, 60, 90], mutate=lambda d: d.pop("http_status"))
        self.assertTrue(any("missing http_status" in p for p in problems), problems)

    def test_an_expected_source_without_files_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems, _ = check.check(Path(tmp), 4, dt.timedelta(minutes=45), ["pagasa"])
        self.assertEqual(problems, ["pagasa: longest run of consecutive index fetches is 0, need 4"])


if __name__ == "__main__":
    unittest.main()
