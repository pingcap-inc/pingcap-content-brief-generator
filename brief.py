#!/usr/bin/env python3
"""
Content Brief Generator for PingCAP
Generates SEO content briefs using DataForSEO, Claude AI, and Google Docs.

Usage:
    python brief.py "<topic>" <content_type>
    python brief.py "best database for AI agents" listicle

Content types: listicle, comparison, blog, product
"""

import sys
import os
import re
import json
import base64
import requests
from urllib.parse import urlparse
from html.parser import HTMLParser
from dotenv import load_dotenv
import anthropic
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from internal_links import (
    DEFAULT_INVENTORY_PATH,
    DEFAULT_SITEMAP_URL,
    load_internal_link_inventory,
    select_internal_link_candidates,
    validate_internal_link_candidates,
)

# ── Setup ─────────────────────────────────────────────────────────────────────

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_HAIKU_MODEL = os.getenv("ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5-20251001")
DATAFORSEO_LOGIN = os.getenv("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.getenv("DATAFORSEO_PASSWORD")
SEMRUSH_API_KEY = os.getenv("SEMRUSH_API_KEY")
PINGCAP_DOMAIN = os.getenv("PINGCAP_DOMAIN", "pingcap.com")
PINGCAP_SITEMAP_URL = os.getenv("PINGCAP_SITEMAP_URL", DEFAULT_SITEMAP_URL)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")
FOLDER_ID_FILE = os.path.join(SCRIPT_DIR, ".folder_id")
DRIVE_FOLDER_NAME = "Content Briefs"
SITEMAP_INVENTORY_FILE = os.getenv("SITEMAP_INVENTORY_FILE") or DEFAULT_INVENTORY_PATH

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

CONTENT_TYPES = ["listicle", "comparison", "blog", "product", "playbook", "solution"]


# ── API Cost Tracking ─────────────────────────────────────────────────────────

_api_costs = []


def record_api_cost(endpoint, response):
    """Extract and store cost info from a DataForSEO API response."""
    try:
        task = response.get("tasks", [{}])[0]
        cost = task.get("cost", 0)
        _api_costs.append({"endpoint": endpoint, "cost": cost})
    except (IndexError, KeyError, TypeError):
        _api_costs.append({"endpoint": endpoint, "cost": 0})


def print_cost_summary():
    """Print a summary of all DataForSEO API costs incurred during the run."""
    if not _api_costs:
        print("\nAPI Cost Summary: No API calls recorded.")
        return
    print("\n── API Cost Summary ────────────────────────────────────────────")
    total = 0
    for entry in _api_costs:
        cost = entry["cost"] or 0
        total += cost
        print(f"  {entry['endpoint']:55s}  ${cost:.4f}")
    print(f"  {'TOTAL':55s}  ${total:.4f}")
    print("────────────────────────────────────────────────────────────────\n")


# ── HTML Heading Extractor ────────────────────────────────────────────────────

class HeadingExtractor(HTMLParser):
    """Extracts H1, H2, H3 headings from raw HTML."""

    def __init__(self):
        super().__init__()
        self.headings = []
        self._current_tag = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3"):
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3") and self._current_tag == tag:
            text = "".join(self._current_text).strip()
            if text:
                self.headings.append({"tag": tag.upper(), "text": text})
            self._current_tag = None
            self._current_text = []

    def handle_data(self, data):
        if self._current_tag:
            self._current_text.append(data)


# ── DataForSEO Helpers ────────────────────────────────────────────────────────

def dataforseo_post(endpoint, payload):
    """POST to the DataForSEO API and return the parsed JSON response."""
    token = base64.b64encode(
        f"{DATAFORSEO_LOGIN}:{DATAFORSEO_PASSWORD}".encode()
    ).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }
    url = f"https://api.dataforseo.com/v3/{endpoint}"
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status_code") != 20000:
        raise RuntimeError(
            f"DataForSEO API error [{data.get('status_code')}]: {data.get('status_message')}"
        )
    return data


def get_keyword_data(topic):
    """
    Fetch search volume and keyword difficulty for the topic
    plus up to 10 related keywords.
    """
    payload = [
        {
            "keyword": topic,
            "location_code": 2840,  # United States
            "language_code": "en",
            "limit": 10,
        }
    ]
    data = dataforseo_post(
        "dataforseo_labs/google/related_keywords/live", payload
    )
    record_api_cost("dataforseo_labs/google/related_keywords/live", data)

    keywords = []
    try:
        task = data["tasks"][0]
        if not task.get("result"):
            print(f"    Warning: No keyword results returned")
            return keywords

        result = task["result"][0]

        # Seed keyword metrics (present on some plan tiers)
        seed_data = result.get("seed_keyword_data") or {}
        seed_info = seed_data.get("keyword_info") or {}
        seed_props = seed_data.get("keyword_properties") or {}
        keywords.append(
            {
                "keyword": topic,
                "search_volume": seed_info.get("search_volume", "N/A"),
                "competition_level": seed_info.get("competition_level", "N/A"),
                "keyword_difficulty": seed_props.get("keyword_difficulty", "N/A"),
                "is_seed": True,
            }
        )

        # Related keywords — each item may nest keyword_data or expose metrics directly
        for item in result.get("items", [])[:10]:
            kw = item.get("keyword_data") or item or {}
            kw_info = kw.get("keyword_info") or {}
            kw_props = kw.get("keyword_properties") or {}
            keyword_text = kw.get("keyword", "")
            if not keyword_text:
                continue
            keywords.append(
                {
                    "keyword": keyword_text,
                    "search_volume": kw_info.get("search_volume", "N/A"),
                    "competition_level": kw_info.get("competition_level", "N/A"),
                    "keyword_difficulty": kw_props.get("keyword_difficulty", "N/A"),
                }
            )
    except (KeyError, IndexError, TypeError) as exc:
        print(f"    Warning: Could not fully parse keyword data: {exc}")
        # Debug: print raw structure to help diagnose
        try:
            raw = data["tasks"][0].get("result")
            print(f"    Raw result structure: {json.dumps(raw, indent=2)[:500]}")
        except Exception:
            pass

    return keywords


def get_serp_and_paa(topic):
    """
    Fetch top 10 organic SERP results and People Also Ask questions
    for the topic in a single API call.
    """
    payload = [
        {
            "keyword": topic,
            "location_code": 2840,
            "language_code": "en",
            "device": "desktop",
            "os": "windows",
            "depth": 10,
            "load_async_ai_overview": True,
        }
    ]
    data = dataforseo_post("serp/google/organic/live/advanced", payload)
    record_api_cost("serp/google/organic/live/advanced", data)

    serp_results = []
    paa_questions = []
    serp_features = {"ai_overview": [], "featured_snippet": [], "status": "unavailable"}

    try:
        items = data["tasks"][0]["result"][0]["items"]
        if not isinstance(items, list):
            raise TypeError("SERP items are unavailable")
        serp_features["status"] = "returned"
        organic_count = 0
        for item in items:
            item_type = item.get("type")
            if item_type in {"ai_overview", "featured_snippet"}:
                serp_features[item_type].append(item)
            elif item_type == "organic" and item.get("is_featured_snippet"):
                serp_features["featured_snippet"].append(item)

            if item_type == "organic" and organic_count < 10:
                serp_results.append(
                    {
                        "rank": item.get("rank_group", organic_count + 1),
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                    }
                )
                organic_count += 1

            elif item_type == "people_also_ask":
                for paa in item.get("items", []):
                    if paa.get("type") == "people_also_ask_element":
                        question = paa.get("title", "").strip()
                        if question:
                            paa_questions.append(question)

    except (KeyError, IndexError, TypeError) as exc:
        print(f"    Warning: Could not fully parse SERP data: {exc}")

    return serp_results, paa_questions, serp_features


def get_llm_mentions(topic):
    """
    Fetch LLM mention data for the topic using the DataForSEO
    AI Optimization / LLM Mentions API.

    Returns a dict with:
      questions     - list of {question, answer} dicts
      sources       - list of cited source domains
      brands        - list of brand entities mentioned
      pingcap_mentioned - bool
      tidb_mentioned    - bool
      competitor_brands - list of competitor brand names found
    """
    payload = [
        {
            "keyword": topic,
            "location_code": 2840,
            "language_code": "en",
            "platform": "google",
            "limit": 20,
        }
    ]

    result = {
        "questions": [],
        "sources": [],
        "brands": [],
        "pingcap_mentioned": False,
        "tidb_mentioned": False,
        "competitor_brands": [],
    }

    try:
        data = dataforseo_post(
            "ai_optimization/llm_mentions/search/live", payload
        )
        record_api_cost("ai_optimization/llm_mentions/search/live", data)

        items = data.get("tasks", [{}])[0].get("result", [])
        if not items:
            return result

        all_text_parts = []
        seen_sources = set()
        seen_brands = set()

        for item in items:
            if not isinstance(item, dict):
                continue

            # Extract question/answer pairs
            question = item.get("question") or item.get("keyword") or ""
            answer = item.get("answer") or item.get("text") or ""
            if question:
                result["questions"].append({
                    "question": question,
                    "answer": answer[:500] if answer else "",
                })
                all_text_parts.append(question)
                all_text_parts.append(answer)

            # Extract cited sources
            for source in item.get("sources", []):
                domain = ""
                if isinstance(source, dict):
                    domain = source.get("domain") or source.get("url") or ""
                elif isinstance(source, str):
                    domain = source
                if domain and domain not in seen_sources:
                    seen_sources.add(domain)
                    result["sources"].append(domain)

            # Extract brand entities
            for brand in item.get("brands", []):
                name = ""
                if isinstance(brand, dict):
                    name = brand.get("name") or brand.get("brand") or ""
                elif isinstance(brand, str):
                    name = brand
                if name and name not in seen_brands:
                    seen_brands.add(name)
                    result["brands"].append(name)

        # Check for PingCAP/TiDB presence across all response text
        combined_text = " ".join(all_text_parts).lower()
        result["pingcap_mentioned"] = "pingcap" in combined_text
        result["tidb_mentioned"] = "tidb" in combined_text

        # Identify competitor brands (known database competitors)
        db_competitors = {
            "cockroachdb", "cockroach", "planetscale", "vitess",
            "yugabytedb", "yugabyte", "singlestore", "memsql",
            "alloydb", "aurora", "spanner", "cloud spanner",
            "neon", "supabase", "mongodb", "mysql", "postgresql",
            "postgres", "mariadb", "oracle", "sql server",
        }
        for brand in result["brands"]:
            if brand.lower() in db_competitors:
                result["competitor_brands"].append(brand)

    except (KeyError, IndexError, TypeError) as exc:
        print(f"    Warning: Could not fully parse LLM mentions data: {exc}")
    except RuntimeError as exc:
        print(f"    Warning: LLM mentions API error: {exc}")

    return result


def get_backlinks_data(competitor_urls):
    """
    Fetch backlink summary and top anchor texts for competitor URLs.

    Calls two DataForSEO Backlinks endpoints per URL (capped at 3 URLs):
      - backlinks/summary/live    — referring domains, total backlinks, domain rank
      - backlinks/anchors/live    — top 10 anchor texts by backlink count

    Returns a list of dicts, one per URL, with metrics + top anchors.
    Individual URL failures don't block others.
    """
    results = []

    for url in competitor_urls[:3]:
        entry = {
            "url": url,
            "referring_domains": 0,
            "total_backlinks": 0,
            "domain_rank": 0,
            "link_types": {},
            "top_anchors": [],
        }

        # ── Backlinks Summary ──
        try:
            summary_payload = [{"target": url, "internal_list_limit": 0}]
            summary_data = dataforseo_post(
                "backlinks/summary/live", summary_payload
            )
            record_api_cost("backlinks/summary/live", summary_data)

            summary_items = summary_data.get("tasks", [{}])[0].get("result", [])
            if summary_items:
                s = summary_items[0] if isinstance(summary_items, list) else summary_items
                entry["referring_domains"] = s.get("referring_domains", 0)
                entry["total_backlinks"] = s.get("backlinks", 0) or s.get("total_backlinks", 0)
                entry["domain_rank"] = s.get("rank", 0) or s.get("domain_rank", 0)
                entry["link_types"] = {
                    "dofollow": s.get("dofollow", 0),
                    "nofollow": s.get("nofollow", 0),
                    "redirect": s.get("redirect", 0),
                }
        except Exception as exc:
            print(f"    Warning: Backlinks summary failed for {url}: {exc}")

        # ── Backlinks Anchors ──
        try:
            anchors_payload = [
                {
                    "target": url,
                    "limit": 10,
                    "order_by": ["backlinks,desc"],
                }
            ]
            anchors_data = dataforseo_post(
                "backlinks/anchors/live", anchors_payload
            )
            record_api_cost("backlinks/anchors/live", anchors_data)

            anchor_items = anchors_data.get("tasks", [{}])[0].get("result", [])
            if anchor_items:
                items_list = anchor_items
                if isinstance(anchor_items, list) and len(anchor_items) > 0:
                    # Result may be nested: result[0].items[]
                    first = anchor_items[0]
                    if isinstance(first, dict) and "items" in first:
                        items_list = first.get("items", []) or []

                for anchor_item in items_list[:10]:
                    if isinstance(anchor_item, dict):
                        anchor_text = anchor_item.get("anchor", "")
                        backlinks_count = anchor_item.get("backlinks", 0)
                        if anchor_text:
                            entry["top_anchors"].append({
                                "anchor": anchor_text,
                                "backlinks": backlinks_count,
                            })
        except Exception as exc:
            print(f"    Warning: Backlinks anchors failed for {url}: {exc}")

        results.append(entry)

    return results


