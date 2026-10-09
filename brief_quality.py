"""Brief quality bar: config-driven prompt assembly, deterministic fields, checks, repair.

Everything tunable (limits, word lists, templates, roster, product facts) lives in
config/. This module only interprets it. The model writes prose; anything that can
be computed (slug, target keyword, intent, supporting keywords, Total MSV, empty-data
notes, CTA pruning, claim markers) is set here and then checked like everything else.
"""
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

CONFIG_DIR = Path(__file__).resolve().parent / "config"

# Must match brief.py _BRIEF_SECTIONS (tests assert this).
BRIEF_SECTIONS = (
    "Meta Elements", "Page Goal", "Target Audience", "Technical Notes",
    "Writer Guardrails", "Internal Links", "LLM Visibility Snapshot",
    "Link Landscape & Acquisition Angle", "Outline / Headings",
    "Schema Markup Recommendations", "CTAs", "Word Count Target",
)
OUTLINE = "Outline / Headings"
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "how", "i", "in", "is", "it", "of", "on", "or", "should", "the", "to", "vs", "what",
    "when", "which", "who", "why", "with", "you", "your", "my", "we", "our", "this", "that",
}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


# ── Config ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def load_yaml(name):
    with open(CONFIG_DIR / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def rules():
    return load_yaml("brief_rules.yaml")


@lru_cache(maxsize=None)
def template_for(content_type):
    for path in sorted((CONFIG_DIR / "templates").glob("*.yaml")):
        data = load_yaml(f"templates/{path.name}")
        if path.stem != "default" and content_type in data["content_types"]:
            return data
    return load_yaml("templates/default.yaml")


def roster():
    return load_yaml("customer_roster.yaml")["customers"]


def link_allowed(url):
    """Case-study pages may be linked only when the customer is in the verified roster."""
    if re.search(r"/case-stud(?:y|ies)/.", urlparse(url).path):
        return url in {c["url"] for c in roster()}
    return True


def product_facts():
    return load_yaml("product_facts.yaml")


def review_sources(competitor):
    entries = load_yaml("review_sources.yaml").get("competitors") or {}
    return entries.get((competitor or "").casefold()) or {}


def _lookup(data, path):
    for part in path.split("."):
        data = data[part]
    return data


def render(text, values):
    """Fill {{dotted.path}} placeholders; an unknown placeholder is a config bug."""
    return re.sub(r"\{\{([a-z_.]+)\}\}", lambda m: str(_lookup(values, m.group(1))), text)


# ── Deterministic values ─────────────────────────────────────────────────────

def slugify(keyword):
    return re.sub(r"[^a-z0-9]+", "-", keyword.casefold()).strip("-")


def page_url(content_type, keyword):
    prefixes = rules()["meta"]["slug_prefixes"]
    return prefixes.get(content_type, prefixes["default"]) + slugify(keyword)


def intent_label(search_intent):
    labels = rules()["search_intent_labels"]
    if search_intent not in labels:
        raise ValueError(f"Unknown Stage 0 search intent: {search_intent!r}")
    return labels[search_intent]


def volume(value):
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def word_tier(msv):
    if msv is None:
        raise ValueError("Primary keyword MSV is unavailable; a word-count tier cannot be chosen.")
    for tier in rules()["word_count"]["tiers"]:
        if tier["max_msv"] is None or msv <= tier["max_msv"]:
            return tier
    raise ValueError("Word-count tiers do not cover MSV " + str(msv))


def section_budgets(content_type, total):
    weights = template_for(content_type).get("word_weights")
    if not weights:
        return None
    budgets = {label: total * weight // 100 for label, weight in weights.items()}
    last = list(budgets)[-1]
    budgets[last] += total - sum(budgets.values())
    return budgets


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").casefold())
            if len(t) > 2 and t not in _STOP_WORDS}


def supporting_keywords(resolution, keyword_data=None, semrush_related=None, entity_coverage=()):
    """Measured supporting keywords: MSV on every row; zero/unknown volume only for entity coverage."""
    cfg = rules()["supporting_keywords"]
    primary = " ".join(resolution["primary_keyword"].casefold().split())
    coverage = {" ".join(e.casefold().split()) for e in entity_coverage}
    found = {}
    # Rejections remain authoritative even if another provider returns the same term.
    candidates = resolution.get("supporting_candidates") or []
    rejected = {" ".join(r.get("keyword", "").casefold().split()) for r in candidates
                if str(r.get("status", "")).startswith(("discarded", "blocked", "needs writer confirmation"))}
    # Stage 0 already judged these against the title angle on the live SERP, which is
    # stronger evidence than shared words, so the lexical check below does not apply.
    validated = {" ".join(r.get("keyword", "").casefold().split()) for r in candidates
                 if r.get("status") == "eligible"}
    generic = set(cfg["generic_relevance_tokens"])
    def meaningful(value):
        return {t.rstrip("s") for t in tokens(value)} - generic
    angle_tokens = meaningful(resolution.get("title_angle", "") + " " + primary)

    def add(keyword, msv, source):
        if not isinstance(keyword, str) or not keyword.strip():
            return
        key = " ".join(keyword.casefold().split())
        if key == primary or "?" in key or key in rejected:
            return
        # Conservative lexical check for terms Stage 0 did not SERP-validate.
        if key not in coverage and key not in validated and not meaningful(key).intersection(angle_tokens):
            return
        msv = volume(msv)
        row = found.setdefault(key, {"keyword": key, "msv": msv, "source": source})
        if msv is not None and (row["msv"] is None or msv > row["msv"]):
            row.update(msv=msv, source=source)

    for row in resolution.get("supporting_candidates") or []:
        if not str(row.get("status", "")).startswith("discarded"):
            add(row.get("keyword"), row.get("msv"), "Stage 0 (DataForSEO Google Ads)")
    for row in keyword_data or []:
        add(row.get("keyword"), row.get("search_volume"), "DataForSEO related")
    for row in semrush_related or []:
        add(row.get("keyword"), row.get("search_volume"), "SEMrush related")
    measured = sorted(({**r, "entity_coverage": False} for r in found.values() if r["msv"]),
                      key=lambda r: (-r["msv"], r["keyword"]))
    # Tagged entity-coverage terms are kept even at zero volume; they reserve room in the cap.
    covered = sorted(({**r, "entity_coverage": True} for r in found.values()
                      if not r["msv"] and r["keyword"] in coverage), key=lambda r: r["keyword"])
    covered = covered[:cfg["max_keywords"]]
    return measured[:cfg["max_keywords"] - len(covered)] + covered


def relevant_serp(organic, threshold):
    """Relevant pages only, deduplicated by URL, original rank preserved."""
    seen, rows = set(), []
    for row in organic:
        url = row.get("url", "")
        if url in seen or not isinstance(row.get("relevance"), (int, float)):
            continue
        seen.add(url)
        if row["relevance"] >= threshold:
            rows.append(row)
    return rows


def rows_all_empty(rows, fields):
    """True when every row is missing or zero for every metric field."""
    return not rows or all(not row.get(field) for row in rows for field in fields)


def dedupe(items, key=lambda item: item):
    seen, result = set(), []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


# ── Prompt assembly ──────────────────────────────────────────────────────────

def prompt_values(content_type, priority_link, competitor=None):
    cfg = rules()
    template = template_for(content_type)
    tiers = cfg["word_count"]["tiers"]
    table = ["| MSV | Target range |", "|-----|-------------|"]
    for tier in tiers:
        table.append(f"| {tier['label']} | {tier['minimum']:,}–{tier['maximum']:,} words |")
    facts = product_facts()["facts"]
    customers = "\n".join(f"- {c['name']}: {c['url']}" for c in roster())
    alt_rule = ("For alternative pages (an alternative content type or a primary keyword "
                "containing \"alternative\"), include at least one question about a competitor "
                "capability and one about migration.")
    sources = review_sources(competitor)
    source_text = ("Use exactly these review pages: " + ", ".join(f"{k} {v}" for k, v in sources.items())
                   if sources else "If you do not have the exact page URLs, instruct the writer to "
                   "locate them; never guess a review URL.")
    values = {**cfg,
              "style": {**cfg["style"], "banned_list": ", ".join(
                  f'"{p["id"]}"' for p in cfg["style"]["banned_phrases"])},
              "word_count": {**cfg["word_count"], "table": "\n".join(table)},
              "faq": {**cfg["faq"], "alternative_rule": alt_rule},
              "mechanisms": ", ".join(template.get("mechanisms") or [
                  "Raft consensus", "Multi-Raft", "TiKV", "TiFlash", "PD placement driver",
                  "HTAP", "MVCC", "two-phase commit", "online DDL", "native VECTOR type"]),
              "product_facts": "\n".join(f"- {f['statement']}" for f in facts),
              "customer_roster": (
                  "### Verified customer proof points\n\nUse ONLY these customers and URLs for case "
                  "studies or customer proof. If none fits a slot, remove the slot. Never write "
                  "anonymized examples. Customer-specific numbers must match the case study or be "
                  f"marked \"{cfg['case_studies']['verify_marker']}\".\n\n" + customers),
              "review_sources": source_text,
              "priority_anchor": priority_link["anchor"], "priority_url": priority_link["url"]}
    values["template_guidance"] = render(template.get("guidance") or "", values)
    values["template_checklist"] = render(template.get("checklist") or "", values)
    return values


def system_prompt_parts(content_type, priority_link, competitor=None):
    values = prompt_values(content_type, priority_link, competitor)
    base = render((CONFIG_DIR / "prompts/base_instructions.md").read_text(encoding="utf-8"), values)
    checklist = render((CONFIG_DIR / "prompts/quality_checklist.md").read_text(encoding="utf-8"), values)
    return base, checklist


# ── Parsing ──────────────────────────────────────────────────────────────────

def _lines(text, base=0):
    """(offset, line, fenced, fence_info) for each line; fence markers count as fenced."""
    fence, info, offset = None, "", base
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^[ \t]*(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if marker and fence is None:
            fence, info = marker.group(1), marker.group(2).strip()
            yield offset, line, True, info
        elif marker and marker.group(1)[0] == fence[0] and not marker.group(2).strip():
            yield offset, line, True, info
            fence, info = None, ""
        else:
            yield offset, line, fence is not None, info
        offset += len(line)


def sections(content):
    """Brief section name -> (heading_start, body_start, end)."""
    marks = []
    for offset, line, fenced, _ in _lines(content):
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line.strip())
        if match and not fenced and match.group(1).strip().strip("*") in BRIEF_SECTIONS:
            marks.append((match.group(1).strip().strip("*"), offset, offset + len(line)))
    result = {}
    for i, (name, start, body) in enumerate(marks):
        result.setdefault(name, (start, body, marks[i + 1][1] if i + 1 < len(marks) else len(content)))
    return result


def outline_parts(content):
    """Spans inside the outline: preamble, h1 block, and each article H2 block."""
    span = sections(content).get(OUTLINE)
    if not span:
        return None
    _, start, end = span
    h1, h2s = None, []
    for offset, line, fenced, _ in _lines(content[start:end], start):
        if fenced:
            continue
        if re.match(r"^#[ \t]+\S", line) and h1 is None and not h2s:
            h1 = offset
        elif re.match(r"^##[ \t]+\S", line):
            h2s.append((re.sub(r"^##[ \t]+", "", line).strip().strip("*"), offset))
    first_h2 = h2s[0][1] if h2s else end
    parts = {"start": start, "end": end,
             "preamble": (start, h1 if h1 is not None else first_h2),
             "h1": (h1, first_h2) if h1 is not None else None,
             "h2s": [(title, pos, h2s[i + 1][1] if i + 1 < len(h2s) else end)
                     for i, (title, pos) in enumerate(h2s)]}
    return parts


def unit_spans(content):
    """Repair units: brief sections plus outline sub-units (preamble, h1, h2_N)."""
    spans = {name: (head, end) for name, (head, body, end) in sections(content).items()}
    parts = outline_parts(content)
    if parts:
        spans[OUTLINE + "::preamble"] = parts["preamble"]
        if parts["h1"]:
            spans[OUTLINE + "::h1"] = parts["h1"]
        for i, (_, start, end) in enumerate(parts["h2s"], 1):
            spans[f"{OUTLINE}::h2_{i}"] = (start, end)
    return spans


def unit_at(content, offset):
    best = None
    for unit, (start, end) in unit_spans(content).items():
        if start <= offset < end and (best is None or end - start < best[1] - best[0]):
            best = (start, end, unit)
    return best[2] if best else None


def meta_cells(content):
    span = sections(content).get("Meta Elements")
    if not span:
        return {}
    cells = {}
    for line in content[span[1]:span[2]].splitlines():
        match = re.match(r"^\|\s*([^|]+?)\s*\|(.*)\|\s*$", line.strip())
        if match:
            cells.setdefault(match.group(1).strip().strip("*"), match.group(2).strip())
    return cells


def split_cell(value):
    return [v.strip() for v in re.split(r"<br\s*/?>|\n", value or "") if v.strip()]


def table_rows(text):
    rows = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if any(cells) and not all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                rows.append(cells)
    return rows


def plain_words(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    return [w for w in text.split() if re.search(r"[A-Za-z0-9]", w)]


def match_template_sections(titles, template):
    """section id -> H2 index (0-based), using fixed positions first, then patterns."""
    found, used = {}, set()
    n = len(titles)
    for spec in template.get("sections", []):
        pos = spec.get("position")
        if pos is None:
            continue
        index = pos - 1 if pos > 0 else n + pos
        if 0 <= index < n and any(re.search(p, titles[index]) for p in spec["match"]):
            found[spec["id"]] = index
            used.add(index)
    for spec in template.get("sections", []):
        if spec.get("position") is not None:
            continue
        for index, title in enumerate(titles):
            if index not in used and any(re.search(p, title) for p in spec["match"]):
                found[spec["id"]] = index
                used.add(index)
                break
    return found


def competitor_name(content, ctx):
    if ctx.get('competitor'):
        return ctx['competitor']
    parts = outline_parts(content)
    template = template_for(ctx["content_type"])
    spec = glance_spec(template)
    if parts and spec:
        index = match_template_sections([t for t, *_ in parts["h2s"]], template).get("at_a_glance")
        if index is not None:
            _, start, end = parts["h2s"][index]
            rows = table_rows(content[start:end])
            column = spec["columns"].index("{competitor}")
            header = [_header_key(c) for c in rows[0]] if rows else []
            if len(header) > column and header[0] == spec["columns"][0].casefold():
                # The competitor is whichever product column is not TiDB.
                others = [rows[0][i].strip("*") for i, key in enumerate(header[1:], 1) if key != "tidb"]
                if others:
                    return others[0]
    return ctx.get("competitor")


# Header labels models use for the same at-a-glance column.
_HEADER_SYNONYMS = {"category": "criteria", "tidb product": "tidb"}


def _header_key(cell):
    key = " ".join(cell.strip("* ").split()).casefold()
    return _HEADER_SYNONYMS.get(key, key)


def glance_spec(template):
    return next((s["table"] for s in template.get("sections", []) if s["id"] == "at_a_glance" and s.get("table")), None)


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def normalize_glance_table(body, spec, competitor):
    """Rename synonymous header labels and reorder columns to the template order."""
    expected = [c.replace("{competitor}", competitor) for c in spec["columns"]]
    lines = body.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("|")), None)
    if start is None:
        return body
    keys = [_header_key(c) for c in _cells(lines[start])]
    wanted = [e.casefold() for e in expected]
    if sorted(keys) != sorted(wanted):
        return body
    order = [keys.index(w) for w in wanted]
    if [c.strip("* ") for c in _cells(lines[start])] == expected:
        return body
    lines[start] = "| " + " | ".join(expected) + " |\n"
    if order == list(range(len(order))):
        return "".join(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i].lstrip().startswith("|"):
            break
        cells = _cells(lines[i])
        if len(cells) == len(order):
            lines[i] = "| " + " | ".join(cells[j] for j in order) + " |\n"
    return "".join(lines)


