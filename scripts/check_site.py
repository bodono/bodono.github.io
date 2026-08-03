#!/usr/bin/env python3
"""Validate local links and assets for the static site."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urldefrag, urlparse


ROOT = Path(__file__).resolve().parents[1]
HTML_ENTRYPOINT = ROOT / "index.html"
SKIP_SCHEMES = {"data", "http", "https", "javascript", "mailto", "tel"}
CSS_URL_RE = re.compile(r"url\(([^)]+)\)")
UMAMI_SCRIPT_URL = "https://cloud.umami.is/script.js"
UMAMI_WEBSITE_ID = "29f3bde0-f7b6-4a4b-8e43-43cb99121aa1"
UMAMI_ATTRIBUTES = {
    "data-domains": "bodono.github.io",
    "data-tag": "personal-site",
    "data-exclude-search": "true",
    "data-exclude-hash": "true",
    "data-do-not-track": "true",
}


@dataclass(frozen=True)
class Reference:
    source: Path
    line: int
    attr: str
    value: str


class SiteParser(HTMLParser):
    def __init__(self, source: Path):
        super().__init__()
        self.source = source
        self.anchors: set[str] = set()
        self.references: list[Reference] = []
        self.errors: list[str] = []
        self.umami_trackers: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {
            name.lower(): "" if value is None else value for name, value in attrs
        }
        for anchor_attr in ("id", "name"):
            if anchor := attrs_dict.get(anchor_attr):
                self.anchors.add(anchor)

        line, _ = self.getpos()
        for attr in ("href", "src", "poster"):
            if value := attrs_dict.get(attr):
                self.references.append(Reference(self.source, line, attr, value))

        if srcset := attrs_dict.get("srcset"):
            for candidate in srcset.split(","):
                url = candidate.strip().split(maxsplit=1)[0]
                if url:
                    self.references.append(Reference(self.source, line, "srcset", url))

        if tag == "img" and "alt" not in attrs_dict:
            self.errors.append(
                f"{self.source.relative_to(ROOT)}:{line}: img is missing alt text"
            )

        if tag == "script" and attrs_dict.get("src") == UMAMI_SCRIPT_URL:
            self.umami_trackers.append(attrs_dict)


def parse_html(path: Path) -> SiteParser:
    parser = SiteParser(path)
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def is_external(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in SKIP_SCHEMES or parsed.netloc)


def resolve_path(base: Path, value: str) -> Path | None:
    if is_external(value):
        return None
    path_part, _ = urldefrag(value)
    if not path_part:
        return None
    path_part = unquote(path_part)
    if path_part.startswith("/"):
        return (ROOT / path_part.lstrip("/")).resolve()
    return (base.parent / path_part).resolve()


def validate_reference(ref: Reference, html_cache: dict[Path, SiteParser]) -> list[str]:
    errors: list[str] = []
    _, fragment = urldefrag(ref.value)

    if is_external(ref.value):
        return errors

    target = resolve_path(ref.source, ref.value)
    if target is not None and not target.is_relative_to(ROOT):
        errors.append(
            f"{ref.source.relative_to(ROOT)}:{ref.line}: {ref.attr} points outside "
            f"the site root with {ref.value!r}"
        )
        return errors
    if target is not None and not target.exists():
        errors.append(
            f"{ref.source.relative_to(ROOT)}:{ref.line}: {ref.attr} points to missing "
            f"file {ref.value!r}"
        )
        return errors

    if not fragment:
        return errors

    anchor_file = target if target is not None else ref.source
    if anchor_file.suffix.lower() not in {"", ".html", ".htm"}:
        return errors
    if anchor_file.is_dir():
        anchor_file = anchor_file / "index.html"

    parser = html_cache.get(anchor_file)
    if parser is None:
        parser = parse_html(anchor_file)
        html_cache[anchor_file] = parser
    if fragment not in parser.anchors:
        errors.append(
            f"{ref.source.relative_to(ROOT)}:{ref.line}: {ref.attr} points to missing "
            f"anchor #{fragment}"
        )
    return errors


def iter_css_references(css_file: Path) -> list[Reference]:
    references: list[Reference] = []
    css_lines = css_file.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(css_lines, 1):
        for match in CSS_URL_RE.finditer(line):
            value = match.group(1).strip().strip("\"'")
            references.append(Reference(css_file, line_number, "url", value))
    return references


def validate_analytics(parser: SiteParser) -> list[str]:
    if len(parser.umami_trackers) != 1:
        return [
            "index.html: expected exactly one Umami tracker, found "
            f"{len(parser.umami_trackers)}"
        ]

    tracker = parser.umami_trackers[0]
    expected = {"data-website-id": UMAMI_WEBSITE_ID, **UMAMI_ATTRIBUTES}
    errors: list[str] = []
    if "defer" not in tracker:
        errors.append("index.html: Umami tracker must use defer")
    for attribute, value in expected.items():
        if tracker.get(attribute) != value:
            errors.append(
                f"index.html: Umami tracker must set {attribute}={value!r}"
            )
    return errors


def main() -> int:
    html_cache = {HTML_ENTRYPOINT: parse_html(HTML_ENTRYPOINT)}
    errors: list[str] = []
    errors.extend(validate_analytics(html_cache[HTML_ENTRYPOINT]))

    for parser in list(html_cache.values()):
        errors.extend(parser.errors)
        for ref in parser.references:
            errors.extend(validate_reference(ref, html_cache))

    for css_file in ROOT.glob("*.css"):
        for ref in iter_css_references(css_file):
            errors.extend(validate_reference(ref, html_cache))

    if errors:
        print("Static site validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Static site validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