def extract_headings_from_url(url):
    """GET a URL and return its H1/H2/H3 headings."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        extractor = HeadingExtractor()
        extractor.feed(resp.text)
        return extractor.headings
    except Exception as exc:
        print(f"    Warning: Could not fetch {url} — {exc}")
        return []


def url_domain(url):
    """Return the host of an absolute URL without a leading "www.", or "" if unparseable."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


# ── SEMrush Helpers ───────────────────────────────────────────────────────────

def semrush_get(params, strict=False):
    """GET the SEMrush API with the given params dict and return parsed lines."""
    if not SEMRUSH_API_KEY:
        if strict:
            raise RuntimeError("SEMrush is not configured")
        return []
    params["key"] = SEMRUSH_API_KEY
    try:
        resp = requests.get("https://api.semrush.com/", params=params, timeout=15)
        resp.raise_for_status()
        text = resp.text.strip()
        if strict and (not text or (text.startswith("ERROR") and not text.startswith("ERROR 50"))):
            raise RuntimeError("SEMrush ranking report unavailable")
        if not text or text.startswith("ERROR"):
            print(f"    SEMrush warning: {text[:120]}")
            return []
        lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
        if len(lines) < 2:
            if strict:
                raise RuntimeError("SEMrush ranking report has no rows")
            return []
        aliases = {"Keyword": "Ph", "Search Volume": "Nq", "Position": "Po",
                   "URL": "Ur", "Keyword Difficulty": "Kd", "CPC": "Cp",
                   "Competition": "Co", "Intent": "In", "Intents": "In"}
        headers = [aliases.get(h.strip(), h.strip()) for h in lines[0].split(";")]
        rows = []
        for line in lines[1:]:
            if not line.strip():
                continue
            values = line.split(";")
            rows.append(dict(zip(headers, values)))
        return rows
    except Exception as exc:
        if strict:
            raise
        print(f"    SEMrush request failed: {exc}")
        return []


def get_semrush_keyword_intent(topic):
    """
    Fetch intent classification, volume, difficulty, and CPC for the primary keyword.
    Returns a dict with intent, search_volume, keyword_difficulty, cpc.
    Intent values: 0=informational, 1=navigational, 2=commercial, 3=transactional.
    """
    intent_labels = {
        "0": "informational", "1": "navigational",
        "2": "commercial", "3": "transactional",
    }
    rows = semrush_get({
        "type": "phrase_this",
        "phrase": topic,
        "database": "us",
        "export_columns": "Ph,Nq,Cp,Co,Kd,In",
        "display_limit": 1,
    })
    if not rows:
        return {}
    r = rows[0]
    raw_intent = r.get("In", "")
    return {
        "keyword": r.get("Ph", topic),
        "search_volume": r.get("Nq", "N/A"),
        "cpc": r.get("Cp", "N/A"),
        "competition": r.get("Co", "N/A"),
        "keyword_difficulty": r.get("Kd", "N/A"),
        "intent": intent_labels.get(raw_intent.strip(), raw_intent or "unknown"),
    }


def get_semrush_related_keywords(topic, limit=15):
    """
    Fetch semantically related keywords with volume and difficulty.
    Complements DataForSEO by returning a broader LSI keyword set.
    Returns a list of dicts with keyword, search_volume, keyword_difficulty, intent.
    """
    intent_labels = {
        "0": "informational", "1": "navigational",
        "2": "commercial", "3": "transactional",
    }
    rows = semrush_get({
        "type": "phrase_related",
        "phrase": topic,
        "database": "us",
        "export_columns": "Ph,Nq,Kd,In",
        "display_limit": limit,
        "display_sort": "nq_desc",
    })
    keywords = []
    for r in rows:
        raw_intent = r.get("In", "")
        keywords.append({
            "keyword": r.get("Ph", ""),
            "search_volume": r.get("Nq", "N/A"),
            "keyword_difficulty": r.get("Kd", "N/A"),
            "intent": intent_labels.get(raw_intent.strip(), raw_intent or "unknown"),
        })
    return keywords


def get_semrush_keyword_gap(topic, competitor_domains):
    """Compare relevant competitor keywords with PingCAP's recorded rankings."""
    if not competitor_domains:
        return []
    gap_keywords = []
    for domain in competitor_domains[:2]:
        rows = semrush_get({
            "type": "domain_organic",
            "domain": domain,
            "database": "us",
            "export_columns": "Ph,Po,Nq,Kd",
            "display_limit": 50,
            "display_sort": "nq_desc",
            "display_filter": "+|Po|Lt|11",
        })
        topic_words = set(topic.lower().split())
        for r in rows:
            kw = r.get("Ph", "").lower()
            if any(w in kw for w in topic_words if len(w) > 3):
                gap_keywords.append({
                    "keyword": r.get("Ph", ""),
                    "search_volume": r.get("Nq", "N/A"),
                    "keyword_difficulty": r.get("Kd", "N/A"),
                    "competitor_domain": domain,
                    "competitor_position": r.get("Po", "N/A"),
                })
    gap_keywords.sort(key=lambda x: int(x["search_volume"]) if str(x["search_volume"]).isdigit() else 0, reverse=True)
    opportunities = []
    seen = set()
    for candidate in gap_keywords:
        keyword = candidate["keyword"]
        if keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        if len(seen) > 20:
            break
        candidate.update(check_pingcap_ranking(keyword))
        if candidate["classification"] != "existing coverage":
            opportunities.append(candidate)
    return opportunities


def get_semrush_domain_authority(competitor_urls):
    """
    Fetch authority score, organic traffic estimate, and keyword count
    for the domains of the top competitor URLs.
    Returns a list of dicts with domain, authority_score, organic_traffic, organic_keywords.
    """
    results = []
    seen_domains = set()
    for url in competitor_urls[:3]:
        domain = url_domain(url)
        if not domain:
            continue
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        rows = semrush_get({
            "type": "domain_ranks",
            "domain": domain,
            "database": "us",
            "export_columns": "Dn,Rk,Or,Ot,Oc,Ad",
            "display_limit": 1,
        })
        if rows:
            r = rows[0]
            results.append({
                "domain": domain,
                "authority_score": r.get("Rk", "N/A"),
                "organic_keywords": r.get("Or", "N/A"),
                "organic_traffic_est": r.get("Ot", "N/A"),
                "paid_keywords": r.get("Ad", "N/A"),
            })
    return results



# ── Claude ────────────────────────────────────────────────────────────────────

def summarize_title(topic):
    """Use Claude to distill a long topic prompt into a clean 6-8 word doc title."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_HAIKU_MODEL,
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": (
                f"Convert this content topic into a clean, specific 6-8 word title "
                f"suitable for a Google Doc filename. No punctuation, no quotes, "
                f"just the title. Topic: {topic}"
            )
        }]
    )
    return message.content[0].text.strip()


# Files to skip when loading brief examples
_SKIP_FILES = {"requirements.txt", "feedback.txt", "CLAUDE.md"}


def load_brief_examples():
    """
    Read all .txt and .md files from the project root and docs/examples/
    (except skipped files) and return them formatted as labelled reference examples.
    """
    examples = []
    scan_dirs = [
        SCRIPT_DIR,
        os.path.join(SCRIPT_DIR, "docs", "examples"),
    ]

    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        try:
            for fname in sorted(os.listdir(scan_dir)):
                if not (fname.endswith(".txt") or fname.endswith(".md")) or fname in _SKIP_FILES:
                    continue
                fpath = os.path.join(scan_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        content = fh.read().strip()
                    if content:
                        examples.append(f"### Example: {fname}\n\n{content}")
                except Exception as exc:
                    print(f"    Warning: could not read {fname}: {exc}")
        except Exception as exc:
            print(f"    Warning: could not scan {scan_dir}: {exc}")

    return "\n\n---\n\n".join(examples)


def load_feedback():
    """Return the contents of feedback.txt, or an empty string if missing."""
    fpath = os.path.join(SCRIPT_DIR, "feedback.txt")
    if not os.path.exists(fpath):
        return ""
    try:
        with open(fpath, encoding="utf-8") as fh:
            text = fh.read().strip()
        # Return only meaningful lines (ignore header comments)
        lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
        return "\n".join(lines)
    except Exception:
        return ""


_BASE_INSTRUCTIONS = """
You are an expert SEO content strategist for PingCAP (pingcap.com), the company
behind TiDB — an open-source, distributed SQL database that supports hybrid
transactional and analytical processing (HTAP) workloads at massive scale.

Your task: produce a fully populated PingCAP content brief in Markdown.
The reference examples provided are the gold standard — match them exactly
in depth, tone, heading rationale quality, and entity specificity. The explicit
output format and current rules below take precedence over outdated example formats.

---

## Required Output Format

Produce the brief in this exact order. Every section must be fully written —
no placeholders, no "TBD", no skeleton text. Explicit missing-evidence notices
and omission of conditional sections are required when data is unavailable.
Use the exact section names below as Markdown headings. End Outline / Headings
before Schema Markup Recommendations; visual notes belong inside each outline H2.

---

### Meta Elements

Present as a single 2-column markdown table with exactly these rows.
Do NOT output Entity Recognition Focus or Relevant LLM Queries as separate
sections — they are rows inside this table.

| Meta Elements | |
|---|---|
| Target Keyword | [the single primary keyword] |
| Supporting Keywords | kw1<br>kw2<br>kw3<br>... (10–15 specific supporting keywords, one per line using <br>) |
| Total MSV | [sum of all keyword search volumes] |
| Entity Recognition Focus | entity1<br>entity2<br>... (10–15 exact named technical entities — specific product names, algorithm names, protocol names, architecture patterns, database-specific concepts — that must co-occur on this page for LLM knowledge graph association. Must be specific and technical, e.g. "Raft consensus", "TiKV", "TiFlash", "MVCC", "PD placement driver", "two-phase commit". Generic terms like "scalability" or "performance" are not acceptable. Use <br> between entities.) |
| Relevant LLM Queries | query1<br>query2<br>... (4–5 specific, realistic prompts a user might type into ChatGPT, Claude, or Gemini that this page should appear in or authoritatively answer. Use <br> between queries.) |
| Meta Title | [<=60 characters, must include the target keyword. Do NOT include a year — titles with years date quickly and require constant maintenance.] |
| Meta Description | [<=155 characters] |
| URL Structure | [For comparison content: /compare/[slug]/ e.g. /compare/tidb-vs-postgresql/ — For blog: /blog/[slug]/ — For solution: /solutions/[slug]/ — For other types: /article/[slug]/] |

Use <br> for line breaks within multi-value cells (Supporting Keywords,
Entity Recognition Focus, Relevant LLM Queries). Do NOT use separate sections
for Entity Recognition Focus or Relevant LLM Queries anywhere in the brief.

---

### Page Goal

Write 3–5 sentences describing what the reader should believe after
reading, what action they should take, and how this content strengthens
TiDB/PingCAP's entity association in LLMs and search engines for the target
keyword cluster. Put role, company type, evaluation stage, and decision driver
only in the separate Target Audience section.

---

### Target Audience

  Write a focused 3–5 sentence paragraph that names:
  - The specific job titles or roles (e.g. "Senior engineers, platform architects, and database leads")
  - The company type and scale (e.g. "at high-growth SaaS companies, fintech platforms, or AI-native startups")
  - The evaluation stage they are at (e.g. "who are actively evaluating distributed SQL solutions after hitting MySQL scaling limits")
  - What drives their decision (the primary technical or business concern they are trying to resolve)

  Where relevant, reference PingCAP persona research: TiDB adoption is typically initiated
  by highly technical stakeholders — senior engineers and engineering leaders — who care
  most about scalability, high availability, MySQL compatibility, and lower system
  complexity. Tailor the description to the specific topic and content type rather than
  copying this verbatim.

  Do NOT merge this section with Page Goal. They are separate outputs.

---

### Technical Notes

Bullet list of SEO and Core Web Vitals requirements specific to this content type:
- Only one H1 (page title)
- Sequential H2 -> H3 hierarchy — no skips
- Natural inclusion of semantically related keywords
- Exact schema markup types to implement (e.g. FAQPage, ItemList,
  HowTo, BreadcrumbList, SoftwareApplication, TechArticle, VideoObject) —
  match to actual sections on the page