# ── Deterministic post-processing ────────────────────────────────────────────

def _set_meta_row(content, label, value, after=None):
    span = sections(content).get("Meta Elements")
    if not span:
        return content
    body = content[span[1]:span[2]]
    row = f"| {label} | {value} |"
    pattern = re.compile(rf"(?mi)^\|\s*\**{re.escape(label)}\**\s*\|.*\|[ \t]*$")
    if pattern.search(body):
        body = pattern.sub(lambda m: row, body, count=1)
    else:
        anchor = re.compile(rf"(?mi)^\|\s*\**{re.escape(after)}\**\s*\|.*\|[ \t]*$") if after else None
        match = anchor.search(body) if anchor else None
        if not match:
            return content
        body = body[:match.end()] + "\n" + row + body[match.end():]
    return content[:span[1]] + body + content[span[2]:]


def _replace_body(content, name, text):
    span = sections(content).get(name)
    if not span:
        return content
    return content[:span[1]] + "\n" + text + "\n\n" + content[span[2]:]


def entity_list(content):
    cfg = rules()["supporting_keywords"]
    tag = re.compile(rf"\s*\(\s*{re.escape(cfg['entity_coverage_tag'])}\s*\)", re.I)
    items = split_cell(meta_cells(content).get("Entity Recognition Focus", ""))
    clean = [(tag.sub("", item).strip(), bool(tag.search(item)), item) for item in items]
    return clean


