# PingCAP Content Brief Generator

A CLI tool that generates SEO/AEO content briefs for PingCAP writers by pulling live data from DataForSEO, SEMrush, the PingCAP sitemap, and Claude.

## What it does

Runs required Stage 0 keyword resolution and confirmation, followed by the existing 11-step pipeline:

1. DataForSEO keyword data
2. SERP top-10 + People Also Ask
3. Competitor heading scrape (top 3 pages)
4. LLM mentions check
5. Competitor backlinks
6. SEMrush keyword intent classification
7. SEMrush related/LSI keywords
8. SEMrush keyword gap vs competitors
9. SEMrush domain authority (competitor domains)
10. Sitemap-backed internal link candidates (verified pingcap.com pages)
11. Claude generates the structured brief

Outputs a structured content brief to:

- Google Docs (saved to a "Content Briefs" Drive folder)
- Local .md file (always saved before Google Docs export)

## Content types

blog  listicle  comparison  product  playbook  solution

## Setup

### 1. Clone and install

git clone [https://github.com/YOUR\_ORG/pingcap-brief-generator](https://github.com/YOUR_ORG/pingcap-brief-generator)
cd pingcap-brief-generator
pip install -r requirements.txt

### 2. Configure environment

cp .env.example .env

# Edit .env and fill in your API keys

Required keys:

- ANTHROPIC\_API\_KEY — Claude API (claude-sonnet-4-20250514 by default)
- DATAFORSEO\_LOGIN + DATAFORSEO\_PASSWORD — DataForSEO account credentials
- SEMRUSH\_API\_KEY — optional but recommended (SEMrush Pro plan, Standard API)

### 3. Google Docs output (optional)

To save briefs to Google Docs:

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Desktop application type)
3. Download the credentials JSON and rename it to credentials.json
4. Place credentials.json in the same folder as brief.py
5. Run the script once — a browser window will open for Google auth
6. After auth, token.json is saved automatically and reused on future runs

Enable these APIs in your Google Cloud project:

- Google Docs API
- Google Drive API

### 4. Run

Every run needs a priority link: the page's single primary CTA and a required
internal link, used verbatim.

```bash
python brief.py "TiDB Cloud Zero vs Supabase for AI Agent Backends" comparison \
  --priority-link-url https://www.pingcap.com/ai/ \
  --priority-link-anchor "distributed SQL database for AI applications"
```

To supply a keyword for validation, use the separate override flag (confirmation is still required):

```bash
python brief.py "TiDB Cloud is the unified database layer for AI agents" solution \
  --primary-keyword-override "AI agent memory" \
  --priority-link-url https://www.pingcap.com/ai/ --priority-link-anchor "AI database"
```

Optional: `--required-links links.json`, a list of `{"url", "anchor", "section"}`
objects. `section` is a template section id (for example `pricing`) or H2 text.
The priority link and required links must be live, indexable PingCAP pages or the
run stops before generation.

## Model configuration

By default the script uses:

- claude-sonnet-4-20250514 for brief generation
- claude-haiku-4-5-20251001 for doc title summarisation

Override either by setting ANTHROPIC\_MODEL or ANTHROPIC\_HAIKU\_MODEL in .env.

To use OpenAI instead of Anthropic, replace the anthropic client calls in generate\_brief() and summarize\_title() with OpenAI SDK calls pointing to gpt-4o or equivalent. The prompt system is model-agnostic — the prompts themselves require no changes.

## Brief quality

Rules, templates and lists live in `config/`, not in code:

| File | Holds |
|---|---|
| `config/brief_rules.yaml` | Meta limits, slug prefixes, intent labels, SERP depth, takeaway and FAQ limits, E-E-A-T terms, banned words, word-count tiers, internal-link weights |
| `config/templates/*.yaml` | Page-type outlines (comparison/alternative, listicle, solution, default): required H2s, order, word weights, guidance. Comparison and listicle outlines follow the live pages under https://www.pingcap.com/compare/ |
| `config/prompts/*.md` | Base instructions and quality checklist, with `{{...}}` placeholders filled from the rules |
| `config/customer_roster.yaml` | The only customers and URLs a brief may cite |
| `config/product_facts.yaml` | Product facts the brief is checked against |
| `config/review_sources.yaml` | Optional, human-verified G2/Capterra/Clutch URLs per competitor |

`brief_quality.py` applies them in three passes:

1. **Deterministic fields.** Code sets the target keyword, search intent, URL slug
   (from the primary keyword, e.g. `/compare/supabase-alternative`), supporting
   keywords with MSV, and Total MSV. It drops CTAs whose URLs cannot be verified,
   replaces empty data sections with a one-line note, deduplicates entities, and
   marks unmatched customer numbers and sourced competitor prices for verification.
