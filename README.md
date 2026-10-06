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

python brief.py "TiDB vs PostgreSQL" comparison
python brief.py "Best databases for real-time analytics" listicle
python brief.py "AI agent memory persistent state database" solution

To supply a keyword for validation, use the separate override flag (confirmation is still required):

python brief.py "TiDB Cloud is the unified database layer for AI agents" solution --primary-keyword-override "AI agent memory"

## Model configuration

By default the script uses:

- claude-sonnet-4-20250514 for brief generation
- claude-haiku-4-5-20251001 for doc title summarisation

Override either by setting ANTHROPIC\_MODEL or ANTHROPIC\_HAIKU\_MODEL in .env.

To use OpenAI instead of Anthropic, replace the anthropic client calls in generate\_brief() and summarize\_title() with OpenAI SDK calls pointing to gpt-4o or equivalent. The prompt system is model-agnostic — the prompts themselves require no changes.

## Brief quality

The brief prompt enforces:

- Word count scaled by keyword MSV (N/A → 1,800–2,500 words; 5,000+ → 3,500–4,500 words)
- AI Overview patterns block grounded in SERP data
- At a glance comparison table for comparison and listicle types
- Per-section word count targets
- Writer guardrails (benchmark sourcing, pricing claims, review verification)
- Conflict disclosure for PingCAP-published content
- Up to five verified internal-link recommendations from the PingCAP sitemap
- Exact H2 placement, suggested anchor text, and a rationale for every link
- Schema markup recommendations matched to page sections

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

Before export, structural validation checks section order, H2 limits, second-H2
comparison placement, word budgets, visual-line presence, metadata lengths, URL
structure, and supplied internal-link URLs and placements. Truncated responses or
validation failures stop the run with an error after preserving `draft.md`,
`validation.json`, and `research.md` in a unique local `brief_failed_*` folder.
Brief generation defaults to 16,000 output tokens; override with a positive integer
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
python3 brief.py "TiDB Cloud Zero vs Supabase for AI Agent Backends" comparison
python3 brief.py "TiDB Cloud Zero vs Supabase for AI Agent Backends" comparison \
  --primary-keyword-override "supabase alternative"
```

Every run opens a local confirmation screen and prints its URL in Terminal.
Enter your name; use arrow keys or the radio buttons to select a candidate, then
press Enter or Confirm. Warnings disable confirmation until **I've checked this**
is selected. The override field on the same screen must be validated before you
can continue. A command-line override also requires this confirmation; it is not
expanded or silently replaced. Cancel or Ctrl+C blocks generation. There is no
unattended/auto-confirm option. This is a temporary, loopback-only screen, not a
hosted web service.

Stage 0 extracts title entities with the configured Haiku model, generates 10–20
pattern candidates, expands them through DataForSEO Labs and optional SEMrush,
and fetches measured US monthly volume and difficulty. It evaluates up to five
SERPs using an LLM judgment of the supplied titles, URLs and snippets, then shows
up to three choices. These are relevance judgments, not full-page factual checks.
The chosen snapshot is reused in Step 2. All unselected candidates travel with the
research, marked as scored, unvalidated, or discarded as appropriate.

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
require acknowledgment; terms whose results clearly concern a different brand
are discarded. AI Overview opportunity is based on the returned snapshot only.

If all candidates, including parent terms, miss the volume floor, or the best
candidate has fewer than five relevant results, Stage 0 blocks and says the run
is routed to the SEO owner. This is a manual handoff message, not an automated
notification. Provider failures also block Stage 0; no brief-generation call is
made. Successful Stage 0 responses are cached in `.keyword_cache/` beside the
script for 24 hours. Delete that directory to refresh research early. Cache and
run-output directories are ignored by Git. Paid API calls still occur on misses.

The generated Markdown/Google Doc begins with the confirmed keyword, name/time,
selection source, runner-up scores and acknowledged warnings. The same resolution
object is saved immediately to `brief_run_*/validation.json`, then marked validated
on success; failed brief validation also includes it in the existing
`brief_failed_*/validation.json`. Keep the ordinary brief-generation and validation
rules unchanged: Stage 0 adds keyword selection and audit metadata only.

Run all tests with `python3 -m unittest discover -s tests -v`. The browser-script
unit test additionally uses Node if installed (otherwise it is explicitly skipped).
Tests mock paid providers; they do not make live API calls.