def apply_deterministic(content, ctx):
    """Set computed fields and prune unverifiable content. Returns (content, notes)."""
    cfg = rules()
    notes = []
    resolution = ctx["resolution"]
    primary = resolution["primary_keyword"]

    # Preserve the author's exact article title independently of model wording.
    parts = outline_parts(content)
    if parts and parts['h1']:
        start,end = parts['h1']
        original = content[start:end]
        title = resolution['title_angle']
        corrected = re.sub(r'^#[ \t]+[^\n]+',lambda _: '# '+title,original,count=1)
        if corrected != original:
            content = content[:start]+corrected+content[end:]
            notes.append('Restored the supplied article H1')

    # Meta: values the model must not choose.
    entities = entity_list(content)
    unique = dedupe(entities, key=lambda e: e[0].casefold())
    if len(unique) != len(entities):
        notes.append(f"Removed {len(entities) - len(unique)} duplicate entities")
    if entities:
        content = _set_meta_row(content, "Entity Recognition Focus", "<br>".join(e[2] for e in unique))
    coverage = [name for name, tagged, _ in unique if tagged]
    supporting = supporting_keywords(resolution, ctx.get("keyword_data"), ctx.get("semrush_related"), coverage)
    ctx["supporting"] = supporting
    cell = "<br>".join(
        f"{r['keyword']} (MSV {format(r['msv'], ',') if r['msv'] is not None else 'unavailable'}{', ' + cfg['supporting_keywords']['entity_coverage_tag'] if r['entity_coverage'] else ''})"
        for r in supporting) or "No measured supporting keywords returned"
    total = resolution["primary_metrics"]["msv"] + sum(r["msv"] for r in supporting if r["msv"] is not None)
    total_note = "; unavailable volumes excluded" if any(r["msv"] is None for r in supporting) else ""
    for label, value, after in [
            ("Target Keyword", primary, None),
            ("Search Intent", intent_label(resolution["search_intent"]), "Target Keyword"),
            ("Supporting Keywords", cell, "Search Intent"),
            ("Total MSV", f"{total:,} (primary {resolution['primary_metrics']['msv']:,} + supporting{total_note})", "Supporting Keywords"),
            ("URL Structure", page_url(ctx["content_type"], primary), "Meta Description")]:
        content = _set_meta_row(content, label, value, after)

    template = template_for(ctx['content_type'])
    parts = outline_parts(content)
    if parts:
        found = match_template_sections([title for title,*_ in parts['h2s']],template)
        spec = glance_spec(template)
        if 'at_a_glance' in found and spec:
            _,start,end = parts['h2s'][found['at_a_glance']]
            body = content[start:end]
            expected = ctx.get('competitor') or competitor_name(content,ctx)
            if expected:
                corrected = normalize_glance_table(body,spec,expected)
                if corrected != body:
                    content=content[:start]+corrected+content[end:]
                    notes.append('Normalized the at-a-glance table columns')

    # Empty data: one line instead of an empty table.
    if ctx.get("backlinks_empty"):
        content = _replace_body(content, "Link Landscape & Acquisition Angle", cfg["empty_data"]["backlinks"])
        notes.append("Backlinks data empty: section replaced with the unavailable-data note")
    if ctx.get("llm_mentions_empty"):
        content = _replace_body(content, "LLM Visibility Snapshot", cfg["empty_data"]["llm_mentions"])
        notes.append("LLM mentions empty: section replaced with the unavailable-data note")

    # CTAs: drop any non-primary CTA line whose URL cannot be verified or that is a placeholder.
    verifier = ctx.get("url_verifier") or (lambda url: False)
    placeholders = [re.compile(p) for p in cfg["cta"]["forbidden_placeholders"]]
    kept = []
    for line in content.splitlines(keepends=True):
        is_cta = re.search(r"(?i)\bcta\b", line) and cfg["cta"]["primary_label"].casefold() not in line.casefold()
        urls = re.findall(r"https?://[^\s)\]>|]+", line)
        bad_url = [u for u in urls if u.rstrip(".,") != ctx["priority_link"]["url"] and not verifier(u.rstrip(".,"))]
        if is_cta and (bad_url or any(p.search(line) for p in placeholders)):
            notes.append("Dropped unverifiable CTA: " + line.strip()[:120])
            continue
        kept.append(line)
    content = "".join(kept)

    # Claim markers.
    content, marked = _mark_customer_claims(content, ctx)
    notes += marked
    content, marked = _mark_competitor_claims(content, ctx)
    notes += marked
    return content, notes


def _mark_customer_claims(content, ctx):
    cfg = rules()["case_studies"]
    marker = cfg["verify_marker"]
    notes, out = [], []
    for line in content.splitlines(keepends=True):
        if marker.casefold() not in line.casefold() and _unmatched_customer_numbers(line, ctx):
            end = len(line.rstrip("\r\n"))
            line = line[:end] + f" ({marker})" + line[end:]
            notes.append("Marked customer claim for verification: " + line.strip()[:100])
        out.append(line)
    return "".join(out), notes