2. **Checks.** Every rule is a named check: meta title/description lengths counted
   in code, SERP table (relevant pages from the top 20 only), Key Takeaways, E-E-A-T,
   template H2s and order, at-a-glance table, Key differences H3, decision H3s, pricing,
   one primary CTA, internal links (priority, required, section relevance), case
   studies, FAQs, style lint (em dashes, banned words, TiDB superlatives), product
   facts, TiDB SQL `<=>` misuse (parsed with sqlglot), empty data, word count.
3. **One repair round.** Failing sections are regenerated once, alone, and spliced
   back. If anything still fails, the brief is rejected.

Every check is written to `validation.json` with `passed` and `details`, both for
rejected drafts (`brief_failed_*/`) and successful runs (`brief_run_*/`). Fewer than
five relevant pages in the top-20 SERP rejects the run before the paid generation call.

## Internal-link inventory

Internal-link recommendations are sourced from the verified sitemap index at
`https://www.pingcap.com/sitemap_index.xml`. The generator never invents a URL.

Refresh the local inventory manually with:

```bash
python build_sitemap_inventory.py
```

The command writes `sitemap_inventory.json`, storing each eligible page's final URL,
title, H1, meta description, mapped primary keyword when available, page type,
publish date, HTTP status, canonical URL, and validation timestamp. Sitemap membership
alone is not treated as proof that a page is live: the builder follows redirects,
requires a final HTTP 200 response on `pingcap.com`, and excludes `noindex` pages,
soft 404s, and metadata fetch failures. It also excludes non-English URLs,
documentation, tags, categories, pagination, and author archives. A weekly GitHub
Actions workflow refreshes and commits this file automatically. If the file is absent,
the CLI falls back to the live sitemap and uses URL slugs for lightweight matching.

The deterministic selection layer prefers a governing pillar, a relevant hub,
and, for comparison briefs, up to two sibling comparisons before filling any
remaining slots by topical relevance. Claude maps those candidates to exact H2s
and supplies descriptive anchor text and a one-sentence rationale. The resulting
table is intended for editorial review before publication. Immediately before the
selected candidates enter the prompt, the CLI fetches them again and removes any URL
that no longer returns a live, indexable PingCAP page.

## Credentials security

Never commit .env, token.json, or credentials.json. All three are in .gitignore.
For hosted deployments, base64-encode token.json and credentials.json and pass them
as TOKEN\_JSON\_B64 and CREDENTIALS\_JSON\_B64 environment variables — the script decodes
them automatically at startup.

## Claude code review (Bedrock)

Every pull request is reviewed automatically by Claude via
`.github/workflows/claude-code-review.yml` (using `anthropics/claude-code-action@v1`)
running against **Amazon Bedrock**, authenticating with static AWS access keys. There is
no `ANTHROPIC_API_KEY` secret and no Claude GitHub App to install.

One-time setup by a repo admin, under
Settings > Secrets and variables > Actions:

1. **Add secrets** (Secrets tab):
   - `BEDROCK_AWS_ACCESS_KEY_ID` — an AWS access key ID with Bedrock `InvokeModel` access.
   - `BEDROCK_AWS_SECRET_ACCESS_KEY` — the matching secret access key.
2. **(Optional) Add variables** (Variables tab; defaults are already baked in):
   - `BEDROCK_AWS_REGION` — defaults to `ap-southeast-1`.
   - `ANTHROPIC_MODEL` — defaults to `global.anthropic.claude-sonnet-4-5-20250929-v1:0`.
     Ensure Claude model access is granted for this inference profile in your account.

After that, opening or updating a PR triggers the review; Claude posts inline comments
and a summary. Comments are posted with the default `GITHUB_TOKEN` (as `github-actions[bot]`).

## Brief consistency checks

Listicles allow up to 12 H2 sections; other types allow 10. Article word budgets
use valid SEMrush primary-keyword volume first, then the DataForSEO seed volume.
The 2,000–4,999 and 5,000+ tiers do not overlap. Listicle section allocations
sum to a selected article total, including the introduction and FAQ allowance.
Solution pages use `/solutions/`; customer evidence is conditional on relevant
source material. The two-visual cap applies to commissioned diagrams/illustrations,
not tables, code snippets, existing assets, or the solution hero video.

The SERP request retains AI Overview and featured-snippet evidence, including
source URLs. Missing feature evidence is explicitly unavailable, not evidence
that the feature does not exist. Asynchronous AI Overview loading is enabled;
DataForSEO may charge its documented additional fee for this option.

