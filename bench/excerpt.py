"""Cut the cancellation section an operator would paste out of a saved operator page.

    python bench/excerpt.py <page.html> <operator> <url> <accessed> <first-last> [...]

The page is converted to text (scripts and styles dropped, entities decoded, blank runs
collapsed); the line ranges, 1-based and inclusive, are taken from that text and joined. The
excerpt, its sha256 and its source are printed as JSON for `bench/fixtures/`. Full pages are
not committed (third-party content); they stay on the VM under ~/tidehold-bench/pages.
"""

import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

SKIP = {"script", "style", "noscript", "svg"}
BREAK = {"p", "br", "li", "h1", "h2", "h3", "h4", "div", "tr"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self.skipping += tag in SKIP
        if tag in BREAK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        self.skipping -= tag in SKIP

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.out.append(data)


def page_text(html: str) -> list[str]:
    parser = _Text()
    parser.feed(html)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(parser.out))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return [line.strip() for line in text.strip().split("\n")]


def main(argv: list[str]) -> int:
    page, operator, url, accessed, *ranges = argv
    lines = page_text(Path(page).read_text(encoding="utf-8", errors="replace"))
    picked: list[str] = []
    for spec in ranges:
        first, last = (int(n) for n in spec.split("-"))
        picked += lines[first - 1 : last]
    wording = "\n".join(line for line in picked if line)
    record = {
        "operator": operator,
        "url": url,
        "accessed": accessed,
        "lines": ranges,
        "sha256": hashlib.sha256(wording.encode()).hexdigest(),
        "wording": wording,
    }
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