def _unmatched_customer_numbers(line, ctx):
    """Numbers in a line naming a roster customer that its case-study text does not contain."""
    cfg = rules()["case_studies"]
    stripped = re.sub(r"https?://\S+", "", line)
    hits = [c for c in roster() if re.search(rf"(?i)\b{re.escape(c['name'])}\b", stripped)]
    if not hits:
        return []
    for c in hits:  # A number inside the customer's own name is not a claim.
        stripped = re.sub(rf"(?i)\b{re.escape(c['name'])}\b", " ", stripped)
    numbers = [n.group(0).strip() for n in re.finditer(cfg["claim_number_pattern"], stripped, re.I)]
    fetch = ctx.get("case_study_text") or (lambda name: None)
    text = " ".join((fetch(c["name"]) or "") for c in hits).casefold().replace(",", "")
    return [n for n in numbers if n.casefold().replace(",", "") not in text]


def _mark_competitor_claims(content, ctx):
    cfg = rules()["claims"]
    marker = cfg["verify_marker"]
    parts = outline_parts(content)
    template = template_for(ctx["content_type"])
    if not parts:
        return content, []
    found = match_template_sections([t for t, *_ in parts["h2s"]], template)
    notes = []
    for section_id in ("pricing", "at_a_glance"):
        if section_id not in found:
            continue
        _, start, end = outline_parts(content)["h2s"][found[section_id]]
        lines = content[start:end].splitlines(keepends=True)
        for i, line in enumerate(lines):
            sources = section_id != "pricing" and re.match(r"(?i)^\W*sources?\b", line)
            for url in reversed(list(re.finditer(r"https?://[^\s)\]>|]*[^\s)\]>|.,;:]", line))):
                if _pingcap_url(url.group(0)):
                    continue
                before = line[:url.start()]
                claim = before[max(before.rfind(". "), 0):]
                if (sources or re.search(cfg["currency_pattern"], claim, re.I)) \
                        and marker.casefold() not in line[url.end():url.end() + 40].casefold():
                    line = line[:url.end()] + f" ({marker})" + line[url.end():]
                    notes.append(f"Added '{marker}' to a competitor claim in {section_id}")
            lines[i] = line
        content = content[:start] + "".join(lines) + content[end:]
    return content, notes


def _external_urls(text):
    return [u for u in re.findall(r"https?://[^\s)\]>|]+", text)
            if not _pingcap_url(u)]


def _pingcap_url(url):
    host = (urlparse(url).hostname or '').casefold()
    return host == 'pingcap.com' or host.endswith('.pingcap.com')


# ── Checks ───────────────────────────────────────────────────────────────────

def _check(check_id, details, units=()):
    return {"id": check_id, "passed": not details, "details": list(details), "units": sorted(set(u for u in units if u))}


def _section_unit(index):
    return f"{OUTLINE}::h2_{index + 1}"