- Interactive elements (tabs, comparison widgets) with lazy loading
- Alt text for all visuals that are not CSS background images
- Any content-type-specific requirements (e.g. sticky TOC for playbooks,
  comparison table for listicles)

---

### Writer Guardrails

These are mandatory editorial standards the writer must follow before publication:
- **Benchmark data**: Must include year, test conditions, and verifiable source. Drop any benchmark that cannot be attributed — do not use unverifiable speed claims.
- **Pricing claims**: Use publicly available numbers with source URLs. If exact pricing cannot be cleanly compared, compare models only (e.g. open-source vs. managed vs. enterprise).
- **Review ratings**: If citing G2, Capterra, or Trustpilot scores, include the source URL and verify the rating is current before publication.
- **Internal links**: Use only verified pingcap.com URLs. Do not guess or invent paths.
- **Competitor claims**: Any limitation attributed to a competitor must be factual and attributable — no editorialising.
- **Product positioning**: Avoid generic product praise. Ground all TiDB positioning in specific capabilities, architecture facts, or customer proof points.

---

### Internal Links

Present up to five recommendations as a Markdown table in this exact format:

| Section (H2) | Anchor text | Target URL | Why |
|--------------|-------------|------------|-----|

Use only URLs from the Verified Internal Link Candidates supplied in the research
data. Never invent, alter, or guess a URL. If no candidates were supplied, write
"No verified internal link candidates returned — refresh the sitemap inventory"
instead of creating links.

Map every link to the exact text of a named H2 in the outline. Use each target URL
only once, place no more than two links in any H2, and keep anchor text descriptive
and natural rather than exact-match stuffed. Preserve the deterministic slot purpose
shown in each candidate's selection_rule field. The Why column must explain in one
sentence how the target supports that specific H2. Treat this table as a recommendation
for editorial approval before publication, not as automatic link insertion.

---

### LLM Visibility Snapshot

Using the LLM mentions data provided, produce:

- **AI Search Presence**: How many AI-generated responses mention this topic area,
  and which platforms surface results (Google AI Overviews, ChatGPT, etc.)
- **Cited Sources / Domains**: Which domains are most frequently cited in AI
  responses for this topic. Note any patterns (docs sites, tutorials, comparisons).
- **PingCAP / TiDB Visibility**: Whether PingCAP or TiDB currently appears in AI
  responses. If yes, note the context. If no, note the gap.
- **Competitor Brand Mentions**: Which competitor brands appear in AI responses.
  Note their positioning and frequency.
- **Recommended Content Angle for LLM Citability**: 2–3 sentences on how to
  structure this content so AI systems are more likely to cite it — e.g.
  definitive answers, structured data, FAQ format, authoritative comparisons.

If no LLM mentions data was provided: write "No data returned — manual review
recommended."

---

### Link Landscape & Acquisition Angle

Using the backlinks data provided, produce:

- **Competitor Backlink Comparison Table**: A table showing each competitor URL
  analyzed, their referring domains count, total backlinks, domain rank, and
  dofollow/nofollow ratio.
- **Anchor Text Patterns**: Summarize the most common anchor text themes across
  competitors. Note branded vs. generic vs. keyword-rich anchors.
- **Link Acquisition Strategies**: 2–3 specific, actionable strategies for
  acquiring backlinks to this content (e.g. data-driven outreach, resource page
  inclusion, guest posting on complementary sites, creating linkable assets).
- **Difficulty Flag**: If any competitor has 500+ referring domains, flag this
  as a high-competition topic and note that link building will require sustained
  effort.

If no backlinks data was provided: write "No data returned — manual review
recommended."

---

### Outline / Headings

THIS IS THE MOST IMPORTANT SECTION. It must account for at least 50% of the
total brief word count. Be exhaustive. Every heading in the content must appear
here with full guidance.

#### Before the heading list, write two blocks: a SERP competitor table and an AI Overview patterns analysis

**Block 1 — Top ranking pages table**

Using the SERP results provided in the research data, produce a table of the top
ranking pages for the primary keyword. Format exactly as follows:

| # | Page | Key Angle |
|---|------|-----------|
| [rank] | [page title / domain] | [1–2 sentence description of what this page covers and what angle it takes — be specific, not generic] |

Include up to 5 rows — only pages that are genuinely topically relevant to the
primary keyword. If fewer than 5 relevant pages are in the SERP data, include only
those that are relevant and note the others as "not topically relevant — excluded".
If SERP data is empty or unavailable, write:
"No SERP data returned — manual review recommended before writing. Run DataForSEO
with the primary keyword to populate this table before briefing a writer."

Do not skip this table even when SERP data is sparse — a partial table with a note
is more useful to the writer than omitting it entirely.

**Block 2 — Patterns Favored by AI Overviews & LLMs**

This is a writer-facing orientation section (not for publication). Based on the
SERP data, LLM mentions data, and competitor headings provided, write 4–6 bullets:
- What the AI Overview leads with for this keyword (first 100 words pattern)
- Which comparison entities appear repeatedly across the top-ranking pages
- What content structures earn featured snippets (tables, definition blocks, FAQs)
- Any content gaps the top-ranking pages miss that TiDB can own
- Editorial warnings specific to this topic (e.g. "benchmark claims require dated sources")

Ground this in the actual SERP and competitor data provided — do not invent patterns.
Only describe AI Overview wording or snippet ownership when the supplied SERP Features
contain that evidence. Empty arrays or missing text mean evidence unavailable: say
"AI Overview data unavailable" or "Featured-snippet data unavailable" as applicable.
Organic page structures may support editorial recommendations, not claims about
which structures earned a snippet. A feature not returned is not proof of absence.
If no SERP data is available, write "Insufficient SERP data — manual review recommended"
for this block and move on. Do not omit the block entirely.

---

#### Heading structure rules

- Maximum 10 H2 sections, except listicles which allow up to 12.
  Keep the AEO answer and named mechanism requirements within the existing sections.
  For listicles, the quick answer is the AEO answer; name the mechanism in the
  TiDB spotlight H2. For solutions, adapt the solution-positioning H2 to satisfy both.
- H2 headings should mirror actual search intent phrasing where SERP data supports it
  (e.g. "Which database performs better as workloads grow?" not "Performance Comparison").
- For comparison and listicle content types, the SECOND H2 must be an "at a glance"
  section titled "[Topic] at a glance" with a neutral 6–8 row comparison table
  covering: architecture, scaling model, high availability, analytics, ecosystem,
  deployment, and best fit. This is mandatory for comparison and listicle types.
- Each H2 must represent a distinct stage in the buyer's evaluation journey.
  Merge any H2 that overlaps in scope with an adjacent H2.

- AEO answer H2 (mandatory for all content types): Every brief must include one H2
  positioned in the first third of the outline whose heading directly answers the primary
  keyword's core question or search intent (e.g. if the keyword is "AI agent memory",
  the H2 might be "What is AI agent memory and why do agents lose it?"). The first 2–3
  sentences under this H2 must be written as a self-contained, extractable answer —
  structured so an AI Overview or featured snippet can lift them verbatim. Check the
  SERP snapshot in the research data: if a competitor already holds the featured snippet
  for this query, note it in the Inline Content Guidance and instruct the writer to
  provide more depth and specificity, not just matching coverage.
  A brief missing this AEO answer H2 is a failure.
- Named mechanism H2 (mandatory for all content types): Every brief must include one
  H2 that explicitly closes the loop between the problem raised in the intro and TiDB's
  specific mechanism that solves it. The heading must name the actual mechanism — not
  "How TiDB helps" (too vague) but "How TiDB's Multi-Raft architecture eliminates
  cross-shard transaction overhead" or equivalent. Valid named mechanisms include:
  Raft consensus, Multi-Raft, TiKV, TiFlash, PD placement driver, HTAP, MVCC,
  two-phase commit, online DDL, per-agent isolation. The Inline Content Guidance for
  this section must instruct the writer to name the mechanism in the first sentence and
  connect it directly to the problem named in the intro — not describe it as a general
  capability.
  A brief missing this named mechanism H2 is a failure.

---

