"""Record official warning feeds, one gzipped JSON envelope per fetch.

Each run fetches every source's index feed, then the warning documents it links
to that were not recorded before. Every response is kept, non-200 included, with
`source`, `url`, `fetched_at` and `http_status`. Standard library only; run with
`python3 -I record.py --out DIR`.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

RECORDER_VERSION = "1"
USER_AGENT = "tidehold-recorder/1 (+https://github.com/Will413028/tidehold)"
TIMEOUT_S = 30
MAX_BODY = 20 * 1024 * 1024
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclasses.dataclass(frozen=True)
class Link:
    title: str
    href: str
    type: str


@dataclasses.dataclass(frozen=True)
class Source:
    name: str
    url: str
    accept: str = "*/*"
    # Picks the linked warning documents to record from the index; None records the index only.
    pick: Callable[[Link], bool] | None = None
    max_docs_per_run: int = 60


def _cap_link(link: Link) -> bool:
    return link.type == "application/cap+xml"


SOURCES: tuple[Source, ...] = (
    Source("pagasa", "https://publicalert.pagasa.dost.gov.ph/feeds/", pick=_cap_link),
    # GeoJSON carries the full CAP fields per alert, so there is nothing to follow.
    Source(
        "nws-marine",
        "https://api.weather.gov/alerts/active?status=actual&region_type=marine",
        accept="application/geo+json",
    ),
    *(
        Source(f"meteoalarm-{code}", f"https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}", pick=_cap_link)  # noqa: E501
        for code, country in (("gr", "greece"), ("mt", "malta"), ("hr", "croatia"))
    ),
    Source(
        "jma-marine",
        "https://www.data.jma.go.jp/developer/xml/feed/other_l.xml",
        pick=lambda link: link.title.startswith(("地方海上警報", "全般海上警報")),
    ),
    Source("bmkg", "https://www.bmkg.go.id/alerts/nowcast/en/rss.xml", pick=lambda link: link.href.endswith("_alert.xml")),  # noqa: E501
    Source("cwa-ncdr", "https://alerts.ncdr.nat.gov.tw/RssAtomFeeds.ashx", pick=lambda link: link.href.endswith(".cap")),  # noqa: E501
    # BoM publishes no public CAP; the state warnings RSS includes marine wind warnings.
    Source("bom-qld", "https://reg.bom.gov.au/fwo/IDZ00056.warnings_qld.xml", pick=lambda link: True),
)


@dataclasses.dataclass(frozen=True)
class Response:
    status: int | None
    content_type: str | None
    body: bytes
    error: str | None = None


Fetch = Callable[[str, str], Response]


def http_fetch(url: str, accept: str) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return Response(response.status, response.headers.get("Content-Type"), response.read(MAX_BODY))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers.get("Content-Type"), exc.read(MAX_BODY))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Response(None, None, b"", error=f"{type(exc).__name__}: {exc}")


def feed_links(body: bytes) -> list[Link]:
    """Links of Atom entries or RSS items, in feed order."""
    root = ET.fromstring(body)  # noqa: S314
    links: list[Link] = []
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        for el in entry.findall(f"{ATOM}link"):
            if el.get("href"):
                links.append(Link(title, el.get("href", ""), el.get("type", "")))
    for item in root.iter("item"):
        href = (item.findtext("link") or "").strip()
        if href:
            links.append(Link((item.findtext("title") or "").strip(), href, ""))
    return links


def envelope(source: str, url: str, kind: str, fetched_at: dt.datetime, response: Response) -> dict:
    try:
        body, encoding = response.body.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        body, encoding = base64.b64encode(response.body).decode("ascii"), "base64"
    return {
        "source": source,
        "url": url,
        "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
        "http_status": response.status,
        "kind": kind,
        "content_type": response.content_type,
        "error": response.error,
        "body_encoding": encoding,
        "body": body,
        "recorder_version": RECORDER_VERSION,
    }


def write_envelope(out: Path, record: dict) -> Path:
    fetched_at = dt.datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
    digest = hashlib.sha256(record["url"].encode()).hexdigest()[:12]
    directory = out / record["source"] / fetched_at.strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{fetched_at.strftime('%Y%m%dT%H%M%S%fZ')}-{record['kind']}-{digest}.json.gz"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(record, ensure_ascii=False).encode()))
    os.replace(tmp, path)
    return path


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)  # noqa: UP017


def record_source(source: Source, out: Path, fetch: Fetch = http_fetch, now: Callable[[], dt.datetime] = _now) -> dict:
    """Fetch one source's index and its new linked documents; return a summary."""
    response = fetch(source.url, source.accept)
    write_envelope(out, envelope(source.name, source.url, "index", now(), response))
    summary = {"source": source.name, "index_status": response.status, "docs": 0, "doc_errors": 0}
    if source.pick is None or response.status != 200:
        return summary
    try:
        links = [link for link in feed_links(response.body) if source.pick(link)]
    except ET.ParseError as exc:
        summary["parse_error"] = str(exc)
        return summary

    seen_path = out / source.name / "seen.txt"
    seen = set(seen_path.read_text().splitlines()) if seen_path.exists() else set()
    new = list(dict.fromkeys(link.href for link in links if link.href not in seen))[: source.max_docs_per_run]
    for href in new:
        doc = fetch(href, "*/*")
        write_envelope(out, envelope(source.name, href, "document", now(), doc))
        if doc.status == 200:
            seen.add(href)
            with seen_path.open("a") as fh:
                fh.write(href + "\n")
            summary["docs"] += 1
        else:
            summary["doc_errors"] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", nargs="*", help="source names to record (default: all)")
    args = parser.parse_args(argv)
    for source in SOURCES:
        if args.only and source.name not in args.only:
            continue
        try:
            print(json.dumps(record_source(source, args.out)), flush=True)
        except Exception as exc:  # one broken source must not stop the others  # noqa: BLE001
            print(json.dumps({"source": source.name, "crash": f"{type(exc).__name__}: {exc}"}), file=sys.stderr, flush=True)  # noqa: E501
    return 0


if __name__ == "__main__":
    sys.exit(main())