def run_checks(content, ctx, structural_errors=()):
    cfg = rules()
    checks = []
    resolution = ctx["resolution"]
    primary = resolution["primary_keyword"]
    template = template_for(ctx["content_type"])
    meta = meta_cells(content)
    parts = outline_parts(content) or {"h2s": [], "h1": None, "preamble": (0, 0), "start": 0, "end": 0}
    titles = [t for t, *_ in parts["h2s"]]
    found = match_template_sections(titles, template)
    competitor = competitor_name(content, ctx)

    def block(index):
        _, start, end = parts["h2s"][index]
        return content[start:end]

    # 1. Keyword-driven meta.
    checks.append(_check("meta_target_keyword", [] if meta.get("Target Keyword") == primary else
                         [f"Target Keyword must be {primary!r}"], ["Meta Elements"]))
    expected_intent = intent_label(resolution["search_intent"])
    checks.append(_check("meta_search_intent", [] if meta.get("Search Intent") == expected_intent else
                         [f"Search Intent must be {expected_intent!r}"], ["Meta Elements"]))
    expected_url = page_url(ctx["content_type"], primary)
    checks.append(_check("url_slug", [] if (meta.get("URL Structure") or "").strip("`") == expected_url else
                         [f"URL Structure must be {expected_url}"], ["Meta Elements"]))
    m = cfg["meta"]
    title = meta.get("Meta Title", "")
    problems = []
    if primary.casefold() not in title.casefold():
        problems.append(f"Meta Title must contain {primary!r}")
    if not title.endswith(m["title_suffix"]):
        problems.append(f"Meta Title must end with {m['title_suffix']!r}")
    if not m["title_min_chars"] <= len(title) <= m["title_max_chars"]:
        problems.append(f"Meta Title is {len(title)} characters; allowed {m['title_min_chars']}-{m['title_max_chars']}")
    if re.search(r"\b20\d\d\b", title):
        problems.append("Meta Title must not contain a year")
    checks.append(_check("meta_title", problems, ["Meta Elements"]))
    desc = meta.get("Meta Description", "")
    problems = []
    if len(desc) > m["description_max_chars"]:
        problems.append(f"Meta Description is {len(desc)} characters; maximum {m['description_max_chars']}")
    if not desc:
        problems.append("Meta Description is missing")
    elif primary.casefold() not in desc.casefold() and not {
            t.rstrip("s") for t in tokens(primary)} <= {t.rstrip("s") for t in tokens(desc)}:
        problems.append(f"Meta Description must contain {primary!r} or a close variant")
    checks.append(_check("meta_description", problems, ["Meta Elements"]))

    # 2. Supporting keywords.
    supporting = ctx.get("supporting", [])
    cell = split_cell(meta.get("Supporting Keywords", ""))
    problems = [f"Missing MSV: {item}" for item in cell if not re.search(r"\(MSV (?:[\d,]+|unavailable)", item)
                and item != "No measured supporting keywords returned"]
    problems += [f"Zero-volume keyword without entity coverage: {r['keyword']}"
                 for r in supporting if not r["msv"] and not r["entity_coverage"]]
    checks.append(_check("supporting_keywords", problems, ["Meta Elements"]))
    names = [name.casefold() for name, *_ in entity_list(content)]
    checks.append(_check("entity_dedupe", [f"Duplicate entity: {n}" for n in set(names) if names.count(n) > 1],
                         ["Meta Elements"]))
    checks.append(_check('entity_count', [] if 10 <= len(names) <= 15 else
                         [f'Entity Recognition Focus has {len(names)} entities; expected 10-15'], ['Meta Elements']))
    queries = split_cell(meta.get('Relevant LLM Queries', ''))
    checks.append(_check('llm_query_count', [] if 4 <= len(queries) <= 5 else
                         [f'Relevant LLM Queries has {len(queries)} queries; expected 4-5'], ['Meta Elements']))

    # 3. SERP section.
    s = cfg["serp"]
    relevant = ctx.get("relevant_serp", [])
    checks.append(_check("serp_relevant_pages", [] if len(relevant) >= s["min_relevant_pages"] else
                         [f"Only {len(relevant)} relevant pages in the top {s['depth']} (minimum {s['min_relevant_pages']})"]))
    preamble = content[parts["preamble"][0]:parts["preamble"][1]]
    ranks = {str(r.get("rank")) for r in relevant}
    rows = [r for r in table_rows(preamble) if r and r[0] != "#"]
    problems = [f"Excluded or irrelevant page in SERP table: rank {r[0]}" for r in rows if r[0] not in ranks]
    problems += [f"SERP section mentions excluded pages ({p})" for p in s["excluded_page_patterns"]
                 if re.search(p, preamble)]
    if not s["table_min_rows"] <= len(rows) <= s["table_max_rows"]:
        problems.append(f"SERP table has {len(rows)} rows; allowed {s['table_min_rows']}-{s['table_max_rows']}")
    checks.append(_check("serp_table", problems, [OUTLINE + "::preamble"]))
    source = ctx.get("serp_source_keyword")
    checks.append(_check("aio_patterns_source", [] if source == primary else
                         [f"AI Overview patterns must come from the {primary!r} SERP (got {source!r})"]))

    # 4. Key Takeaways and E-E-A-T.
    h1 = content[parts["h1"][0]:parts["h1"][1]] if parts["h1"] else ""
    h1_titles = [line[2:].strip().strip('*') for line in h1.splitlines() if line.startswith('# ')]
    expected_h1 = ' '.join(resolution['title_angle'].split()).casefold()
    checks.append(_check('article_title', [] if len(h1_titles) == 1 and
                         ' '.join(h1_titles[0].split()).casefold() == expected_h1 else
                         [f'Article H1 must be exactly: # {resolution["title_angle"]}'], [OUTLINE+'::h1']))
    checks.append(_check("key_takeaways", _takeaway_problems(h1), [OUTLINE + "::h1"]))
    problems = [f"Intro guidance is missing the {group['id'].replace('_', ' ')} requirement"
                for group in cfg["eeat"] if not all(re.search(p, h1) for p in group["all"])]
    checks.append(_check("eeat", problems, [OUTLINE + "::h1"]))

    # 5. Page-type template.
    problems = []
    max_h2 = template.get("max_h2", 10)
    if len(titles) > max_h2:
        problems.append(f"{len(titles)} H2s; maximum {max_h2}")
    for spec in template.get("sections", []):
        if spec["id"] not in found:
            problems.append(f"Missing required H2: {spec['label']}")
    order = [found[s["id"]] for s in template.get("sections", []) if s["id"] in found]
    if order != sorted(order):
        problems.append("Template H2s are out of order")
    checks.append(_check("template_sections", problems, [OUTLINE] if problems else []))
    for spec in template.get("sections", []):
        if spec["id"] not in found:
            continue
        index = found[spec["id"]]
        text = block(index)
        unit = _section_unit(index)
        problems = []
        if spec.get("key_differences") and not re.search(r"(?mi)^###\s+\**key differences\b", text):
            problems.append(f"{titles[index]!r} needs a 'Key differences' H3")
        for term in spec.get("required_terms", []):
            if not re.search(term, text):
                problems.append(f"{titles[index]!r} is missing required guidance matching {term!r}")
        if spec.get("requires_mechanism"):
            mechanisms = template.get("mechanisms") or []
            if not any(re.search(rf"(?i)\b{re.escape(x)}\b", text) for x in mechanisms):
                problems.append(f"{titles[index]!r} does not name a TiDB mechanism")
        if spec.get("table"):
            problems += _glance_problems(text, spec["table"], competitor)
        if spec.get("reviews"):
            problems += _review_problems(text, competitor)
        if spec.get("pricing"):
            problems += _pricing_problems(text, competitor)
        if problems or spec.get("key_differences") or spec.get("required_terms") or spec.get("table"):
            checks.append(_check("section_" + spec["id"], problems, [unit]))

    # Ratings are never invented anywhere.
    rating = [f"Possible invented rating: {mt.group(0)!r}" for p in cfg["reviews"]["invented_rating_patterns"]
              for mt in re.finditer(p, content)]
    checks.append(_check("no_invented_ratings", rating,
                         [unit_at(content, mt.start()) for p in cfg["reviews"]["invented_rating_patterns"]
                          for mt in re.finditer(p, content)]))
    schema_span = sections(content).get('Schema Markup Recommendations')
    schema_text = content[schema_span[1]:schema_span[2]] if schema_span else ''
    checks.append(_check('schema_types', ['ComparisonTable is not a Schema.org type; use documented schema types.']
                         if re.search(r'\bComparisonTable\b',schema_text) else [], ['Schema Markup Recommendations']))

    # 6. CTA.
    checks.append(_cta_check(content, ctx, parts, found, template))

    # 7. Internal links.
    checks += _link_checks(content, ctx, parts)

    # Case studies.
    checks.append(_case_study_check(content, ctx))

    # 8. FAQs.
    checks.append(_faq_check(content, ctx, parts, found, template, meta, competitor))

    # 9. Style lint.
    checks.append(_lint_check(content))

    # 10. Empty data.
    problems = []
    bodies = {name: content[b:e] for name, (h, b, e) in sections(content).items()}
    for flag, name, key in [("backlinks_empty", "Link Landscape & Acquisition Angle", "backlinks"),
                            ("llm_mentions_empty", "LLM Visibility Snapshot", "llm_mentions")]:
        if ctx.get(flag) and (bodies.get(name, "").strip() != cfg["empty_data"][key] or "|" in bodies.get(name, "")):
            problems.append(f"{name} must be the one-line unavailable-data note")
    checks.append(_check("empty_data", problems))

    # 11. Fact-check pass.
    checks.append(_facts_check(content))
    checks.append(_sql_check(content))

    # 12. Brief length: the brief stays a scannable plan, not a draft article.
    checks.append(_length_check(content, parts, found, template))

    # 13. Word count.
    tier = ctx.get("plan") or {}
    problems = [e for e in structural_errors if "budget" in e.lower()]
    if tier.get("primary_keyword_msv") is None:
        problems.append("Word-count tier has no primary keyword MSV")
    checks.append(_check("word_count", problems))
    other = [e for e in structural_errors if "budget" not in e.lower()]
    checks.append(_check("structure", other))
    return checks


def _takeaway_problems(h1):
    cfg = rules()["key_takeaways"]
    lines = [l for l in h1.splitlines()[1:] if l.strip()]
    label_at = next((i for i, l in enumerate(lines[:cfg["max_lines_after_h1"]])
                     if cfg["label"].casefold() in l.casefold()), None)
    if label_at is None:
        return [f"No '{cfg['label']}' block directly under the H1"]
    bullets = []
    for line in lines[label_at + 1:]:
        if re.match(r"^\s*[-*]\s+", line):
            bullets.append(re.sub(r"^\s*[-*]\s+", "", line))
        else:
            break
    problems = []
    if not cfg["min_bullets"] <= len(bullets) <= cfg["max_bullets"]:
        problems.append(f"{cfg['label']} has {len(bullets)} bullets; allowed {cfg['min_bullets']}-{cfg['max_bullets']}")
    for bullet in bullets:
        count = len(plain_words(bullet))
        if not cfg["min_words"] <= count <= cfg["max_words"]:
            problems.append(f"Takeaway has {count} words (allowed {cfg['min_words']}-{cfg['max_words']}): {bullet[:60]}")
    return problems


