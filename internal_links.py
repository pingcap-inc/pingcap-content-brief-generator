#!/usr/bin/env python3
"""Build and query the PingCAP internal-link inventory."""

from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse
from xml.etree import ElementTree

DEFAULT_SITEMAP_URL = "https://www.pingcap.com/sitemap_index.xml"
DEFAULT_INVENTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sitemap_inventory.json"
)
ALLOWED_HOSTS = {"pingcap.com", "www.pingcap.com"}
MAX_SITEMAP_WORKERS = 6
MAX_CANDIDATE_POOL = 40
PRIMARY_KEYWORD_WEIGHT = 5
TITLE_WEIGHT = 4
URL_WEIGHT = 3
DESCRIPTION_WEIGHT = 1

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_NON_ENGLISH_PREFIXES = (
    "/cn/", "/zh/", "/zh-cn/", "/ja/", "/ko/", "/de/", "/fr/",
    "/es/", "/pt/", "/pt-br/",
)
_EXCLUDED_PATH_PARTS = (
    "/docs/", "/tag/", "/tags/", "/category/", "/categories/",
    "/author/", "/page/", "/feed/",
)
_HUB_PATHS = {
    "/ai/", "/blog/", "/case-studies/", "/compare/", "/developers/",
    "/resources/", "/solutions/", "/topics/",
}
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "database",
    "databases", "for", "from", "how", "in", "is", "of", "on", "or",
    "the", "to", "vs", "what", "when", "which", "with",
}


class _PageMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.h1 = ""
        self.meta_description = ""
        self.primary_keyword = ""
        self.publish_date = ""
        self.robots = []
        self.canonical_url = ""
        self._in_title = False
        self._in_h1 = False
        self._title_parts = []
        self._h1_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs if value is not None}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
        elif tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            content = (attrs.get("content") or "").strip()
            if name in {"description", "og:description"} and not self.meta_description:
                self.meta_description = content
            elif name in {"keywords", "primary_keyword"} and not self.primary_keyword:
                self.primary_keyword = content.split(",")[0].strip()
            elif name in {"article:published_time", "date", "datepublished"} and not self.publish_date:
                self.publish_date = content
            elif name in {"robots", "googlebot"} and content:
                self.robots.extend(
                    directive.strip().lower() for directive in content.split(",")
                )
        elif tag == "link":
            rel = (attrs.get("rel") or "").lower().split()
            if "canonical" in rel and not self.canonical_url:
                self.canonical_url = (attrs.get("href") or "").strip()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            self.title = _clean_text(" ".join(self._title_parts))
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.h1 = _clean_text(" ".join(self._h1_parts))

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_h1:
            self._h1_parts.append(data)


