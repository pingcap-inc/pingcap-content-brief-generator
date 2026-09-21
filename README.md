# PingCAP Content Brief Generator

A CLI tool that generates SEO/AEO content briefs for PingCAP writers by pulling live data from DataForSEO, SEMrush, the PingCAP sitemap, and Claude.

## What it does

Runs an 11-step pipeline per topic:

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
- Local .md file (fallback if Google auth is not configured)

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

For long prompts, include "Primary keyword: your keyword" anywhere in the topic text:

python brief.py "TiDB Cloud is the unified database layer for AI agents... Primary keyword: AI agent memory" solution

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

The command writes `sitemap_inventory.json`, storing each eligible page's URL,
title, H1, meta description, mapped primary keyword when available, page type,
and publish date. It excludes non-English URLs, documentation, tags, categories,
pagination, and author archives. A weekly GitHub Actions workflow refreshes and
commits this file automatically. If the file is absent, the CLI falls back to the
live sitemap for verified URLs and uses URL slugs for lightweight matching.

The deterministic selection layer prefers a governing pillar, a relevant hub,
and, for comparison briefs, up to two sibling comparisons before filling any
remaining slots by topical relevance. Claude maps those candidates to exact H2s
and supplies descriptive anchor text and a one-sentence rationale. The resulting
table is intended for editorial review before publication.

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