def _glance_problems(text, spec, competitor):
    rows = table_rows(text)
    if not rows:
        return ["At-a-glance table is missing"]
    header, data = rows[0], rows[1:]
    problems = []
    expected = [c.replace("{competitor}", "*") for c in spec["columns"]]
    ci = spec["columns"].index("{competitor}")
    if len(header) != len(expected) or any(e != "*" and h.strip("*").casefold() != e.casefold()
                                           for h, e in zip(header, expected)):
        problems.append("At-a-glance columns must be " + " | ".join(c.replace("{competitor}", competitor or "[Competitor]") for c in spec["columns"]))
    elif re.fullmatch(r"(?i)\[?competitor\]?", header[ci].strip("*")):
        problems.append(f"At-a-glance column {ci + 1} must name the competitor")
    elif competitor and header[ci].strip('*').casefold() != competitor.casefold():
        problems.append(f'At-a-glance competitor must be {competitor!r}, not {header[ci]!r}')
    if not spec["min_rows"] <= len(data) <= spec["max_rows"]:
        problems.append(f"At-a-glance table has {len(data)} rows; allowed {spec['min_rows']}-{spec['max_rows']}")
    categories = [r[0] for r in data if r]
    for cat in spec["required_categories"]:
        if not any(re.search(cat["match"], c) for c in categories):
            problems.append(f"At-a-glance table is missing the {cat['label']} row")
    for row in data:
        if len(row) != len(expected):
            problems.append('Every at-a-glance row must have all required columns')
        elif competitor and competitor.casefold() == 'mariadb' and re.search(r'(?i)vector',row[0]) and re.search(
                r'(?i)\b(?:no|without|lacks?|does not (?:have|support))\b.{0,50}\b(?:native|vector)',row[ci]) and not re.search(r'\b(?:10|11)\.\d+',row[ci]):
            problems.append('MariaDB has a native VECTOR type from 11.7.1. State the version and check https://mariadb.com/docs/server/reference/sql-structure/vectors/vector before comparing vector support.')
        elif re.fullmatch(spec["empty_cell_pattern"], row[ci].strip("* ").casefold()):
            problems.append(f"Competitor cell is empty for {row[0]!r}; describe the competitor's capability")
    if spec.get("requires_sources"):
        marker = rules()["claims"]["verify_marker"]
        source = [l for l in text.splitlines() if re.match(r"(?i)^\W*sources?\b", l)]
        if not source or not _external_urls(" ".join(source)) or marker.casefold() not in " ".join(source).casefold():
            problems.append(f"At-a-glance table needs a Sources line with complete https:// competitor source URLs marked '{marker}'")
    return problems


def _review_problems(text, competitor):
    cfg = rules()["reviews"]
    problems = [f"Reviews section must name {p}" for p in cfg["platforms"] if p.casefold() not in text.casefold()]
    problems += [f"Reviews section must instruct capturing {t.split(')')[-1]}" for t in cfg["required_terms"]
                 if not re.search(t, text)]
    for platform, url in review_sources(competitor).items():
        if url not in text:
            problems.append(f"Reviews section must link the configured {platform} page {url}")
    return problems


def _sentences(line):
    """Split prose into sentences without breaking URLs (a URL never has '. ' plus a capital)."""
    return re.split(r"(?<=[.!?])\s+(?=[A-Z*])", line)


def _pricing_problems(text, competitor):
    cfg = rules()["claims"]
    problems = []
    for line in (s for l in text.splitlines() for s in _sentences(l)):
        if not re.search(cfg["currency_pattern"], line, re.I):
            continue
        about_tidb = re.search(r"\bTiDB\b", line) and not (competitor and competitor.casefold() in line.casefold())
        if about_tidb:
            if not any(_pingcap_url(u) for u in re.findall(r"https?://[^\s)\]>|]+",line)):
                problems.append("TiDB price without a pingcap.com source; describe the billing model instead: " + line.strip()[:80])
            continue
        if not _external_urls(line):
            problems.append("Competitor price without a source URL: " + line.strip()[:80])
        if not re.search(cfg["year_pattern"], line):
            problems.append("Competitor price without a year: " + line.strip()[:80])
        if cfg["verify_marker"].casefold() not in line.casefold():
            problems.append(f"Competitor price without '{cfg['verify_marker']}': " + line.strip()[:80])
    return problems


def _cta_check(content, ctx, parts, found, template):
    cfg = rules()["cta"]
    link = ctx["priority_link"]
    exact = f"[{link['anchor']}]({link['url']})"
    label = re.compile(rf"(?i)\**{re.escape(cfg['primary_label'])}\**\s*:")
    problems, units = [], []
    section_id = template.get("primary_cta_section")
    index = found.get(section_id)
    if index is None:
        problems.append("Closing H2 for the primary CTA is missing")
    outline_lines = [(o, l) for o, l, fenced, _ in _lines(content[parts["start"]:parts["end"]], parts["start"])
                     if label.search(l)]
    if len(outline_lines) != 1:
        problems.append(f"Outline has {len(outline_lines)} primary CTAs; exactly one is required")
        units += [unit_at(content, o) for o, _ in outline_lines]
    for offset, line in outline_lines:
        if exact not in line:
            problems.append(f"Primary CTA must be exactly {exact}")
        if index is not None and unit_at(content, offset) != _section_unit(index):
            problems.append("Primary CTA must sit in the closing H2")
            units.append(unit_at(content, offset))
    if index is not None:
        units.append(_section_unit(index))
    span = sections(content).get("CTAs")
    cta_body = content[span[1]:span[2]] if span else ""
    cta_lines = [l for l in cta_body.splitlines() if label.search(l)]
    if len(cta_lines) != 1 or exact not in cta_lines[0]:
        problems.append("CTAs section must list the primary CTA exactly once, verbatim")
        units.append("CTAs")
    elsewhere = [o for o, l, *_ in _lines(content) if label.search(l)
                 and not (parts["start"] <= o < parts["end"]) and not (span and span[0] <= o < span[2])]
    if elsewhere:
        problems.append("Primary CTA appears outside the closing H2 and CTAs section")
        units += [unit_at(content, o) for o in elsewhere]
    placeholders = [p for p in cfg["forbidden_placeholders"] if re.search(p, content)]
    problems += [f"CTA placeholder text present ({p})" for p in placeholders]
    return _check("primary_cta", problems, units)


def _link_rows(content):
    span = sections(content).get("Internal Links")
    if not span:
        return []
    rows = []
    for cells in table_rows(content[span[1]:span[2]]):
        if len(cells) == 4 and cells[0] != "Section (H2)":
            urls = re.findall(r"https?://[^\s<>\])]+", cells[2])
            rows.append({"section": cells[0].strip("*"), "anchor": cells[1].strip(), "url": urls[0] if urls else ""})
    return rows


def _link_checks(content, ctx, parts):
    cfg = rules()["internal_links"]
    rows = _link_rows(content)
    link = ctx["priority_link"]
    checks = []
    problems = [] if any(r["url"] == link["url"] and r["anchor"] == link["anchor"] for r in rows) else [
        f"Internal Links must include the priority link {link['url']} with anchor {link['anchor']!r}"]
    checks.append(_check("internal_links_priority", problems, ["Internal Links"]))
    titles = {t.casefold(): (start, end) for t, start, end in parts["h2s"]}
    template = template_for(ctx["content_type"])
    found = match_template_sections([t for t, *_ in parts["h2s"]], template)
    problems = []
    for req in ctx.get("required_links") or []:
        row = next((r for r in rows if r["url"] == req["url"]), None)
        if not row or row["anchor"] != req["anchor"]:
            problems.append(f"Required link {req['url']} with anchor {req['anchor']!r} is missing")
            continue
        wanted = req.get("section", "")
        index = found.get(wanted)
        target = parts["h2s"][index][0] if index is not None else wanted
        if target.casefold() not in row["section"].casefold() and row["section"].casefold() not in target.casefold():
            problems.append(f"Required link {req['url']} must be placed in {wanted!r}, not {row['section']!r}")
    checks.append(_check("internal_links_required", problems, ["Internal Links"]))
    exempt = {link["url"]} | {r["url"] for r in ctx.get("required_links") or []}
    candidates = {c["url"]: c for c in ctx.get("link_candidates") or []}
    blocks = [_stems(tokens(content[start:end])) for _, start, end in parts["h2s"]]
    ubiquitous = {t for t in set().union(*blocks)
                  if sum(t in b for b in blocks) >= cfg["section_ubiquity_fraction"] * len(blocks)} if blocks else set()
    ignore = set(cfg["section_ignore_tokens"]) | _stems(tokens(ctx["resolution"]["primary_keyword"])) | ubiquitous
    generic = set(cfg["section_ignore_tokens"]) | set(rules()['supporting_keywords']['generic_relevance_tokens']) | {'vs','versus','mysql','mariadb','postgres','postgresql','supabase'}
    generic = _stems(generic)
    problems = []
    for row in rows:
        if row["url"] in exempt or not row["url"]:
            continue
        span = titles.get(row["section"].casefold())
        if not span:
            continue
        page = candidates.get(row["url"], {})
        page_tokens = tokens(" ".join([page.get("title", ""), page.get("h1", ""), page.get("primary_keyword", ""), page.get("meta_description", ""),
                                       urlparse(row["url"]).path.replace("-", " ")]))
        section_tokens = _stems(tokens(content[span[0]:span[1]]))
        shared = (_stems(page_tokens) & section_tokens) - ignore
        # A recurring technical term still establishes a specific relationship.
        # Broad product names and generic words alone cannot rescue a bad placement.
        technical_overlap = (_stems(page_tokens) & section_tokens) & _stems(cfg.get('section_technical_tokens', [])) - generic
        shared |= technical_overlap
        if len(shared) < cfg["section_min_shared_tokens"]:
            problems.append(f"{row['url']} shares no topic words with {row['section']!r}")
    checks.append(_check("internal_links_relevance", problems, ["Internal Links"]))
    return checks