def _clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _get_response(url, timeout=30):
    import requests

    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing non-PingCAP URL: {url}")
    response = requests.get(
        url,
        headers={"User-Agent": "PingCAP-Content-Brief-Generator/1.0"},
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError(f"Expected HTTP 200 from {url}, got {response.status_code}")
    final_url = response.url or url
    final_parsed = urlparse(final_url)
    final_host = final_parsed.netloc.lower().split(":", 1)[0]
    if final_parsed.scheme not in {"http", "https"} or final_host not in ALLOWED_HOSTS:
        raise ValueError(f"Redirect left pingcap.com: {url} -> {final_url}")
    return response


def _get(url, timeout=30):
    return _get_response(url, timeout=timeout).text


def _xml_locations(xml_text):
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Malformed sitemap XML: {exc}") from exc
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name == "sitemapindex":
        return "index", [
            _clean_text(node.text)
            for node in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
            if node.text
        ]
    if root_name == "urlset":
        entries = []
        for node in root.findall("sm:url", _SITEMAP_NS):
            loc = node.findtext("sm:loc", default="", namespaces=_SITEMAP_NS).strip()
            lastmod = node.findtext("sm:lastmod", default="", namespaces=_SITEMAP_NS).strip()
            if loc:
                entries.append({"url": loc, "publish_date": lastmod})
        return "urls", entries
    raise ValueError(f"Unsupported sitemap root: {root_name}")


def is_eligible_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        return False
    if path.startswith(_NON_ENGLISH_PREFIXES):
        return False
    if any(part in path for part in _EXCLUDED_PATH_PARTS):
        return False
    if re.search(r"/page/\d+/?$", path):
        return False
    return True


def classify_page_type(url, source_sitemap=""):
    path = urlparse(url).path.lower()
    source = source_sitemap.lower()
    if "/case-study/" in path or "case-study" in source:
        return "case study"
    if path in _HUB_PATHS or path.rstrip("/") in {p.rstrip("/") for p in _HUB_PATHS}:
        return "hub"
    if "/compare/" in path or "comparison" in path:
        return "comparison"
    if "/blog/" in path or "/article/" in path or "post-sitemap" in source or "article-sitemap" in source:
        return "blog"
    if "guide" in path or "pillar" in path or "/topics/" in path:
        return "pillar"
    if "solution-sitemap" in source:
        return "pillar"
    return "page"


def discover_sitemap_pages(sitemap_url=DEFAULT_SITEMAP_URL):
    """Return filtered page records from a sitemap index or URL set."""
    try:
        sitemap_text = _get(sitemap_url)
    except Exception as exc:
        raise RuntimeError(f"Could not fetch root sitemap {sitemap_url}: {exc}") from exc
    sitemap_type, values = _xml_locations(sitemap_text)
    sitemap_urls = values if sitemap_type == "index" else [sitemap_url]
    pages = []
    seen = set()

    for child_url in sitemap_urls:
        if sitemap_type == "urls":
            child_entries = values
        else:
            try:
                child_type, child_entries = _xml_locations(_get(child_url))
            except Exception as exc:
                print(f"Skipping unavailable child sitemap {child_url}: {exc}")
                continue
            if child_type != "urls":
                continue
        for entry in child_entries:
            url = entry["url"]
            if url in seen or not is_eligible_url(url):
                continue
            seen.add(url)
            slug = urlparse(url).path.strip("/").split("/")[-1]
            pages.append({
                "url": url,
                "title": _clean_text(slug.replace("-", " ").title()),
                "h1": "",
                "meta_description": "",
                "primary_keyword": "",
                "page_type": classify_page_type(url, child_url),
                "publish_date": entry.get("publish_date", ""),
                "source_sitemap": child_url,
            })
        if sitemap_type == "urls":
            break
    return pages


def _fetch_page_metadata(page):
    enriched = dict(page)
    try:
        response = _get_response(page["url"], timeout=20)
        final_url = response.url or page["url"]
        if not is_eligible_url(final_url):
            raise ValueError(f"Final URL is not an eligible PingCAP content page: {final_url}")
        parser = _PageMetadataParser()
        parser.feed(response.text)
        robots = {directive.split(":", 1)[0].strip() for directive in parser.robots}
        x_robots_tag = response.headers.get("X-Robots-Tag", "").lower()
        robots.update(
            directive.split(":", 1)[0].strip()
            for directive in x_robots_tag.split(",")
            if directive.strip()
        )
        if "noindex" in robots or "none" in robots:
            raise ValueError("Page is marked noindex")
        page_label = f"{parser.title} {parser.h1}".lower()
        if re.search(r"\b404\b|page not found|not found", page_label):
            raise ValueError("Page appears to be a soft 404")
        if parser.canonical_url:
            canonical = urlparse(parser.canonical_url)
            canonical_host = canonical.netloc.lower().split(":", 1)[0]
            if canonical.scheme not in {"http", "https"} or canonical_host not in ALLOWED_HOSTS:
                raise ValueError(f"Canonical URL is outside pingcap.com: {parser.canonical_url}")
        if final_url != page["url"]:
            enriched["original_url"] = page["url"]
        enriched["url"] = final_url
        enriched["final_url"] = final_url
        enriched["status_code"] = response.status_code
        enriched["canonical_url"] = parser.canonical_url
        enriched["is_live"] = True
        enriched["indexable"] = True
        enriched["last_checked"] = datetime.now(timezone.utc).isoformat()
        enriched["title"] = parser.title or enriched["title"]
        enriched["h1"] = parser.h1
        enriched["meta_description"] = parser.meta_description
        enriched["primary_keyword"] = parser.primary_keyword
        if parser.publish_date:
            enriched["publish_date"] = parser.publish_date
    except Exception as exc:
        enriched["is_live"] = False
        enriched["indexable"] = False
        enriched["last_checked"] = datetime.now(timezone.utc).isoformat()
        enriched["validation_error"] = str(exc)
    return enriched


def build_sitemap_inventory(
    sitemap_url=DEFAULT_SITEMAP_URL,
    output_path=DEFAULT_INVENTORY_PATH,
    fetch_metadata=True,
    max_pages=None,
):
    pages = discover_sitemap_pages(sitemap_url)
    if max_pages:
        pages = pages[:max_pages]

    if fetch_metadata and pages:
        enriched = [None] * len(pages)
        with ThreadPoolExecutor(max_workers=MAX_SITEMAP_WORKERS) as executor:
            future_indexes = {
                executor.submit(_fetch_page_metadata, page): index
                for index, page in enumerate(pages)
            }
            for future in as_completed(future_indexes):
                enriched[future_indexes[future]] = future.result()
        valid_pages = []
        seen_final_urls = set()
        for page in enriched:
            if not page.get("is_live") or not page.get("indexable"):
                continue
            if page["url"] in seen_final_urls:
                continue
            seen_final_urls.add(page["url"])
            valid_pages.append(page)
        excluded_page_count = len(enriched) - len(valid_pages)
        pages = valid_pages
    else:
        excluded_page_count = 0

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sitemap_url": sitemap_url,
        "page_count": len(pages),
        "excluded_page_count": excluded_page_count,
        "pages": pages,
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(inventory, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return inventory


def load_internal_link_inventory(
    inventory_path=DEFAULT_INVENTORY_PATH,
    sitemap_url=DEFAULT_SITEMAP_URL,
):
    """Load the weekly inventory, falling back to live sitemap URLs only."""
    try:
        with open(inventory_path, encoding="utf-8") as handle:
            inventory = json.load(handle)
        pages = inventory.get("pages", [])
        if pages:
            return pages, "cached"
    except (OSError, ValueError, TypeError):
        pass
    return discover_sitemap_pages(sitemap_url), "live sitemap fallback"


def _tokens(value):
    return {
        token for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _candidate_score(page, topic_tokens):
    title_tokens = _tokens(f"{page.get('title', '')} {page.get('h1', '')}")
    keyword_tokens = _tokens(page.get("primary_keyword", ""))
    url_tokens = _tokens(urlparse(page.get("url", "")).path.replace("-", " "))
    description_tokens = _tokens(page.get("meta_description", ""))
    return (
        PRIMARY_KEYWORD_WEIGHT * len(topic_tokens & keyword_tokens)
        + TITLE_WEIGHT * len(topic_tokens & title_tokens)
        + URL_WEIGHT * len(topic_tokens & url_tokens)
        + DESCRIPTION_WEIGHT * len(topic_tokens & description_tokens)
    )


def select_internal_link_candidates(pages, topic, content_type, max_links=5):
    """Select up to five unique URLs using deterministic site-architecture rules."""
    topic_tokens = _tokens(topic)
    scored = []
    for page in pages:
        if page.get("is_live") is False or page.get("indexable") is False:
            continue
        score = _candidate_score(page, topic_tokens)
        if score <= 0:
            continue
        candidate = dict(page)
        candidate["relevance_score"] = score
        scored.append(candidate)
    scored.sort(key=lambda page: (-page["relevance_score"], page["url"]))
    pool = scored[:MAX_CANDIDATE_POOL]
    selected = []
    selected_urls = set()

    def choose(page_type, rule, limit=1):
        count = 0
        for page in pool:
            if page.get("page_type") != page_type or page["url"] in selected_urls:
                continue
            item = dict(page)
            item["selection_rule"] = rule
            selected.append(item)
            selected_urls.add(page["url"])
            count += 1
            if count >= limit or len(selected) >= max_links:
                break

    choose("pillar", "governing pillar page")
    choose("hub", "relevant topic hub")
    if content_type == "comparison":
        choose("comparison", "sibling comparison page", limit=2)

    for page in pool:
        if len(selected) >= max_links:
            break
        if page["url"] in selected_urls:
            continue
        item = dict(page)
        item["selection_rule"] = "highest remaining topical relevance"
        selected.append(item)
        selected_urls.add(page["url"])

    for index, item in enumerate(selected, start=1):
        item["slot"] = index
    return selected


def validate_internal_link_candidates(candidates):
    """Revalidate final candidates immediately before they enter the brief prompt."""
    validated = []
    seen_urls = set()
    for candidate in candidates:
        checked = _fetch_page_metadata(candidate)
        if not checked.get("is_live") or not checked.get("indexable"):
            continue
        if checked["url"] in seen_urls:
            continue
        seen_urls.add(checked["url"])
        validated.append(checked)
    for index, item in enumerate(validated, start=1):
        item["slot"] = index
    return validated


def main():
    parser = argparse.ArgumentParser(description="Build the PingCAP internal-link inventory")
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP_URL)
    parser.add_argument("--output", default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--skip-metadata", action="store_true")
    args = parser.parse_args()
    inventory = build_sitemap_inventory(
        sitemap_url=args.sitemap_url,
        output_path=args.output,
        fetch_metadata=not args.skip_metadata,
        max_pages=args.max_pages,
    )
    print(f"Wrote {inventory['page_count']} pages to {args.output}")


if __name__ == "__main__":
    main()