Competitor keyword opportunities are checked against PingCAP's exact-keyword
SEMrush US domain report (up to 20 unique keywords per brief). Top-10 PingCAP
rankings are excluded; lower rankings include the existing URL as an improvement
opportunity. No ranking returned means a potential gap requiring a content check.
Failed lookups remain unknown. These checks add SEMrush requests and API usage.

Before export, structural validation checks section order, H2 limits,
at-a-glance placement (first H2 for comparisons, second for listicles), word budgets, visual-line presence, metadata lengths, URL
structure, and supplied internal-link URLs and placements. Truncated responses or
validation failures stop the run with an error after preserving `draft.md`,
`validation.json`, and `research.md` in a unique local `brief_failed_*` folder.
Brief generation defaults to 16,000 output tokens (21,000 for listicles); override with a positive integer
in `ANTHROPIC_MAX_TOKENS` in your local `.env` (title generation remains at 50).
Known outline formatting variants (`## H1:` and a visual-summary heading) are
normalized before validation and export, without changing article content or budgets.
Comparison briefs receive calculated section allocations totaling the selected
article target. Real budget overruns still fail validation; no automatic paid retry
is made. SERP instructions preserve ranks, identify owned coverage, distinguish
Google AI Overview evidence from other mentions, and prohibit unsupported citation
causality or ranking-time claims. These checks do not certify factual
accuracy, customer evidence, or every editorial instruction; human review is still
required. Google Docs separates the outline from trailing metadata using named
section boundaries at any supported Markdown heading level.

Run regression checks with `python3 -m unittest discover -s tests -v`.



### Internal-link section IDs

Generated link tables use `h2_1`, `h2_2`, etc., referring to article H2s in final
outline order. Python builds the ID map after heading normalization and replaces
the IDs with the actual headings before validation and export. H1s, H3s, metadata,
and fenced code examples do not receive IDs. Wording changes therefore do not
require repeating the heading text in the generated link table. Reordering sections
requires updating their ordinal IDs. Existing exact-heading tables remain supported.
Unknown IDs, unmatched legacy headings, duplicate/unverified URLs, and more than two
links per H2 still fail validation. The code does not guess semantic link placement;
editorial relevance still requires review. No extra generation call is needed.


## Stage 0: confirm a separate primary keyword

The first positional argument is the **title / H1 angle**, not the search keyword.
Before the existing 11 steps, Stage 0 resolves and validates a primary keyword:

```bash
python3 brief.py "TiDB Cloud Zero vs Supabase for AI Agent Backends" comparison \
  --priority-link-url https://www.pingcap.com/ai/ --priority-link-anchor "distributed SQL database for AI applications"
python3 brief.py "TiDB Cloud Zero vs Supabase for AI Agent Backends" comparison \
  --primary-keyword-override "supabase alternative" \
  --priority-link-url https://www.pingcap.com/ai/ --priority-link-anchor "distributed SQL database for AI applications"
```

Every run opens a local confirmation screen and prints its URL in Terminal.
Enter your name; use arrow keys or the radio buttons to select a candidate, then
press Enter or Confirm. Below the options, a **Top 10 results for this keyword**
panel shows each result's rank, linked title, domain, page type, angle relevance
(✅ at or above `relevance_threshold`, ❌ below) and a different-brand flag, plus
a **View live on Google** link. Warnings disable confirmation until **I've reviewed
these results and this keyword still fits my article** is ticked. The override
field on the same screen must be validated before you can continue. A
command-line override also requires this confirmation; it is not expanded or
silently replaced. Cancel or Ctrl+C blocks generation. There is no
unattended/auto-confirm option. This is a temporary, loopback-only screen, not a
hosted web service.

Stage 0 extracts title entities with the configured Haiku model, generates 10–20
pattern candidates, expands them through DataForSEO Labs and optional SEMrush,
and fetches measured US monthly volume and difficulty. It evaluates up to five
SERPs using an LLM judgment of the supplied titles, URLs and snippets, then shows
up to three choices. These are relevance judgments, not full-page factual checks.
The chosen snapshot is reused in Step 2. All unselected candidates travel with the
research, marked as scored, unvalidated, or discarded as appropriate.

For short comparison titles such as `TiDB vs. MariaDB`, `entity` means the main
topic for explainer queries. If the model omits it or returns null or blank text,
Stage 0 uses the nonempty extracted `category`. Product, competitor and
`head_entity` stay as extracted. Other missing required fields and malformed
values still block the run. Extraction responses are validated before caching;
the revised prompt uses a new cache key, so older responses are not reused.