def _stems(words):
    stems = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}
    families = {'compatible':'compatibility', 'scaling':'scale', 'scalable':'scale'}
    return {families.get(word,word) for word in stems}


def _case_study_check(content, ctx):
    cfg = rules()["case_studies"]
    allowed = {c["url"] for c in roster()}
    problems, units = [], []
    for pattern in cfg["anonymized_patterns"]:
        for mt in re.finditer(pattern, content):
            problems.append(f"Anonymized customer example: {mt.group(0)!r}")
            units.append(unit_at(content, mt.start()))
    for mt in re.finditer(r"https?://(?:www\.)?pingcap\.com/case-stud(?:y|ies)/[^\s)\]|>]+", content):
        if mt.group(0).rstrip(".,") not in allowed and not mt.group(0).rstrip("/").endswith("/case-studies"):
            problems.append(f"Case-study URL not in the customer roster: {mt.group(0)}")
            units.append(unit_at(content, mt.start()))
    for offset, line, *_ in _lines(content):
        unmatched = _unmatched_customer_numbers(line, ctx)
        if unmatched and cfg["verify_marker"].casefold() not in line.casefold():
            problems.append(f"Customer claim does not match the case study and is unmarked: {line.strip()[:80]}")
            units.append(unit_at(content, offset))
    return _check("case_studies", problems, units)


def _faq_check(content, ctx, parts, found, template, meta, competitor):
    cfg = rules()["faq"]
    index = found.get(template.get("faq_section"))
    if index is None:
        return _check("faqs", ["FAQ H2 is missing"], [OUTLINE])
    _, start, end = parts["h2s"][index]
    text = content[start:end]
    unit = _section_unit(index)
    blocks = re.split(r"(?m)^###\s+", text)[1:]
    problems = []
    if not cfg["min_questions"] <= len(blocks) <= cfg["max_questions"]:
        problems.append(f"FAQ has {len(blocks)} questions; allowed {cfg['min_questions']}-{cfg['max_questions']}")
    sources = [tokens(q) for q in (ctx.get("paa") or []) + split_cell(meta.get("Relevant LLM Queries", ""))]
    questions = []
    for blk in blocks:
        question = blk.splitlines()[0].strip().strip("*")
        questions.append(question)
        q = tokens(question)
        if q and not any(len(q & src) >= cfg["source_overlap"] * len(q) for src in sources):
            problems.append(f"FAQ question not sourced from PAA or Relevant LLM Queries: {question!r}")
        label = re.search(rf"(?mi)^\**{re.escape(cfg['answer_label'])}:?\**:?\s*$", blk)
        if not label:
            problems.append(f"FAQ {question!r} has no '{cfg['answer_label']}:' bullets")
            continue
        answer = []
        for line in blk[label.end():].splitlines():
            if not line.strip():
                continue
            if re.match(r"^\s*(?:\*\*[^*]+:\*\*|#)", line):
                break
            answer.append(line)
        bullets = [l for l in answer if re.match(r"^\s*[-*]\s+\S", l)]
        if len(bullets) != len(answer):
            problems.append(f"FAQ {question!r} answer guidance must be bullet points only")
        if not 1 <= len(bullets) <= cfg["max_bullets"]:
            problems.append(f"FAQ {question!r} has {len(bullets)} answer bullets; allowed 1-{cfg['max_bullets']}")
    problems += [f"FAQ guidance uses a prose-length instruction ({p})" for p in cfg["forbidden_guidance"]
                 if re.search(p, text)]
    if ctx["content_type"] == "alternative" or "alternative" in ctx["resolution"]["primary_keyword"].casefold():
        for rule in cfg["alternative_questions"]:
            if rule.get("requires_competitor"):
                ok = competitor and any(competitor.casefold() in q.casefold()
                                        and not re.search(rule["exclude_pattern"], q) for q in questions)
            else:
                ok = any(re.search(rule["pattern"], q) for q in questions)
            if not ok:
                problems.append(f"Alternative page FAQ needs {rule['description']}")
    return _check("faqs", problems, [unit])


def _lint_check(content):
    cfg = rules()["style"]
    problems, units = [], []
    for char, name in cfg["forbidden_characters"].items():
        for mt in re.finditer(re.escape(char), content):
            line_end = content.find("\n", mt.start())
            line = content[content.rfind("\n", 0, mt.start()) + 1:line_end if line_end >= 0 else None]
            problems.append(f"{name}: {line.strip()[:80]}")
            units.append(unit_at(content, mt.start()))
    for phrase in cfg["banned_phrases"]:
        for mt in re.finditer(phrase["pattern"], content):
            problems.append(f"Banned phrase {phrase['id']!r}: {mt.group(0)!r}")
            units.append(unit_at(content, mt.start()))
    for mt in re.finditer(r"[^.!?\n|]+", content):
        sentence = mt.group(0)
        if re.search(r"\bTiDB\b", sentence):
            for claim in cfg["tidb_claims"]:
                if re.search(claim["pattern"], sentence):
                    problems.append(f"TiDB claim {claim['id']!r}: {sentence.strip()[:80]}")
                    units.append(unit_at(content, mt.start()))
                    break
    return _check("style_lint", problems, units)


def _facts_check(content):
    problems, units = [], []
    for fact in product_facts()["facts"]:
        for check in fact["checks"]:
            if check["type"] == "forbidden_pattern":
                for mt in re.finditer(check["pattern"], content):
                    # A sentence that already carries the required qualifier is not the claim.
                    start = max(content.rfind(". ", 0, mt.start()), content.rfind("\n", 0, mt.start())) + 1
                    ends = [i for i in (content.find(". ", mt.end()), content.find("\n", mt.end())) if i >= 0]
                    sentence = content[start:min(ends) if ends else len(content)]
                    if check.get("allow_if") and re.search(check["allow_if"], sentence):
                        continue
                    problems.append(f"{fact['id']}: {check['message']}")
                    units.append(unit_at(content, mt.start()))
            elif check["type"] == "number_in_context":
                expected = {str(e).casefold() for e in check["expected"]}
                for mt in re.finditer(check["context"], content):
                    start = max(content.rfind(".", 0, mt.start()), content.rfind("\n", 0, mt.start())) + 1
                    stop = min(i for i in (content.find(".", mt.end()), content.find("\n", mt.end()), len(content)) if i >= 0)
                    for num in re.finditer(check["pattern"], content[start:stop]):
                        value = num.group(1).casefold()
                        if value not in expected and str(_NUMBER_WORDS.get(value, value)) not in expected:
                            problems.append(f"{fact['id']}: {check['message']}")
                            units.append(unit_at(content, mt.start()))
    return _check("product_facts", dedupe(problems), units)