The outline must have a clear narrative arc from start to finish:
- The intro establishes the decision stakes and the reader's problem
- Each H2 builds on the previous one — problem → framework → evaluation → decision
- The closing section resolves the tension set up in the intro with a concrete next step
- Transitions between major sections must be implicit in the Inline Content Guidance
  (e.g. "End this section by signalling that the next section provides the framework
  for evaluating these differences"). The writer should never feel a section is
  disconnected from what came before.

For every heading, provide all of the following:

Write the heading as actual markdown — ## for H2, ### for H3, #### for H4.
Do NOT write "Recommended Heading:" as a label. Just write the heading directly.
**Current Heading** (for content refreshes only): the existing heading being replaced.
Omit this field entirely for new content.
**Target word count**: Include a specific word count range for this section in the
format "Target: ~X–Y words". Distribute the total word count proportionally.
For listicles use the supplied deterministic section budgets. They include FAQs
and the introduction; H3 budgets are subdivisions of H2 budgets, not additional words.
For other types, distribute the selected total across all sections, including the H1
introduction. Fixed ranges below are relative weighting guidance only: scale them to
the selected total. The sum of top-level section targets must fit the selected tier.
**Rationale**: Exactly 2 sentences — no more. Sentence 1: cover search intent (which query pattern this heading captures and why this phrasing wins over alternatives). Sentence 2: cover one of — LLM entity co-occurrence (which named entities this surfaces and why), buyer evaluation logic (what the evaluator must believe at this stage), or semantic positioning (how this claims a gap competitors miss). Generic rationales (“improves SEO”, “adds keyword”) are not acceptable.

### Verified customer proof points

When the brief requires a customer case study, concrete proof point, or social proof reference, use ONLY customers from this verified list. Never write anonymized placeholders ("a leading global online travel agency", "a multinational financial services firm", etc.) — if nothing from this list fits the topic, omit the case study slot from the outline entirely rather than fabricating or anonymizing.

Verified customers with confirmed pingcap.com case study URLs:

- Flipkart — [https://www.pingcap.com/case-study/flipkart-transforming-database-management-and-reducing-complexity-with-tidb/](https://www.pingcap.com/case-study/flipkart-transforming-database-management-and-reducing-complexity-with-tidb/)
- MNC Bank — [https://www.pingcap.com/case-study/mnc-bank-supercharges-performance-with-tidb/](https://www.pingcap.com/case-study/mnc-bank-supercharges-performance-with-tidb/)
- Zhihu — [https://www.pingcap.com/blog/from-plan-to-execution-zhihus-guide-to-petabyte-scale-tidb-database-migration/](https://www.pingcap.com/blog/from-plan-to-execution-zhihus-guide-to-petabyte-scale-tidb-database-migration/)
- Rakuten — [https://www.pingcap.com/blog/revolutionizing-loyalty-programs-rakuten-distributed-sql/](https://www.pingcap.com/blog/revolutionizing-loyalty-programs-rakuten-distributed-sql/)
- Kimi — [https://www.pingcap.com/case-study/kimi-2-6-agent-hosting-platform-tidb-cloud/](https://www.pingcap.com/case-study/kimi-2-6-agent-hosting-platform-tidb-cloud/)
- Manus — [https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/](https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/)
- Trip.com — [https://www.pingcap.com/case-study/trip-com-boosts-real-time-data-processing-and-financial-settlement-with-tidb/](https://www.pingcap.com/case-study/trip-com-boosts-real-time-data-processing-and-financial-settlement-with-tidb/)

Customers likely to have case studies (use only if verified source material is supplied in the research; the generator has no browsing tool):
Pinterest, Plaid, CardX, Catalyst, WeBank, Tuya, Bolt, Mercari, Dify

**Inline Content Guidance**: After the rationale, provide specific writer
instructions for this section's body copy. Include ALL of the following that
apply: the exact argument to make, which TiDB capability or customer proof
point to reference, which named technical entities to use, what the reader
should conclude after this section, specific data points or statistics to find,
and any suggested visuals, diagrams, code examples, or interactive elements.
This is where ALL "key points", "proof points", "data to find", "examples",
and "visual suggestions" live — do NOT put them anywhere else.

For listicle content type, the outline must follow this exact H2 sequence:

**H2 1 — Quick answer box** (Fix 2)
Title: "Quick answer: Which [topic] is best for your use case?"
Target: use the corresponding supplied section budget.
Content: 5–7 use-case winners with one-line justifications (e.g. "Best for
customer-facing dashboards", "Best distributed SQL option"). TiDB must appear
as the recommended option for its primary use case. Include jump links to the
comparison table, framework section, methodology, vendor reviews, how-to-choose,
FAQs, and next steps.

**H2 2 — At a glance comparison table**
Title: "Compare the best [topic] at a glance"
Target: use the corresponding supplied section budget.
Content: 7–8 row comparison table. Columns must include: Database, Best for,
key technical criteria relevant to the topic, Architecture type, Deployment
model, Key tradeoff, Getting started. Place the primary CTA immediately after
this table.

**H2 3 — How to read the table**
Title: "How should you read this [topic] table?"
Target: use the corresponding supplied section budget.
Content: 3–5 bullets mapping common buyer needs to the right tool category.
Call out when a single-tool approach is not enough.

**H2 4 — Unifying evaluation framework** (Fix 3)
Title: "What framework separates a great [category] from a weak one?"
Target: use the corresponding supplied section budget.
Content: Define exactly 3 evaluation criteria in under 150 words total (e.g.
freshness, concurrency, workload breadth — or compatibility depth, scaling path,
workload breadth). These 3 criteria MUST be reused as column headers in the
comparison table and referenced in every vendor review. This gives the article
a consistent analytical spine. Each criterion gets its own H3.

**H2 5 — Methodology with conflict disclosure** (Fix 4)
Title: "How we chose the best [topic]"
Target: use the corresponding supplied section budget.
Content: Explain selection scope, what was excluded, and evaluation criteria.
MUST include this exact conflict disclosure: "PingCAP is the publisher of this
content. TiDB appears on this list because it meets the same evaluation criteria
applied to all other products reviewed." Note that human must verify G2, Capterra,
Clutch, pricing, and benchmark details before publication.

**H2 6 — Benchmark checklist** (Fix 5)
Title: "How should you benchmark a [category] for your workload?"
Target: use the corresponding supplied section budget.
Content: A practical benchmark checklist with 6–8 specific test parameters
relevant to the topic (e.g. p95/p99 latency, concurrency under load, failover
behavior, data freshness lag). All performance numbers must include year,
methodology, and source — remove any stat without a year. Use 3 H3s covering
workload definition, performance measurement, and operational cost.

**H2 7 — Vendor reviews**
Title: "Best [topic] reviewed"
Target: use the corresponding supplied section budget.
Content: Use the same 7-part vendor template for every entry:
  - **Best for**: one-sentence positioning statement
  - **Why it's on the list**: 2–3 sentences on why this product fits the topic
  - **Key features**: 3–5 bullet points of relevant capabilities
  - **Pros**: 2–4 bullet points
  - **Cons / tradeoffs**: 2–3 bullet points (be honest)
  - **Pricing**: current pricing model (note if self-serve vs. enterprise-only)
  - **Getting started**: one sentence pointing to docs, trial, or quickstart
  - **Framework score**: After the 7-part template, add a one-sentence explicit
    score against each of the 3 evaluation criteria defined in H2 4. Format:
    "[Criterion 1]: [one-sentence verdict]. [Criterion 2]: [one-sentence verdict].
    [Criterion 3]: [one-sentence verdict]." This ties every vendor review back
    to the unifying framework and gives the article analytical consistency.
For each listed product, include exact G2, Capterra, or Clutch review links
only if live pages are verified by a human before publication.

**H2 8 — TiDB spotlight** (Fix 6 — when is TiDB the best choice)
Title: "How TiDB’s [named mechanism] solves [the introductory problem]"
Target: use the corresponding supplied section budget.
Content: Map 3 specific use cases to TiDB with concrete fit reasons. Use a customer example or case study only when relevant verified evidence
is supplied. Otherwise omit that example and explain the technical fit. Each use case gets an H3.

**H2 9 — 4-step decision framework** (Fix 6)
Title: "How do you choose the right [category]?"
Target: use the corresponding supplied section budget.
Content: Four H3 steps: (1) Define use case and workload, (2) Match requirements
to architecture, (3) Plan for growth and scale, (4) Validate ecosystem and
operational needs. Help readers decide when a simpler option is enough vs when
distributed SQL is the right long-term path.

**H2 10 — Architecture patterns** (Fix 7)
Title: "What architecture patterns work best for [category]?"
Target: use the corresponding supplied section budget.
Content: Compare 3 common deployment patterns relevant to the topic. For each:
name the pattern, describe when it applies, and name the main pitfall to avoid.
Each pattern gets an H3.

**H2 11 — Closing CTA section** (Fix 8)
Title: "Ready to evaluate a [category]?" or equivalent
Target: use the corresponding supplied section budget.
Content: One primary CTA only, aligned to the priority page. MUST include a
short editorial policy box covering: how vendors were selected, how often the
page is updated, how claims are validated, and how conflicts are disclosed.

**H2 12 — FAQs**
Title: "[Topic] FAQs"
Target: use the supplied FAQ budget. If PAA is empty, omit this H2 and
redistribute its budget across the other sections; do not invent questions.
Content: Answer each PAA question directly. Answer by use case, not with a
single universal winner. Keep answers factual and unbiased.

TiDB must be featured as a full vendor entry in every listicle, with the same
7-part structure applied. TiDB should be positioned as the recommended choice
for teams needing MySQL compatibility + horizontal scale + HTAP.
TiDB vector search must be noted as beta (public beta) wherever mentioned.

Note: The 10 H2 maximum rule is relaxed for listicle content type to accommodate
this required structure. Listicles may have up to 12 H2 sections.

---

For solution content type, the outline must follow this exact structure.
Solution pages are conversion-focused product pages targeting a specific
industry, persona, or use case. They are NOT educational blog posts — every
section must move the reader toward a demo, trial, or consultation.

**Solution page audience:** Always specify 2–3 named roles (e.g. "CTOs,
platform engineers, and data leads at fintech companies"). The page goal
must state what the reader should believe, what action they should take,
and how this page strengthens TiDB's entity association for the target vertical.

**Solution page narrative arc:**
Hero → Pain → Solution → Use Cases → Architecture → Social Proof →
Adoption Journey → Resources → Closing CTA

**H1 — Value proposition headline**
Format: "[TiDB capability] for [industry/use case]" or "[Outcome] with TiDB"
Must incorporate the target keyword. Include above-the-fold guidance:
- Intro video block (60–90s explainer animation or demo)
- Single-sentence positioning statement under the headline
- 3–4 key value prop bullets (capability + outcome format, not feature lists)
- Primary CTA button ("Start Free on TiDB Cloud" or equivalent)
- Secondary CTA ("Talk to a Solutions Architect")
Target: ~120–180 words above the fold.

**H2 1 — Pain / problem framing**
Title pattern: "Why [legacy approach] breaks down for [use case]"
Target: ~220–300 words.
Content: 3 H3s, each naming a specific pain point with business impact.
Structure each H3 as: pain name → what causes it → what it costs the business.
Pain must be specific to the target vertical — no generic "legacy databases
are slow" statements. Reference actual failure modes (e.g. "batch ETL kills
real-time visibility", "manual sharding fragments logistics data").
Rationale: 2 sentences — search intent captured + urgency established.

**H2 2 — TiDB solution positioning**
Title pattern: "TiDB: [specific capability] for [use case]"
Target: ~260–340 words.
Content: 3–4 H3s, each mapping a TiDB architectural capability to the
vertical pain. Name specific technical entities: TiKV, TiFlash, Raft
consensus, HTAP, PD placement driver, MVCC. Each H3 ends with a concrete
outcome the buyer can expect (metric, capability, or architectural benefit).
Do NOT list generic features — connect every capability to the vertical.
Rationale: 2 sentences.

**H2 3 — Use cases**
Title pattern: "Solution use cases: [specific scenarios for this vertical]"
Target: ~300–400 words.
Content: 3–4 H3s, each a named use case with:
- One-sentence description of the scenario
- What TiDB enables in this scenario (specific capability)
- A per-use-case CTA (e.g. "See a [use case] demo")
Use cases must be specific to the vertical, not generic ("real-time inventory
management", not "real-time analytics"). Each use case should connect to a
buying scenario and ideally map to a longer-form playbook that could be built.
Rationale: 2 sentences.

**H2 4 — Architecture (technical evaluator section)**
Title pattern: "Architecture: [how TiDB enables the vertical capability]"
Target: ~240–320 words.
Content: 3 H3s covering the data flow:
- H3 1: Ingest — how data enters TiDB (streaming, batch, CDC, Kafka)
- H3 2: Unified store — how TiDB eliminates the need for a separate warehouse
- H3 3: Query and serve — how analytics and operational queries run together
Use at most one shared architecture diagram for these three H3s. Include sample SQL queries or code
snippets relevant to the vertical. This section is for architects and
engineers — be technically specific.
Rationale: 2 sentences.

**H2 5 — Social proof / customer evidence** (conditional on relevant verified evidence)
Title pattern: "Proven [vertical] results: customers and social proof"
Target: ~220–300 words.
Content: Include only the H3s supported by supplied evidence; omit the entire
section if no relevant verified customer material is available. Never invent metrics.
A roster URL alone is not evidence for a customer metric or use-case fit.
- H3 1: Named customer story with metrics (throughput, latency, cost reduction,
  downtime eliminated). Link to full case study. Do NOT invent metrics —
  use verified pingcap.com case study data only.
- H3 2: Second customer vignette (before/after architecture, 2–3 sentences)
- H3 3: Logos, pull quotes, and third-party recognition (G2, Gartner Peer
  Insights, awards). Verify all ratings before publication.
Rationale: 2 sentences.

**H2 6 — Interactive tools** (include when vertically relevant)
Title pattern: "Interactive tools: explore [scale/cost/latency] for your [vertical]"
Target: ~180–240 words.
Content: 2–3 H3s covering interactive elements:
- Capacity / latency estimator (slider for events/second, regions, data volume)
- Cost comparison widget (current stack vs TiDB)
- Sample query explorer (pre-built SQL for the vertical use case)
Include specific slider parameters and output metrics relevant to the vertical.
Note: this section requires engineering implementation — flag it as an
interactive element requiring lazy loading for Core Web Vitals.
Rationale: 2 sentences. Omit this section if the vertical has no clear
interactive tool angle.

**H2 7 — Adoption journey**
Title pattern: "Adoption journey: from pilot to full [vertical] deployment"
Target: ~220–300 words.
Content: 4 H3 steps:
- Step 1: Discovery and architecture design (workshop with PingCAP SAs,
  output: target schemas, migration plan, success metrics)
- Step 2: Pilot (one region, business unit, or use case — validate in production)
- Step 3: Scale-out and rollout (phased migration, timeline visualization)
- Step 4: Optimize and expand (advanced capabilities, co-build roadmap)
Include a visual suggestion: timeline with swimlanes for app, data, and infra
teams. This section reduces perceived migration risk.
Rationale: 2 sentences.

**H2 8 — Resources and thought leadership**
Title pattern: "Resources on [vertical capability] with TiDB"
Target: ~140–180 words.
Content: 3 H3s:
- Vertical-specific case studies (link to verified pingcap.com case study URLs)
- Technical deep dives (HTAP, zero-ETL, real-time architecture blogs/webinars)
- Best practices and checklists (architecture diagrams, design guides)
All links must be verified pingcap.com URLs — do not invent paths.
Rationale: 2 sentences.

**H2 9 — Closing CTA**
Title pattern: "Get started with TiDB for [vertical/use case]"
Target: ~120–160 words.
Content: 3 role-based H3 CTAs:
- H3 1: Self-serve / free trial CTA (for engineers and builders)
- H3 2: Consultation / architecture workshop CTA (for architects and leads)
- H3 3: Docs, SDKs, and reference implementations (for developers evaluating)
Each H3 names the role it targets. Primary CTA is always "Start Free on
TiDB Cloud" or equivalent. Never stack two primary CTAs at the same location.
Rationale: 2 sentences.

**Solution page URL structure:** Always /solutions/[slug]/
Examples: /solutions/fintech/ /solutions/real-time-logistics-data-platform/
         /solutions/modernize-mysql-workloads/

**Current page refresh guidance** (for existing pages being updated):
When the brief is for a content refresh, include a "Current Heading" field
for each H2/H3 alongside the "Recommended" heading, with a one-sentence
change rationale explaining what is wrong with the current heading and why
the new version is better. Follow the format used in the SaaS and SQL
Workload modernization briefs: "Current H2: [existing text]" followed by
"Recommended H2: [new text]" and "Change rationale: [one sentence]".

**Solution page schema markup:** Always include FAQPage (if FAQ section
present), VideoObject (for the hero video), HowTo (for the adoption journey
steps), and SoftwareApplication (for TiDB mentions).

Note: The 10 H2 maximum rule applies to solution pages. Solution pages have
up to 9 template H2 sections. Social proof and interactive tools are conditional;
renumber remaining sections when either is omitted. Adoption journey position is
not fixed after omissions. Do not add placeholder sections to maintain numbering.

---

### Visual recommendations

For every H2 in the outline, assess whether the section describes something visual by nature — architecture, data flow, comparison across options, a process or sequence, a before/after. For every H2, add a one-line visual note immediately after the Inline Content Guidance in this format:

**Visual:** [Table / Architecture diagram / Code snippet / Sequence diagram / None needed] — [one sentence: what it would show and why prose alone is insufficient, OR "prose is sufficient for this section"]

Rules:

- Default to None needed unless prose genuinely cannot convey the concept clearly
- Default to Table before suggesting a diagram — tables are zero production cost for the writer and solve most comparison and mapping needs
- For code-heavy sections (SQL, CLI, SDK examples), always specify Code snippet with the language
- For architecture sections naming TiDB components (TiKV, TiFlash, PD, Raft), specify Architecture diagram only if no equivalent already exists on docs.pingcap.com — if one likely exists, note "check docs.pingcap.com before commissioning"
- Maximum 2 commissioned diagrams or illustrations per brief. Tables, code snippets,
  existing assets, and the required solution hero video do not count toward this cap.
  For solutions, use one shared architecture diagram and one adoption timeline.
  Consolidate extra diagram ideas into these two rather than exceeding the cap.

### Schema Markup Recommendations

List the exact schema types to implement. For each, write one sentence explaining
which page section it applies to and why it improves rich-result eligibility or
LLM citation quality.

---

### CTAs

List 2–3 specific CTAs. For each: specify the exact CTA copy, which section it
appears after, and the audience role it targets (e.g. "For architects evaluating
distributed SQL: 'Book a TiDB architecture workshop' — placed after the
Architecture section").

---

### Word Count Target

Scale the word count range based on the primary keyword's monthly search volume (MSV)
from the supplied Word Count Plan. Prefer valid SEMrush volume; otherwise use
the DataForSEO seed keyword volume. Never use related-keyword volume as the seed. Use this exact table — do not deviate:

| MSV | Target range | Rationale |
|-----|-------------|-----------|
| N/A or unknown | 1,800–2,500 words | Unknown volume signals an emerging or niche keyword — depth does not compensate for lack of demand |
| < 500 | 1,800–2,500 words | Low-volume keyword — focused, tight brief outperforms bloated coverage |
| 500–1,999 | 2,500–3,200 words | Moderate volume — enough depth to compete, tight enough to stay on topic |
| 2,000–4,999 | 3,000–3,800 words | High volume — comprehensive coverage needed to compete with established pages |
| 5,000+ | 3,500–4,500 words | Very high volume — maximum depth justified by competitive SERP |

State the MSV value you are using, which tier it falls into, and the resulting word count
range. Example: "Primary keyword MSV: 320. Tier: < 500. Target: 1,800–2,500 words."
Include one sentence of justification based on competitor content depth from the SERP data.

The 4,500 word ceiling is absolute — never exceed it regardless of topic complexity.

---

## Absolute Rules — Violations Will Invalidate the Brief

1. The Outline / Headings section must be the longest section by a wide margin —
   at least 50% of total word count.
2. Every H2 and H3 must have a multi-sentence Rationale covering at least two
   of the four angles listed above. No exceptions.
3. All inline writer guidance — arguments, proof points, data to find, entities
   to name, visual suggestions — goes INSIDE the relevant outline section only.
4. Do NOT include any of these as standalone top-level sections:
   "Key Points to Cover", "Data to Find", "Proof Points", "Examples to Include",
   "Visuals to Add", "Competitor Analysis", "Search Intent Analysis",
   "PingCAP/TiDB Angle", or "Keyword Strategy". These concepts belong in
   the Outline and Page Goal only.
5. No invented pingcap.com URLs. The Internal Links section may use only URLs
   supplied in the Verified Internal Link Candidates research block. If that list
   is empty, say so explicitly and do not create a URL.
6. No unverified performance claims or superlatives without cited evidence.
7. FAQ questions in the outline must map directly to the PAA data provided —
   do not invent questions.
8. Heading hierarchy must be strictly H1 -> H2 -> H3 -> H4 with no skips.
9. Maximum 10 H2 sections for comparison/blog/product/playbook/solution types. Listicle
    type may have up to 12 H2 sections to accommodate the required structure.
10. Comparison and listicle briefs must include an "at a glance" comparison table
    as the second H2. A brief missing this is a failure.
13. Listicle briefs must include: Quick answer box (H2 1), unifying evaluation
    framework (H2 4), methodology with conflict disclosure (H2 5), benchmark
    checklist (H2 6), TiDB spotlight (H2 8), 4-step decision framework (H2 9),
    architecture patterns (H2 10), and editorial policy in the closing section.
    A listicle brief missing any of these is a failure.
15. Solution page briefs must follow the Pain → Solution → Use Cases → Architecture
    → Social Proof → Adoption Journey → Resources → CTA arc. URL must use
    /solutions/[slug]/. Hero section must include video block, value prop bullets,
    and dual CTAs. Conditional social proof and interactive tools may be omitted without failure.
11. The Writer Guardrails section must appear in every brief with all six items.
12. Word Count Target must state the MSV value, which tier it falls into, the resulting
    range, and a justification sentence. A vague or un-tiered word count is a failure.
    The ceiling is always 4,500 words — exceeding it is a failure.
"""

_QUALITY_CHECKLIST = """
## Pre-Output Quality Checklist

Before outputting the brief, verify every item below. Rewrite any section that
fails a check before proceeding. Do not output a brief that fails any check.

1.  The Outline / Headings section is the longest section and accounts for at
    least 50% of the total brief word count.
2.  Every H2 and H3 has a Rationale of EXACTLY 2 sentences — not 1, not 3, not 4.
    Sentence 1 covers search intent. Sentence 2 covers LLM entity co-occurrence,
    buyer evaluation logic, or semantic positioning. Generic rationales
    (“improves SEO”, “adds keyword”) are a failure.
3.  Every H2 and H3 has specific inline content guidance — the writer knows
    exactly what argument to make, which named entity to use, which proof point
    to cite, and what the reader should conclude.
4.  The brief contains NO standalone sections titled "Key Points to Cover",
    "Proof Points", "Data to Find", "Examples", "Visuals to Add",
    "Competitor Analysis", "Search Intent Analysis", or "PingCAP/TiDB Angle".
5.  Internal links are presented as a four-column Markdown table: Section (H2),
    Anchor text, Target URL, and Why. The table contains no more than five unique
    target URLs, every URL appears in the supplied candidate list, every link maps
    to an exact H2 in the outline, no H2 receives more than two links, and every Why
    cell contains a one-sentence rationale. If no candidates were supplied, the brief
    says so explicitly instead of inventing URLs.
6.  Meta title is <=60 characters, includes the target keyword, and contains NO year (e.g. "2024", "2025"). Year references date quickly — remove them.
7.  Meta description is <=155 characters.
8.  Entity Recognition Focus lists 10–15 specific named entities (products,
    algorithms, protocols, architecture patterns) — no generic terms like
    "scalability" or "performance". Must include technical specifics like
    "Raft consensus", "TiKV", "MVCC", "PD placement driver".
9.  Relevant LLM Queries are realistic, specific user prompts — not category
    labels or generic search queries.
10. TiDB appears as a full vendor entry in every listicle, using the exact
    7-part format: Best for -> Why it's on the list -> Key features -> Pros ->
    Cons/tradeoffs -> Pricing -> Getting started.
11. TiDB vector search is described as beta (public beta) wherever mentioned.
12. FAQ questions in the outline map directly to the PAA data provided — no
    invented questions.
13. Heading hierarchy is strictly H1 -> H2 -> H3 -> H4 with no skips.
14. Every listicle vendor section uses the exact 7-part structure.
15. If LLM mentions data was provided, the brief contains an "LLM Visibility
    Snapshot" section with AI search presence, cited sources, PingCAP/TiDB
    visibility, competitor mentions, and a recommended content angle.
16. If backlinks data was provided, the brief contains a "Link Landscape &
    Acquisition Angle" section with a competitor backlink comparison table,
    anchor text patterns, link acquisition strategies, and a difficulty flag
    if any competitor has 500+ referring domains.
17. The brief includes a Word Count Target that states: the MSV value used, which
    tier it falls into (N/A → 1,800–2,500; <500 → 1,800–2,500; 500–1,999 → 2,500–3,200;
    2,000–4,999 → 3,000–3,800; 5,000+ → 3,500–4,500), the resulting range, and a
    one-sentence justification. A brief that ignores MSV and defaults to 3,800+ words
    for a low-volume keyword is a failure.
18. The outline section opens with two blocks before the heading list:
    (1) A SERP competitor table listing up to 5 top-ranking pages with their key angles,
    or a clear note that SERP data was unavailable. A brief that silently omits this table
    is a failure — an empty table with a note is acceptable, a missing table is not.
    (2) A "Patterns Favored by AI Overviews & LLMs" block with 4–6 bullets grounded in
    the SERP and competitor data. If data is unavailable, the block must say so explicitly.
19. The outline contains a maximum of 10 H2 sections, or 12 for listicles.
20. For comparison and listicle content types, the second H2 is an "at a glance"
    comparison table with 6–8 rows. A missing comparison table is a failure.
21. The brief contains a "Writer Guardrails" section with all six items.
22. URL Structure uses /compare/[slug]/ for comparison, /blog/[slug]/ for blog,
    /solutions/[slug]/ for solution, /article/[slug]/ for remaining types.
23. Every H2 includes a "Target: ~X–Y words" count immediately after the heading.
    A heading without a word count target is a failure.
24. Listicle briefs contain a Quick answer box as H2 1 with 5–7 use-case winners
    and TiDB positioned as the recommended option for its primary use case.
25. Listicle briefs contain a unifying 3-criteria evaluation framework (H2 4)
    whose criteria are reused in the comparison table and every vendor review.
26. Listicle briefs contain a methodology section (H2 5) with the exact conflict
    disclosure: "PingCAP is the publisher of this content. TiDB appears on this
    list because it meets the same evaluation criteria applied to all other
    products reviewed."
27. Listicle briefs contain a benchmark checklist section (H2 6) with 6–8 specific
    test parameters. All performance numbers include year, methodology, and source.
28. Listicle briefs contain a TiDB spotlight section (H2 8) and a 4-step decision
    framework section (H2 9).
29. Listicle briefs contain an architecture patterns section (H2 10) with 3 named
    patterns and pitfalls.
30. Listicle briefs contain an editorial policy box in the closing section covering
    vendor selection, update frequency, claim validation, and conflict disclosure.
31. Every listicle vendor review includes a "Framework score" paragraph that
    explicitly scores the vendor against all 3 evaluation criteria from H2 4.
    A vendor review missing this framework connection is a failure.
32. The outline has a clear narrative arc. The Inline Content Guidance for each
    H2 includes a transition note to the next section. Sections must not feel
    disconnected — each one must set up the next.
33. Solution page briefs include a hero section with video block, positioning
    sentence, 3–4 value prop bullets, and dual CTAs (primary + secondary).
34. Solution page briefs include a pain section (H2 1) with 3 H3s naming specific
    vertical pain points with business impact — no generic statements.
35. Solution page briefs include an adoption journey section (renumbered if conditional sections are omitted) with exactly
    4 steps including a visual suggestion for a timeline or swimlane diagram.
36. Solution page briefs use /solutions/[slug]/ URL structure. Any other URL
    format for a solution brief is a failure.
37. Solution page briefs include schema markup for VideoObject (hero video),
    HowTo (adoption steps), FAQPage (if FAQ present), and SoftwareApplication.
38. For content refresh briefs (solution type), every recommended heading change
    includes a "Current:" label and a one-sentence change rationale.
39. Every case study or customer proof point in the brief uses a verified named customer
    from the roster above, with a real pingcap.com URL. Anonymized examples ("a leading
    global X company") are a failure. A missing case study slot is better than a
    fabricated or anonymized one.
40. The outline contains an AEO answer H2 in the first third of the heading list whose
    heading directly answers the primary keyword's core question. The first 2–3 sentences
    under it are written as a self-contained extractable answer. A brief without this H2
    is a failure.
41. The outline contains a named mechanism H2 that closes the loop between the intro's
    problem and a specific TiDB mechanism by name (Raft, TiFlash, HTAP, TiKV, PD, MVCC,
    two-phase commit, online DDL, per-agent isolation, or equivalent). The heading names
    the mechanism explicitly — generic headings like "How TiDB helps" are a failure.
42. Every H2 in the outline has a Visual line (Table / Architecture diagram / Code snippet /
    Sequence diagram / None needed). The brief contains no more than 2 commissioned diagrams or illustrations;
    tables, code snippets, existing assets, and hero video are exempt. Sections that could use a table have a Table suggestion, not a diagram.
43. The brief contains a standalone Target Audience section between Page Goal and Technical
    Notes. It names specific job titles, company type, evaluation stage, and primary
    decision driver. A brief that merges audience into Page Goal or omits this section
    entirely is a failure.
"""


def build_system_prompt(examples_text, feedback_text):
    """Assemble the full system prompt from base instructions, examples, checklist, and feedback."""
    parts = [_BASE_INSTRUCTIONS]

    if examples_text:
        parts.append(
            "## PingCAP Reference Brief Examples\n\n"
            "The following are real PingCAP content briefs. Use them as the gold "
            "standard for structure, tone, heading rationale quality, internal linking "
            "style, entity focus, and PingCAP-specific positioning.\n\n"
            + examples_text
        )

    parts.append(_QUALITY_CHECKLIST)

    if feedback_text:
        parts.append(
            "## Standing Instructions from Stakeholders\n\n"
            "The following instructions were added by the PingCAP content team and "
            "must be applied to every brief:\n\n"
            + feedback_text
        )

    return "\n\n---\n\n".join(parts)


def check_pingcap_ranking(keyword):
    """Check an exact keyword in SEMrush's US domain report, retaining its URL."""
    unknown = {"classification": "unknown", "pingcap_position": None,
               "pingcap_url": None, "ranking_source": "SEMrush US"}
    # Filter delimiters cannot be embedded safely in a literal keyword.
    if any(char in keyword for char in "|,\n\r"):
        return unknown
    try:
        rows = semrush_get({
            "type": "domain_organic", "domain": PINGCAP_DOMAIN,
            "database": "us", "export_columns": "Ph,Po,Ur",
            "display_filter": f"+|Ph|Eq|{keyword}",
            "display_sort": "po_asc", "display_limit": 100,
        }, strict=True)
        if not rows:
            return {**unknown, "classification": "potential content gap",
                    "coverage": "No ranking returned by the exact-keyword domain report; check existing content."}
        exact = [row for row in rows if row.get("Ph", "").casefold() == keyword.casefold()]
        if not exact:
            return unknown
        best = min(exact, key=lambda row: int(row["Po"]))
        position = int(best["Po"])
        if position < 1 or not best.get("Ur"):
            return unknown
        return {**unknown, "classification": (
            "existing coverage" if position <= 10 else "improvement opportunity"
        ), "pingcap_position": position, "pingcap_url": best["Ur"]}
    except (RuntimeError, ValueError, TypeError, KeyError):
        return unknown
    except requests.RequestException:
        # Transport failures must not masquerade as missing rankings.
        return unknown


def word_count_plan(keyword_data, semrush_data, content_type):
    """Choose a single volume source and allocate a feasible article budget."""
    def volume(value):
        try:
            number = int(str(value).replace(",", ""))
            return number if number >= 0 else None
        except (ValueError, TypeError):
            return None

    msv = volume((semrush_data or {}).get("intent", {}).get("search_volume"))
    source = "SEMrush"
    if msv is None:
        source = "DataForSEO seed"
        msv = next((v for row in keyword_data or [] if row.get("is_seed")
                    and (v := volume(row.get("search_volume"))) is not None), None)
    if msv is None:
        source = "unavailable"
    if msv is None or msv < 500:
        low, high = 1800, 2500
        tier = "unknown" if msv is None else "<500"
    elif msv < 2000:
        low, high, tier = 2500, 3200, "500–1,999"
    elif msv < 5000:
        low, high, tier = 3000, 3800, "2,000–4,999"
    else:
        low, high, tier = 3500, 4500, "5,000+"
    total = (low + high) // 2
    plan = {"primary_keyword_msv": msv, "source": source, "tier": tier,
            "minimum": low, "maximum": high, "article_target": total}
    if content_type == "listicle":
        labels = ["H1 introduction", "Quick answer", "At a glance", "How to read",
                  "Evaluation framework", "Methodology", "Benchmark checklist",
                  "Vendor reviews", "TiDB mechanism spotlight", "Decision framework",
                  "Architecture patterns", "Closing CTA", "FAQs"]
        weights = [5, 7, 9, 5, 7, 5, 7, 27, 7, 7, 6, 4, 4]
        budgets = [total * weight // 100 for weight in weights]
        budgets[-1] += total - sum(budgets)
        plan["section_budgets"] = dict(zip(labels, budgets))
        plan["budget_rule"] = (
            "Use Target: ~N–N words for each allocation. H3s subdivide H2 allocations. "
            "If PAA is empty, omit FAQs and redistribute that allocation."
        )
    return plan


_BRIEF_SECTIONS = (
    "Meta Elements", "Page Goal", "Target Audience", "Technical Notes",
    "Writer Guardrails", "Internal Links", "LLM Visibility Snapshot",
    "Link Landscape & Acquisition Angle", "Outline / Headings",
    "Schema Markup Recommendations", "CTAs", "Word Count Target",
)


def brief_sections(content):
    """Recognize named brief boundaries without confusing article H2/H3 headings."""
    markers = []
    fenced = False
    offset = 0
    for line in content.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line.strip())
        if match and not fenced:
            title = match.group(1).strip().strip("*")
            if title in _BRIEF_SECTIONS:
                markers.append((title, offset, offset + len(line)))
        offset += len(line)
    return [(name, start, markers[i + 1][1] if i + 1 < len(markers) else len(content), body)
            for i, (name, start, body) in enumerate(markers)]


def validate_brief(content, content_type, candidates, plan):
    """Enforce observable structure and link constraints; editorial review is still needed."""
    errors = []
    sections = brief_sections(content)
    names = [name for name, *_ in sections]
    if names != list(_BRIEF_SECTIONS):
        errors.append("Required brief sections missing, duplicated, or out of order")
    bodies = {name: content[body:end] for name, start, end, body in sections}
    outline = bodies.get("Outline / Headings", "")
    # Fenced SQL/Markdown samples are not outline headings.
    outline = re.sub(r"(?ms)^\s*```.*?^\s*```[^\n]*$", "", outline)
    h2s = list(re.finditer(r"(?m)^##\s+(.+)$", outline))
    if not 1 <= len(h2s) <= (12 if content_type == "listicle" else 10):
        errors.append("Invalid number of article H2s")
    if content_type in {"comparison", "listicle"} and (
        len(h2s) < 2 or "at a glance" not in h2s[1].group(1).lower()
    ):
        errors.append("Second H2 must be an at a glance section")
    for index, match in enumerate(h2s):
        end = h2s[index + 1].start() if index + 1 < len(h2s) else len(outline)
        section = outline[match.end():end]
        section_intro = re.split(r"(?m)^#{1,4}\s+", section, maxsplit=1)[0]
        if not re.search(r"Target:\s*~?[\d,]+\s*[–-]\s*[\d,]+\s+words", section_intro):
            errors.append(f"Missing word budget: {match.group(1)}")
        if not re.search(r"\*?\*?Visual:\*?\*?", section):
            errors.append(f"Missing Visual line: {match.group(1)}")
    # Count only the first target beneath each H1/H2; H3 budgets are nested.
    targets = []
    for match in re.finditer(r"(?m)^#{1,2}\s+.+$", outline):
        following = outline[match.end():]
        section = re.split(r"(?m)^#{1,4}\s+", following, maxsplit=1)[0]
        target = re.search(r"Target:\s*~?([\d,]+)\s*[–-]\s*([\d,]+)\s+words", section)
        if target:
            targets.append(tuple(int(v.replace(",", "")) for v in target.groups()))
    if targets and (any(a > b for a, b in targets) or
                    sum(a for a, b in targets) < plan["minimum"] or
                    sum(b for a, b in targets) > plan["maximum"]):
        errors.append("Top-level word budgets do not fit the selected MSV tier")
    if "Top ranking pages table" not in outline or "Patterns Favored by AI Overviews" not in outline:
        errors.append("Missing SERP competitor or AI Overview block")
    meta = bodies.get("Meta Elements", "")
    for label, maximum in [("Meta Title", 60), ("Meta Description", 155)]:
        match = re.search(rf"(?mi)^\|\s*{label}\s*\|\s*(.*?)\s*\|", meta)
        if not match or len(match.group(1).strip()) > maximum:
            errors.append(f"Missing or overlong {label}")
    prefix = "/solutions/" if content_type == "solution" else (
        "/compare/" if content_type == "comparison" else "/blog/" if content_type == "blog" else "/article/"
    )
    url_row = re.search(r"(?mi)^\|\s*URL Structure\s*\|\s*(.*?)\s*\|", meta)
    if not url_row or prefix not in url_row.group(1):
        errors.append("Incorrect content-type URL structure")
    links = bodies.get("Internal Links", "")
    allowed = {page["url"] for page in candidates}
    heading_names = {match.group(1).strip().strip("*") for match in h2s}
    seen = set()
    placements = {}
    rows = [line for line in links.splitlines() if line.strip().startswith("|")]
    if candidates and not rows:
        errors.append("Missing Internal Links table")
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if not cells or cells[0] == "Section (H2)" or re.fullmatch(r"[-: ]+", cells[0]):
            continue
        if len(cells) != 4:
            errors.append("Internal Links row must have four columns")
            continue
        urls = re.findall(r"https?://[^\s<>\])]+", cells[2])
        url = urls[0] if len(urls) == 1 else ""
        if url not in allowed or url in seen:
            errors.append("Unverified or duplicate internal link")
        seen.add(url)
        heading = cells[0].strip("*")
        placements[heading] = placements.get(heading, 0) + 1
        if heading not in heading_names or placements[heading] > 2:
            errors.append("Internal link has invalid H2 placement")
    if len(seen) > 5:
        errors.append("More than five internal links")
    if not candidates and "no verified internal link candidates" not in links.lower():
        errors.append("Missing unavailable internal-links notice")
    return errors


def generate_brief(topic, content_type, keyword_data, serp_results, paa_questions, competitor_headings, llm_mentions_data=None, backlinks_data=None, semrush_data=None, internal_link_candidates=None, serp_features=None):
    """Load examples + feedback, build the system prompt, and call Claude."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    examples_text = load_brief_examples()
    feedback_text = load_feedback()
    system_prompt = build_system_prompt(examples_text, feedback_text)

    research_block = f"""## Topic
{topic}

## Content Type
{content_type}

---

## Keyword Research Data
```json
{json.dumps(keyword_data, indent=2)}
```

---

## Top 10 Organic SERP Results
```json
{json.dumps(serp_results, indent=2)}
```

---

## People Also Ask Questions
```json
{json.dumps(paa_questions, indent=2)}
```

---

## Competitor Heading Structures (Top 3 Results)
```json
{json.dumps(competitor_headings, indent=2)}
```
"""

    plan = word_count_plan(keyword_data, semrush_data, content_type)
    research_block += "\n## Word Count Plan (mandatory)\n" + json.dumps(plan, indent=2)
    research_block += "\n## SERP Features\n" + json.dumps(serp_features or {
        "status": "unavailable", "ai_overview": [], "featured_snippet": []
    }, indent=2)

    if semrush_data:
        intent_data = semrush_data.get("intent", {})
        research_block += f"""
---

## SEMrush Enrichment Data

### Primary Keyword Intent & Metrics
```json
{json.dumps(intent_data, indent=2)}
```

Use the intent field to shape the tone of the brief:
- informational → educational, definitional content
- commercial → comparison, evaluation, use-case content
- transactional → conversion-focused, CTA-heavy content

### SEMrush Related & LSI Keywords
Use these to enrich the Supporting Keywords table. Prioritise by volume.
```json
{json.dumps(semrush_data.get("related_keywords", []), indent=2)}
```

### Competitor Keyword Opportunities — PingCAP Ranking Checked
Respect each classification: top-10 PingCAP keywords are excluded; improvement
opportunities should refresh the supplied existing URL. Potential gaps mean no
ranking returned by this SEMrush report, not proof that content is missing.
Check existing content before recommending a new page. Unknown means the ranking
check failed; never label it a confirmed gap. All comparisons use the US database.
```json
{json.dumps(semrush_data.get("keyword_gap", []), indent=2)}
```

### Competitor Domain Authority
Use this to calibrate the Word Count Target and Link Landscape difficulty assessment.
Higher authority competitors require deeper, more comprehensive content to compete.
```json
{json.dumps(semrush_data.get("domain_authority", []), indent=2)}
```
"""

    research_block += f"""
---

## Verified Internal Link Candidates (PingCAP Sitemap)

These pages were selected deterministically from the verified PingCAP sitemap inventory.
Use only these URLs in the Internal Links table. Do not invent, alter, or guess a path.
Map each selected URL to an exact H2 in the outline, use each URL once, place no more
than two links in one H2, and preserve the purpose in the selection_rule field.
If the list is empty, state that the sitemap inventory returned no verified candidates
and do not create an internal link.

```json
{json.dumps(internal_link_candidates or [], indent=2)}
```
"""

    if llm_mentions_data:
        research_block += f"""
---

## LLM Mentions Data (AI Optimization API)
```json
{json.dumps(llm_mentions_data, indent=2)}
```
"""

    if backlinks_data:
        research_block += f"""
---

## Competitor Backlinks Data
```json
{json.dumps(backlinks_data, indent=2)}
```
"""

    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8096,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    "Generate a fully populated content brief based on the research below.\n\n"
                    + research_block
                ),
            }
        ],
    )

    if message.stop_reason != "end_turn":
        raise ValueError(f"Brief generation incomplete: {message.stop_reason}")
    text = "\n".join(block.text for block in message.content if block.type == "text")
    errors = validate_brief(text, content_type, internal_link_candidates or [], plan)
    if errors:
        raise ValueError("Brief failed validation: " + "; ".join(errors))
    return text


# ── Markdown Parser ───────────────────────────────────────────────────────────

def parse_inline(text):
    """
    Strip **bold** markers from text.
    Returns (plain_text, bold_ranges) where bold_ranges is a list of (start, end)
    character offsets within plain_text.
    """
    parts = []
    bold_ranges = []
    i = 0
    pos = 0
    while i < len(text):
        if text[i:i+2] == "**":
            end = text.find("**", i + 2)
            if end != -1:
                word = text[i+2:end]
                bold_ranges.append((pos, pos + len(word)))
                parts.append(word)
                pos += len(word)
                i = end + 2
            else:
                parts.append(text[i])
                pos += 1
                i += 1
        else:
            parts.append(text[i])
            pos += 1
            i += 1
    return "".join(parts), bold_ranges


def parse_markdown(text):
    """
    Parse a markdown string into a list of block dicts:
      {'type': 'h1'|'h2'|'h3'|'h4', 'text': str, 'bold_ranges': [...]}
      {'type': 'bullet'|'numbered',  'text': str, 'bold_ranges': [...]}
      {'type': 'paragraph',          'text': str, 'bold_ranges': [...]}
      {'type': 'table', 'rows': [[str, ...], ...], 'cols': int}
      {'type': 'blank'}
    """
    blocks = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── Table (header row followed by separator) ──────────────────────────
        if "|" in line:
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if re.match(r"^\s*\|[-: |]+\|\s*$", next_line):
                table_rows = []
                while i < len(lines) and "|" in lines[i]:
                    row = lines[i].strip()
                    if re.match(r"^\s*\|[-: |]+\|\s*$", row):
                        i += 1
                        continue
                    cells = [re.sub(r"\*\*(.*?)\*\*", r"\1", c.strip()).replace("<br>", "\n")
                             for c in row.strip("|").split("|")]
                    table_rows.append(cells)
                    i += 1
                if table_rows:
                    num_cols = max(len(r) for r in table_rows)
                    table_rows = [r + [""] * (num_cols - len(r)) for r in table_rows]
                    blocks.append({"type": "table", "rows": table_rows, "cols": num_cols})
                continue

        # ── Headings ──────────────────────────────────────────────────────────
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            plain, bold_ranges = parse_inline(m.group(2).strip())
            blocks.append({"type": f"h{level}", "text": plain, "bold_ranges": bold_ranges})
            i += 1
            continue

        # ── Unordered list ────────────────────────────────────────────────────
        m = re.match(r"^(\s*)[-*+]\s+(.*)", line)
        if m:
            plain, bold_ranges = parse_inline(m.group(2).strip())
            blocks.append({"type": "bullet", "text": plain, "bold_ranges": bold_ranges})
            i += 1
            continue

        # ── Ordered list ──────────────────────────────────────────────────────
        m = re.match(r"^(\s*)\d+[.)]\s+(.*)", line)
        if m:
            plain, bold_ranges = parse_inline(m.group(2).strip())
            blocks.append({"type": "numbered", "text": plain, "bold_ranges": bold_ranges})
            i += 1
            continue

        # ── Horizontal rule ───────────────────────────────────────────────────
        if re.match(r"^[-*_]{3,}\s*$", line):
            if blocks and blocks[-1]["type"] != "blank":
                blocks.append({"type": "blank"})
            i += 1
            continue

        # ── Blank line ────────────────────────────────────────────────────────
        if not line.strip():
            if blocks and blocks[-1]["type"] != "blank":
                blocks.append({"type": "blank"})
            i += 1
            continue

        # ── Normal paragraph ──────────────────────────────────────────────────
        plain, bold_ranges = parse_inline(line)
        if plain.strip():
            blocks.append({"type": "paragraph", "text": plain, "bold_ranges": bold_ranges})
        i += 1

    # Strip trailing blank blocks
    while blocks and blocks[-1]["type"] == "blank":
        blocks.pop()

    return blocks


_HEADING_STYLE = {
    "h1": "HEADING_1",
    "h2": "HEADING_2",
    "h3": "HEADING_3",
    "h4": "HEADING_4",
}


def _make_location(idx, tab_id=None):
    loc = {"index": idx}
    if tab_id:
        loc["tabId"] = tab_id
    return loc


def _make_range(start, end, tab_id=None):
    r = {"startIndex": start, "endIndex": end}
    if tab_id:
        r["tabId"] = tab_id
    return r


def _bold_requests(bold_ranges, base_idx, tab_id=None):
    return [
        {
            "updateTextStyle": {
                "range": _make_range(base_idx + bs, base_idx + be, tab_id),
                "textStyle": {"bold": True},
                "fields": "bold",
            }
        }
        for bs, be in bold_ranges
    ]


def build_docs_requests(blocks, tab_id=None, use_heading_styles=True):
    """
    Convert parsed markdown blocks into Google Docs API batchUpdate requests.
    Returns (requests_list, final_index).

    tab_id:             if set, injects tabId into all location/range objects
                        so requests target a specific tab.
    use_heading_styles: if True  -> apply native HEADING_1/2/3/4 styles (Tab 2).
                        if False -> render headings as bold NORMAL_TEXT (Tab 1).
    """
    reqs = []
    idx = 1  # Docs API write positions start at 1

    for block in blocks:
        btype = block["type"]

        # Blank
        if btype == "blank":
            reqs.append({"insertText": {"location": _make_location(idx, tab_id), "text": "\n"}})
            idx += 1

        # Headings
        elif btype in _HEADING_STYLE:
            text = block["text"]
            full = text + "\n"
            reqs.append({"insertText": {"location": _make_location(idx, tab_id), "text": full}})
            if use_heading_styles:
                # Tab 2 (Article Outline): native heading styles
                reqs.append({
                    "updateParagraphStyle": {
                        "range": _make_range(idx, idx + len(full), tab_id),
                        "paragraphStyle": {"namedStyleType": _HEADING_STYLE[btype]},
                        "fields": "namedStyleType",
                    }
                })
            else:
                # Tab 1 (Brief Metadata): use native heading styles so H1/H2/H3
                # render with distinct sizes — same as the outline section.
                reqs.append({
                    "updateParagraphStyle": {
                        "range": _make_range(idx, idx + len(full), tab_id),
                        "paragraphStyle": {"namedStyleType": _HEADING_STYLE[btype]},
                        "fields": "namedStyleType",
                    }
                })
            reqs.extend(_bold_requests(block.get("bold_ranges", []), idx, tab_id))
            idx += len(full)

        # Bullet list
        elif btype == "bullet":
            text = block["text"]
            full = text + "\n"
            reqs.append({"insertText": {"location": _make_location(idx, tab_id), "text": full}})
            reqs.append({
                "createParagraphBullets": {
                    "range": _make_range(idx, idx + len(full), tab_id),
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })
            reqs.extend(_bold_requests(block.get("bold_ranges", []), idx, tab_id))
            idx += len(full)

        # Ordered list
        elif btype == "numbered":
            text = block["text"]
            full = text + "\n"
            reqs.append({"insertText": {"location": _make_location(idx, tab_id), "text": full}})
            reqs.append({
                "createParagraphBullets": {
                    "range": _make_range(idx, idx + len(full), tab_id),
                    "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                }
            })
            reqs.extend(_bold_requests(block.get("bold_ranges", []), idx, tab_id))
            idx += len(full)

        # Normal paragraph
        elif btype == "paragraph":
            text = block["text"]
            full = text + "\n"
            reqs.append({"insertText": {"location": _make_location(idx, tab_id), "text": full}})
            reqs.append({
                "updateParagraphStyle": {
                    "range": _make_range(idx, idx + len(full), tab_id),
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "fields": "namedStyleType",
                }
            })
            reqs.extend(_bold_requests(block.get("bold_ranges", []), idx, tab_id))
            idx += len(full)

        # Table (native Google Docs table)
        elif btype == "table":
            rows = block["rows"]
            num_rows = len(rows)
            num_cols = block["cols"]

            # 1. Insert empty table
            reqs.append({
                "insertTable": {
                    "location": _make_location(idx, tab_id),
                    "rows": num_rows,
                    "columns": num_cols,
                }
            })

            # 2. Populate cells in reverse order (last→first) so insertions
            #    don't shift indices of not-yet-populated cells.
            #    NOTE: insertTable adds a leading newline before the table,
            #    so the table structure starts at idx+1 and cells at idx+4.
            for r in range(num_rows - 1, -1, -1):
                for c in range(num_cols - 1, -1, -1):
                    cell_text = rows[r][c].strip() if c < len(rows[r]) else ""
                    if not cell_text:
                        continue
                    cell_idx = idx + 4 + r * (2 * num_cols + 1) + c * 2
                    reqs.append({
                        "insertText": {
                            "location": _make_location(cell_idx, tab_id),
                            "text": cell_text,
                        }
                    })

            # 3. Bold header row + left column labels
            #    Precompute cumulative text lengths for all cells to determine
            #    final positions after all text insertions.
            def bold_req(start, end):
                return {
                    "updateTextStyle": {
                        "range": _make_range(start, end, tab_id),
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                }

            cum_before = {}
            running = 0
            for r in range(num_rows):
                for c in range(num_cols):
                    cum_before[(r, c)] = running
                    cell_text = rows[r][c].strip() if c < len(rows[r]) else ""
                    running += len(cell_text)

            # Header row (all columns)
            for c in range(num_cols):
                cell_text = rows[0][c].strip() if c < len(rows[0]) else ""
                if cell_text:
                    fs = idx + 4 + c * 2 + cum_before[(0, c)]
                    reqs.append(bold_req(fs, fs + len(cell_text)))

            # Left column labels (rows 1+, column 0 only)
            for r in range(1, num_rows):
                cell_text = rows[r][0].strip() if 0 < len(rows[r]) else ""
                if cell_text:
                    fs = idx + 4 + r * (2 * num_cols + 1) + cum_before[(r, 0)]
                    reqs.append(bold_req(fs, fs + len(cell_text)))

            # 4. Advance index past the fully-populated table.
            #    Total = 1 (leading \n) + R*(2C+1)+2 (table) + total_text
            total_text = sum(
                len(rows[r][c].strip()) if c < len(rows[r]) else 0
                for r in range(num_rows)
                for c in range(num_cols)
            )
            idx += num_rows * (2 * num_cols + 1) + 3 + total_text

    return reqs, idx


# Brief splitter

def _shift_request_indices(req, offset):
    """
    Recursively add `offset` to every startIndex / endIndex / index value
    in a Docs API request dict. Used to merge outline requests (which start
    at index 1) into a document that already has metadata content.
    """
    if isinstance(req, dict):
        out = {}
        for k, v in req.items():
            if k in ("startIndex", "endIndex", "index") and isinstance(v, int):
                out[k] = v + offset
            else:
                out[k] = _shift_request_indices(v, offset)
        return out
    elif isinstance(req, list):
        return [_shift_request_indices(item, offset) for item in req]
    return req


def split_brief(content):
    """Separate the outline from all surrounding metadata using named boundaries."""
    for name, start, end, body in brief_sections(content):
        if name == "Outline / Headings":
            metadata = (content[:start].rstrip() + "\n\n" + content[end:].lstrip()).strip()
            return metadata, content[start:end].strip()
    return content, ""


# ── Google Docs


# ── Google Docs ───────────────────────────────────────────────────────────────

def get_google_credentials():
    """Return valid Google OAuth2 credentials, refreshing or re-authorising as needed."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())

    return creds


def get_or_create_drive_folder(creds):
    """
    Return the Drive folder ID for Content Briefs.

    Searches Google Drive for an existing folder with the correct name first.
    Only creates a new folder if none exists. This is safe to call on every
    deployment — it will never create duplicate folders.
    """
    # Check env var first — allows pinning a specific folder ID on Railway
    env_folder_id = os.getenv("DRIVE_FOLDER_ID")
    if env_folder_id:
        return env_folder_id

    drive = build("drive", "v3", credentials=creds)

    # Search for existing folder by name
    query = (
        f"name='{DRIVE_FOLDER_NAME}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = drive.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=1,
    ).execute()

    existing = results.get("files", [])
    if existing:
        folder_id = existing[0]["id"]
        # Cache locally for faster subsequent calls within the same process
        try:
            with open(FOLDER_ID_FILE, "w") as fh:
                fh.write(folder_id)
        except Exception:
            pass
        return folder_id

    # No existing folder found — create one
    folder = drive.files().create(
        body={
            "name": DRIVE_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        },
        fields="id",
    ).execute()
    folder_id = folder["id"]

    try:
        with open(FOLDER_ID_FILE, "w") as fh:
            fh.write(folder_id)
    except Exception:
        pass

    print(f'          Created Google Drive folder: "{DRIVE_FOLDER_NAME}"')
    return folder_id


def create_google_doc(title, content):
    """
    Create a formatted Google Doc in the Content Briefs folder.

    The brief is written as a single document in two logical sections:

      Section 1 "BRIEF METADATA"
        All sections except Outline / Headings.
        Heading markers (##, ###) rendered as bold NORMAL_TEXT so they do not
        pollute the document outline panel.

      Section 2 "ARTICLE OUTLINE"
        Only the Outline / Headings section.
        Heading markers rendered as native HEADING_1/2/3/4 styles so the
        document outline and any table of contents works correctly.

    The two sections are separated by a bold divider paragraph.
    """
    creds = get_google_credentials()
    docs  = build("docs", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    folder_id = get_or_create_drive_folder(creds)

    # Split brief into metadata and outline sections
    metadata_content, outline_content = split_brief(content)

    # Build request list: metadata (no heading styles) + divider + outline (heading styles)
    all_requests = []
    idx = 1

    # -- Section 1: Brief Metadata (headings as bold NORMAL_TEXT) ---------------
    if metadata_content:
        metadata_blocks = parse_markdown(metadata_content)
        meta_reqs, idx = build_docs_requests(
            metadata_blocks, tab_id=None, use_heading_styles=False
        )
        all_requests.extend(meta_reqs)

    # -- Divider between the two sections ---------------------------------------
    divider_text = "\n" + ("─" * 40) + " ARTICLE OUTLINE " + ("─" * 40) + "\n\n"
    all_requests.append({
        "insertText": {"location": {"index": idx}, "text": divider_text}
    })
    all_requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": idx, "endIndex": idx + len(divider_text)},
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "fields": "namedStyleType",
        }
    })
    all_requests.append({
        "updateTextStyle": {
            "range": {"startIndex": idx, "endIndex": idx + len(divider_text) - 1},
            "textStyle": {"bold": True},
            "fields": "bold",
        }
    })
    idx += len(divider_text)

    # -- Section 2: Article Outline (native heading styles) ---------------------
    if outline_content:
        outline_blocks = parse_markdown(outline_content)
        # build_docs_requests starts idx at 1 internally; we need it to continue
        # from our running idx, so we offset the returned requests manually.
        outline_reqs_raw, _ = build_docs_requests(
            outline_blocks, tab_id=None, use_heading_styles=True
        )
        offset = idx - 1  # build_docs_requests starts at 1, we need to shift
        for req in outline_reqs_raw:
            req = _shift_request_indices(req, offset)
            all_requests.append(req)

    # Create empty document and apply all formatting in one batchUpdate
    doc    = docs.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    if all_requests:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": all_requests},
        ).execute()

    # Move into Content Briefs folder
    drive.files().update(
        fileId=doc_id,
        addParents=folder_id,
        removeParents="root",
        fields="id, parents",
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"


# ── Main ──────────────────────────────────────────────────────────────────────

def validate_env():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not DATAFORSEO_LOGIN:
        missing.append("DATAFORSEO_LOGIN")
    if not DATAFORSEO_PASSWORD:
        missing.append("DATAFORSEO_PASSWORD")
    if not SEMRUSH_API_KEY:
        print("Warning: SEMRUSH_API_KEY not set — SEMrush enrichment will be skipped.")
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("Please fill in your .env file and try again.")
        sys.exit(1)


def main():
    if len(sys.argv) != 3:
        print("Usage:  python brief.py \"<topic>\" <content_type>")
        print(f"Types:  {', '.join(CONTENT_TYPES)}")
        print('Example: python brief.py "best database for AI agents" listicle')
        sys.exit(1)

    topic = sys.argv[1].strip()
    content_type = sys.argv[2].strip().lower()

    # ── Extract the primary keyword for API calls ─────────────────────────────
    # When the user provides a full prompt as the topic, extract just the
    # primary keyword for SEMrush, DataForSEO, and SERP API calls.
    # Look for "Primary keyword: X" pattern first, then fall back to first
    # sentence or first 80 characters, whichever is shorter.
    import re as _re
    _kw_match = _re.search(
        r'[Pp]rimary\s+keyword[:\s]+([^\.\n,]+)',
        topic
    )
    if _kw_match:
        search_keyword = _kw_match.group(1).strip().rstrip('.')
    elif len(topic) > 80:
        # Use first sentence if available, else first 80 chars
        _first_sent = topic.split('.')[0].strip()
        search_keyword = _first_sent if len(_first_sent) <= 80 else topic[:80]
    else:
        search_keyword = topic

    # Cap at 100 chars to be safe with all APIs
    search_keyword = search_keyword[:100]

    if content_type not in CONTENT_TYPES:
        print(f"Error: content_type must be one of: {', '.join(CONTENT_TYPES)}")
        sys.exit(1)

    validate_env()

    print("\n=== Content Brief Generator ===")
    print(f"Topic        : {topic[:100]}{'...' if len(topic) > 100 else ''}")
    print(f"Search KW    : {search_keyword}")
    print(f"Content type : {content_type}")
    print()

    # ── Step 1/10: Keyword data ─────────────────────────────────────────────
    print("Step 1/11  Fetching keyword data from DataForSEO...")
    print(f"           Using keyword: \"{search_keyword}\"")
    try:
        keyword_data = get_keyword_data(search_keyword)
        print(f"           Got data for {len(keyword_data)} keywords")
    except Exception as exc:
        print(f"           Failed: {exc}")
        keyword_data = []

    # ── Step 2/10: SERP + PAA ───────────────────────────────────────────────
    print("Step 2/11  Fetching SERP results and PAA questions...")
    try:
        serp_results, paa_questions, serp_features = get_serp_and_paa(search_keyword)
        print(
            f"           Got {len(serp_results)} SERP results "
            f"and {len(paa_questions)} PAA questions"
        )
    except Exception as exc:
        print(f"           Failed: {exc}")
        serp_results, paa_questions = [], []
        serp_features = {"ai_overview": [], "featured_snippet": [], "status": "unavailable"}

    # ── Step 3/10: Competitor headings ──────────────────────────────────────
    print("Step 3/11  Scraping headings from top 3 competitor pages...")
    competitor_headings = []
    for i, result in enumerate(serp_results[:3], start=1):
        url = result.get("url", "")
        if not url:
            continue
        print(f"           #{i} {url}")
        headings = extract_headings_from_url(url)
        competitor_headings.append(
            {"rank": i, "url": url, "headings": headings}
        )
        print(f"              Found {len(headings)} headings")

    # ── Step 4/10: LLM Mentions ─────────────────────────────────────────────
    print("Step 4/11  Fetching LLM mentions data...")
    llm_mentions_data = None
    try:
        llm_mentions_data = get_llm_mentions(search_keyword)
        q_count = len(llm_mentions_data.get("questions", []))
        s_count = len(llm_mentions_data.get("sources", []))
        print(f"           Got {q_count} mentions, {s_count} cited sources")
        if llm_mentions_data.get("pingcap_mentioned"):
            print("           ✓ PingCAP found in AI responses")
        if llm_mentions_data.get("tidb_mentioned"):
            print("           ✓ TiDB found in AI responses")
    except Exception as exc:
        print(f"           Failed: {exc}")
        llm_mentions_data = None

    # ── Step 5/10: Backlinks data ───────────────────────────────────────────
    print("Step 5/11  Fetching competitor backlinks data...")
    backlinks_data = None
    try:
        competitor_urls = [r.get("url", "") for r in serp_results[:3] if r.get("url")]
        if competitor_urls:
            backlinks_data = get_backlinks_data(competitor_urls)
            for bl in (backlinks_data or []):
                rd = bl.get("referring_domains", 0)
                print(f"           {bl['url'][:60]}  —  {rd} referring domains")
        else:
            print("           No competitor URLs available, skipping")
    except Exception as exc:
        print(f"           Failed: {exc}")
        backlinks_data = None

    # ── Step 6/11: Sitemap — internal link candidates ───────────────────────
    print("Step 6/11  Selecting internal links from the PingCAP sitemap...")
    internal_link_candidates = []
    try:
        inventory_pages, inventory_source = load_internal_link_inventory(
            inventory_path=SITEMAP_INVENTORY_FILE,
            sitemap_url=PINGCAP_SITEMAP_URL,
        )
        internal_link_candidates = select_internal_link_candidates(
            inventory_pages,
            search_keyword,
            content_type,
            max_links=5,
        )
        internal_link_candidates = validate_internal_link_candidates(
            internal_link_candidates
        )
        if internal_link_candidates:
            print(
                f"           Found {len(internal_link_candidates)} verified candidates "
                f"from {inventory_source}"
            )
            for page in internal_link_candidates:
                print(f"           Slot {page['slot']}: {page['url'][:70]}")
        else:
            print("           No topically relevant sitemap pages found")
    except Exception as exc:
        print(f"           Failed: {exc}")
        internal_link_candidates = []

    # ── Step 7/11: SEMrush — keyword intent ─────────────────────────────────
    print("Step 7/11  Fetching keyword intent from SEMrush...")
    semrush_intent = {}
    if SEMRUSH_API_KEY:
        try:
            semrush_intent = get_semrush_keyword_intent(search_keyword)
            intent = semrush_intent.get("intent", "unknown")
            vol = semrush_intent.get("search_volume", "N/A")
            kd = semrush_intent.get("keyword_difficulty", "N/A")
            print(f"           Intent: {intent}  |  Volume: {vol}  |  KD: {kd}")
        except Exception as exc:
            print(f"           Failed: {exc}")
    else:
        print("           Skipped — SEMRUSH_API_KEY not set")

    # ── Step 7/10: SEMrush — related & LSI keywords ─────────────────────────
    print("Step 8/11  Fetching related keywords from SEMrush...")
    semrush_related = []
    if SEMRUSH_API_KEY:
        try:
            semrush_related = get_semrush_related_keywords(search_keyword, limit=15)
            print(f"           Got {len(semrush_related)} related keywords")
        except Exception as exc:
            print(f"           Failed: {exc}")
    else:
        print("           Skipped — SEMRUSH_API_KEY not set")

    # ── Step 8/10: SEMrush — keyword gap ────────────────────────────────────
    print("Step 9/11  Running keyword gap analysis via SEMrush...")
    semrush_gap = []
    if SEMRUSH_API_KEY:
        try:
            competitor_domains = []
            for r in serp_results[:3]:
                url = r.get("url", "")
                domain = url_domain(url)
                if domain and domain != PINGCAP_DOMAIN:
                    competitor_domains.append(domain)
            if competitor_domains:
                semrush_gap = get_semrush_keyword_gap(search_keyword, competitor_domains)
                print(f"           Found {len(semrush_gap)} gap keywords vs {competitor_domains[:2]}")
            else:
                print("           No competitor domains available, skipping")
        except Exception as exc:
            print(f"           Failed: {exc}")
    else:
        print("           Skipped — SEMRUSH_API_KEY not set")

    # ── Step 9/10: SEMrush — competitor domain authority ────────────────────
    print("Step 10/11  Fetching competitor domain authority from SEMrush...")
    semrush_authority = []
    if SEMRUSH_API_KEY:
        try:
            comp_urls = [r.get("url", "") for r in serp_results[:3] if r.get("url")]
            semrush_authority = get_semrush_domain_authority(comp_urls)
            for d in semrush_authority:
                print(f"           {d['domain']:40s}  authority: {d['authority_score']}  "
                      f"traffic: {d['organic_traffic_est']}")
        except Exception as exc:
            print(f"           Failed: {exc}")
    else:
        print("           Skipped — SEMRUSH_API_KEY not set")

    semrush_data = {
        "intent": semrush_intent,
        "related_keywords": semrush_related,
        "keyword_gap": semrush_gap,
        "domain_authority": semrush_authority,
    } if SEMRUSH_API_KEY else None

    # ── Step 10/10: Generate brief with Claude ───────────────────────────────
    print("Step 11/11 Generating content brief with Claude...")
    try:
        brief = generate_brief(
            topic,
            content_type,
            keyword_data,
            serp_results,
            paa_questions,
            competitor_headings,
            llm_mentions_data=llm_mentions_data,
            backlinks_data=backlinks_data,
            semrush_data=semrush_data,
            internal_link_candidates=internal_link_candidates,
            serp_features=serp_features,
        )
        print("           Brief generated successfully")
    except Exception as exc:
        print(f"           Claude API failed: {exc}")
        sys.exit(1)

    safe_title = re.sub(r"[^\w\s-]", "", topic[:50]).strip().replace(" ", "_")
    local_path = os.path.join(os.getcwd(), f"brief_{safe_title}.md")
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(brief)
    print(f"           Brief saved locally: {local_path}")

    # ── Step 10/10 cont: Create Google Doc ──────────────────────────────────
    print("           Creating Google Doc...")
    print("           (A browser window may open for Google authentication)")
    print("           Summarising topic into doc title...")
    try:
        short_title = summarize_title(topic)
    except Exception as exc:
        print(f"          Title summarisation failed, using raw topic: {exc}")
        short_title = topic[:60]
    doc_title = f"Content Brief: {short_title} [{content_type}]"
    try:
        doc_url = create_google_doc(doc_title, brief)
    except Exception as exc:
        print(f"          Google Docs failed: {exc}")
        print(f"          Local Markdown remains available: {local_path}")
        sys.exit(1)

    print()
    print("Done! Your content brief is ready:")
    print(f"  {doc_url}")

    print_cost_summary()


if __name__ == "__main__":
    main()