Configure patterns, weights and thresholds in `config/keyword_resolver.json`, or
supply a complete replacement with `--keyword-config path/to/config.json`.
Defaults: MSV >=50, at least five relevant organic results, US location 2840,
24-hour caching. The score weights are intent 30%, angle relevance 25%, log volume
20%, difficulty 15%, and AI Overview opportunity 10%. Difficulty is assessed
against `pingcap_authority_baseline` (default 60), an explicit SEO planning
parameter that should be calibrated by the SEO owner; it is not a measured
SEMrush authority score. Missing metrics are never estimated.

GSC is not integrated in this CLI. Cannibalization therefore uses PingCAP URLs in
the selected top-10 SERP and labels that fallback. Absence from that snapshot does
not prove no existing page targets the term. Short, unusually high-volume terms
require acknowledgment; automatically generated terms whose results clearly
concern a different brand are discarded. AI Overview opportunity is based on the returned snapshot only.

If all automatic candidates, including parent terms, miss the volume floor, or
the best candidate has fewer than five relevant results, Stage 0 blocks and says
the run is routed to the SEO owner. This is a manual handoff message, not an
automated notification.

A writer's override is handled differently. If it is below the volume floor, has
too few relevant results, or its results look like a different brand, it is
still offered as an option with a warning for each failed check, and it can be
confirmed only after the acknowledgment is ticked. The brief header and
`validation.json` record `override_below_threshold` and the failed checks. A
blank or over-long override, or one with no DataForSEO volume/difficulty, shows
an inline error; the existing options stay usable. Provider failures (DataForSEO,
the LLM failing or returning unusable data) still end the run; no
brief-generation call is made. Successful Stage 0 responses are cached in `.keyword_cache/` beside the
script for 24 hours. Delete that directory to refresh research early. Cache and
run-output directories are ignored by Git. Paid API calls still occur on misses.

The generated Markdown/Google Doc begins with the confirmed keyword, name/time,
selection source, whether an override was confirmed below threshold (and which
checks failed), runner-up scores and acknowledged warnings. The same resolution
object is saved immediately to `brief_run_*/validation.json`, then marked validated
on success; failed brief validation also includes it in the existing
`brief_failed_*/validation.json`. Keep the ordinary brief-generation and validation
rules unchanged: Stage 0 adds keyword selection and audit metadata only.

Run all tests with `python3 -m unittest discover -s tests -v`. The browser-script
unit test additionally uses Node if installed (otherwise it is explicitly skipped).
Tests mock paid providers; they do not make live API calls.

### Supporting keyword safeguards and regression checks

Rejected Stage 0 terms stay excluded from the final supporting-keyword list, even
if DataForSEO or SEMrush returns them again. Terms Stage 0 validated against the
SERP (status `eligible`) are kept. Other terms must share a meaningful word with the
title/primary keyword or be explicitly tagged for entity coverage.
Generic words such as “best” and “alternative” do not establish relevance. This
is a conservative lexical filter, not a semantic or SERP validation of every
supporting term; reviewers should still check the final list.

Unknown entity-coverage volume stays **MSV unavailable**. Only measured values
contribute to Total MSV; measured zero remains zero. Candidates remain available
in the resolution audit even when excluded from the final supporting list.

Acknowledging a weak override does not bypass the final generation gate: the
expanded top-20 SERP must still contain at least five relevant pages. The
confirmation screen displays these limits from the same quality configuration
used by generation.

The **Python regression tests** workflow runs the Python and Node-backed UI tests
on pull requests and pushes to main without API credentials. To require a passing
check before merging, a repository administrator must add this job to the branch
protection rules or ruleset.


## Review updates

SEMrush is optional. Leave SEMRUSH_API_KEY empty to skip it. A failed SEMrush
request produces a sanitized warning and does not stop DataForSEO research.
The generator loads .env beside brief.py explicitly. Required quality-check
dependencies are checked before paid research begins.

An explicit comparison keyword must use the products named in the article title.
The expanded SERP evidence gate runs immediately after Step 2. Its failure saves
the actual result judgments in the run's validation.json and stops later research.
The five-page minimum remains a configured editorial rule.

Only files in docs/examples/ and root files named example_* or sample_* are
loaded as reference briefs. The README and generated briefs are excluded.
Place reviewed reference briefs in docs/examples/ to use them as examples.

Validated Markdown is saved inside a unique brief_run_* directory so rerunning
the same title does not overwrite earlier output. A validated status means the
automated checks passed. Human editorial and source verification is still needed.
Drafts and research are preserved if validation or a repair request fails.