def tidb_sql_snippets(content):
    """(offset, code) for fenced snippets treated as TiDB/MySQL SQL."""
    cfg = product_facts()["sql"]
    snippets, current = [], None
    lines = list(_lines(content))
    for i, (offset, line, fenced, info) in enumerate(lines):
        is_marker = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
        if fenced and is_marker and current is None:
            lang = (info.split() or [""])[0].casefold()
            before = "".join(l for _, l, *_ in lines[max(0, i - 3):i])
            if lang in cfg["other_languages"] or (not lang and not re.search(cfg["context_pattern"], before)):
                current = False
            elif lang in cfg["tidb_languages"] or not lang:
                if lang == "sql" and re.search(r"(?i)postgres|pgvector", before):
                    current = False
                else:
                    current = [offset, ""]
            else:
                current = False
        elif fenced and is_marker and current is not None:
            if current:
                snippets.append(tuple(current))
            current = None
        elif current:
            current[1] += line
    return snippets


def _sql_check(content):
    import sqlglot
    from sqlglot import exp
    cfg = product_facts()["sql"]
    message = next(c["message"] for f in product_facts()["facts"] for c in f["checks"] if c["type"] == "sql_vector_nullsafe")
    vector = re.compile(cfg["vector_column_pattern"])
    problems, units = [], []
    for offset, code in tidb_sql_snippets(content):
        if "<=>" not in code:
            continue
        flagged = False
        try:
            for tree in sqlglot.parse(code, read="mysql"):
                for node in (tree.find_all(exp.NullSafeEQ) if tree else []):
                    sides = [node.this, node.expression]
                    if any(isinstance(s, exp.Column) and vector.search(s.name) or
                           isinstance(s, exp.Literal) and s.is_string and s.this.strip().startswith("[") or
                           vector.search(s.sql()) for s in sides):
                        flagged = True
        except sqlglot.errors.SqlglotError:
            flagged = any("<=>" in l and vector.search(l) for l in code.splitlines())
        if flagged:
            problems.append("TiDB/MySQL SQL uses <=> on a vector column: " + message)
            units.append(unit_at(content, offset))
    for mt in re.finditer(r"`([^`\n]*<=>[^`\n]*)`", content):
        line = content[content.rfind("\n", 0, mt.start()) + 1:mt.start()]
        if vector.search(mt.group(1)) and re.search(cfg["context_pattern"], line + mt.group(1)) \
                and not re.search(r"(?i)pgvector|postgres", line):
            problems.append("Inline TiDB/MySQL SQL uses <=> on a vector: " + message)
            units.append(unit_at(content, mt.start()))
    return _check("sql_vector_syntax", problems, units)


def brief_words(text):
    """Words a writer reads; fenced code samples are not counted."""
    return len(plain_words(re.sub(r"(?ms)^```.*?^```", "", text)))


def _length_check(content, parts, found, template):
    cfg = rules()["brief_length"]
    problems, units, sizes = [], [], {}
    for name, (head, body, end) in sections(content).items():
        if name == OUTLINE:
            continue
        sizes[name] = brief_words(content[body:end])
        cap = cfg["section_max_words"].get(name)
        if cap and sizes[name] > cap:
            problems.append(f"{name} is {sizes[name]} words; maximum {cap}")
            units.append(name)
    caps = {index: spec.get("max_words") for spec in template.get("sections", [])
            for sid, index in found.items() if sid == spec["id"] and spec.get("max_words")}
    faq = found.get(template.get("faq_section"))
    outline = [(OUTLINE + "::preamble", parts["preamble"], cfg["outline_preamble_max_words"])]
    if parts.get("h1"):
        outline.append((OUTLINE + "::h1", parts["h1"], cfg["h1_max_words"]))
    for i, (title, start, end) in enumerate(parts["h2s"]):
        cap = caps.get(i) or (cfg["faq_max_words"] if i == faq else cfg["h2_max_words"])
        outline.append((_section_unit(i), (start, end), cap))
    for unit, (start, end), cap in outline:
        sizes[unit] = brief_words(content[start:end])
        if sizes[unit] > cap:
            problems.append(f"{unit.split('::')[-1]} is {sizes[unit]} words; maximum {cap}. "
                            "Keep a Target line, a one-sentence rationale, and 2 to 3 short guidance bullets; H3s are heading lines")
            units.append(unit)
    total = brief_words(content)
    if total > cfg["max_words"]:
        problems.append(f"Brief is {total} words; maximum {cfg['max_words']}. Shorten the longest sections")
        # Without a per-unit overrun, shorten the three longest units.
        if not units:
            units = sorted(sizes, key=sizes.get, reverse=True)[:3]
    return _check("brief_length", problems, units)


def failures(checks):
    return [f"{c['id']}: {d}" for c in checks if not c["passed"] for d in c["details"]]


# ── Repair ───────────────────────────────────────────────────────────────────

def repair_plan(checks):
    """Units to regenerate, with the violations for each. Unattributed failures are not repairable."""
    plan = {}
    for check in checks:
        if check["passed"]:
            continue
        for unit in check["units"]:
            plan.setdefault(unit, []).extend(f"{check['id']}: {d}" for d in check["details"])
    if OUTLINE in plan:
        for unit in [u for u in plan if u.startswith(OUTLINE + "::")]:
            plan[OUTLINE].extend(plan.pop(unit))
    return {unit: dedupe(v) for unit, v in plan.items()}


def repair_prompt(content, plan):
    spans = unit_spans(content)
    parts = ["The brief below failed automated checks. Rewrite ONLY the listed units so "
             "every violation is fixed. Correct headings, Target lines, or structure when "
             "a listed violation requires it. Preserve unrelated content. Follow all original rules.",
             "Return each unit exactly in this form and nothing else:\n<<<UNIT id>>>\n"
             "...full replacement text...\n<<<END UNIT>>>"]
    for unit, problems in plan.items():
        if unit not in spans:
            continue
        start, end = spans[unit]
        parts.append(f"## Unit {unit}\nViolations:\n" + "\n".join("- " + p for p in problems)
                     + f"\nCurrent text:\n<<<UNIT {unit}>>>\n{content[start:end].rstrip()}\n<<<END UNIT>>>")
    return "\n\n".join(parts)


def apply_repair(content, response, plan):
    replacements = dict(re.findall(r"<<<UNIT ([^>]+)>>>\n(.*?)\n?<<<END UNIT>>>", response, re.S))
    applied = []
    # Replace from the end so earlier offsets stay valid.
    spans = unit_spans(content)
    for unit in sorted((u for u in plan if u in replacements and u in spans),
                       key=lambda u: spans[u][0], reverse=True):
        start, end = spans[unit]
        new = replacements[unit].rstrip() + "\n\n"
        if unit.startswith(OUTLINE + "::h2_") and not new.lstrip().startswith("## "):
            continue
        content = content[:start] + new + content[end:]
        applied.append(unit)
    return content, sorted(applied)
