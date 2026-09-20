# PingCAP Content Brief Generator

A CLI tool that generates SEO/AEO content briefs for PingCAP writers by pulling live data from DataForSEO, SEMrush, Google Search Console, and Claude.

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
10. GSC internal link candidates (verified pingcap.com pages)
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
- Google Search Console API (for internal link suggestions)

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
- Verified internal links from GSC data only
- Schema markup recommendations matched to page sections

## Credentials security

Never commit .env, token.json, or credentials.json. All three are in .gitignore.
For hosted deployments, base64-encode token.json and credentials.json and pass them
as TOKEN\_JSON\_B64 and CREDENTIALS\_JSON\_B64 environment variables — the script decodes
them automatically at startup.

## Claude code review

Every pull request is reviewed automatically by Claude via
`.github/workflows/claude-code-review.yml` (using `anthropics/claude-code-action@v1`).

One-time setup by a repo admin:

1. **Add the API key secret.** In Settings > Secrets and variables > Actions, add a
   repository secret named `ANTHROPIC_API_KEY` with a valid Anthropic API key.
2. **Install the Claude GitHub App.** Visit https://github.com/apps/claude and
   install it on `pingcap-inc/pingcap-content-brief-generator` (grant access to this repo).

After that, opening or updating a PR triggers the review; Claude posts inline comments
and a summary. You can also run `/install-github-app` from the Claude Code CLI to
configure the secret and app automatically.
