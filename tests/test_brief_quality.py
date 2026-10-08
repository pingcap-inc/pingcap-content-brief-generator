"""Quality bar: config, deterministic fields, every check, repair, and the regression fixture."""
import ast
import json
import os
import re
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlparse

import requests

import brief_quality as bq
from internal_links import select_internal_link_candidates
from keyword_resolver import Resolver, confirm_resolution, load_config
from test_keyword_resolver import FixtureAPI, TITLE

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (Path(__file__).with_name("fixtures") / "supabase_comparison_brief.md").read_text()
PRIORITY = {"url": "https://www.pingcap.com/ai/", "anchor": "distributed SQL database for AI applications"}
RELEVANT_RANKS = (1, 2, 3, 5, 6, 8)
CANDIDATES = [
    {"url": "https://www.pingcap.com/ai/", "title": "Distributed SQL database for AI applications",
     "selection_rule": "priority link (required)", "anchor": PRIORITY["anchor"]},
    {"url": "https://www.pingcap.com/compare/tidb-vs-postgresql/", "title": "TiDB vs PostgreSQL comparison database model"},
    {"url": "https://www.pingcap.com/tidb/cloud/zero/", "title": "TiDB Cloud Zero"},
]


def resolution():
    proposal = Resolver(FixtureAPI(), load_config()).resolve(TITLE, "comparison")
    return confirm_resolution(proposal, 0, "Writer")


def deep_serp():
    """Mocked top-20 SERP: relevant pages at RELEVANT_RANKS, the rest judged irrelevant."""
    return [{"rank": r, "url": f"https://example.com/{r}", "title": f"Result {r}", "description": "",
             "relevance": 0.9 if r in RELEVANT_RANKS else 0.1, "page_type": "comparison", "different_brand": False}
            for r in range(1, 21)]


def context(**extra):
    res = resolution()
    ctx = {"resolution": res, "content_type": "comparison", "priority_link": PRIORITY, "required_links": [],
           "keyword_data": [{"keyword": "supabase alternatives", "search_volume": 1900},
                            {"keyword": "agent backend scaffolding", "search_volume": 0},
                            {"keyword": "supabase pricing", "search_volume": "N/A"}],
           "semrush_related": [{"keyword": "supabase competitors", "search_volume": "720"}],
           "paa": ["Is there a free Supabase alternative?"],
           "relevant_serp": bq.relevant_serp(deep_serp(), 0.6), "link_candidates": CANDIDATES,
           "serp_source_keyword": res["primary_keyword"], "backlinks_empty": True, "llm_mentions_empty": False,
           "plan": {"primary_keyword_msv": 5000},
           "url_verifier": lambda url: "unverified" not in url,
           "case_study_text": lambda name: "Manus runs agent sandboxes on TiDB Cloud"}
    ctx.update(extra)
    return ctx


def with_headings(text):
    """Render h2_N link IDs the way brief.resolve_internal_link_ids does."""
    for i, (title, *_) in enumerate(bq.outline_parts(text)["h2s"], 1):
        text = text.replace(f"| h2_{i} |", f"| {title} |")
    return text


def finished(text=FIXTURE, ctx=None):
    ctx = ctx or context()
    out, notes = bq.apply_deterministic(with_headings(text), ctx)
    return out, ctx, notes


def failing(text, ctx):
    return {c["id"] for c in bq.run_checks(text, ctx) if not c["passed"]}


class ConfigTests(unittest.TestCase):
    def test_sections_match_brief_py(self):
        tree = ast.parse((ROOT / "brief.py").read_text())
        value = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and any(getattr(t, "id", "") == "_BRIEF_SECTIONS" for t in n.targets))
        self.assertEqual(tuple(value), bq.BRIEF_SECTIONS)

    def test_no_inline_prompt_left_in_code(self):
        source = (ROOT / "brief.py").read_text()
        self.assertNotIn("_BASE_INSTRUCTIONS", source)
        self.assertNotIn("Verified customers with confirmed", source)

    def test_templates_and_weights(self):
        for name in ["comparison", "listicle", "solution", "default"]:
            data = bq.load_yaml(f"templates/{name}.yaml")
            if data.get("word_weights"):
                self.assertEqual(sum(data["word_weights"].values()), 100, name)
            for spec in data["sections"]:
                for pattern in spec["match"]:
                    re.compile(pattern)
        self.assertEqual(bq.template_for("alternative")["primary_cta_section"], "decision")
        self.assertEqual(bq.template_for("blog")["content_types"], ["blog", "product", "playbook"])

    def test_prompts_follow_house_style(self):
        for content_type in ["comparison", "listicle", "solution", "blog"]:
            base, checklist = bq.system_prompt_parts(content_type, PRIORITY, "Supabase")
            for text in (base, checklist):
                self.assertNotIn("—", text, content_type)
                self.assertNotIn("{{", text, content_type)
            self.assertIn(PRIORITY["url"], base)
            self.assertIn("Manus", base)
            self.assertIn("v1beta1", base)
            self.assertNotIn("N/A or unknown", base)

    def test_roster_has_the_confirmed_urls(self):
        urls = {c["name"]: c["url"] for c in bq.roster()}
        self.assertEqual(len(urls), 7)
        self.assertTrue(urls["Kimi"].endswith("/case-study/kimi-2-6-agent-hosting-platform-tidb-cloud/"))
        self.assertTrue(all(u.startswith("https://www.pingcap.com/") for u in urls.values()))

    def test_banned_patterns_cover_the_list(self):
        lint = bq._lint_check
        for phrase in ["leverage", "seamlessly", "straightforward", "battle-tested", "robust", "comprehensive",
                       "furthermore", "not only fast but also cheap", "cutting-edge", "game-changing"]:
            self.assertFalse(lint(f"Some text {phrase} here.")["passed"], phrase)
        self.assertTrue(lint("TiDB is the best fit for MySQL teams.")["passed"])
        self.assertFalse(lint("TiDB is the best database.")["passed"])
        self.assertFalse(lint("TiDB is architecturally superior.")["passed"])
        self.assertTrue(lint("Postgres is the best known option.")["passed"], "claims check TiDB sentences only")


class DeterministicTests(unittest.TestCase):
    def test_keyword_driven_meta(self):
        out, ctx, _ = finished()
        meta = bq.meta_cells(out)
        self.assertEqual(meta["URL Structure"], "/compare/supabase-alternative")
        self.assertEqual(meta["Target Keyword"], "supabase alternative")
        self.assertEqual(meta["Search Intent"], "Commercial Investigation")
        self.assertEqual(bq.page_url("solution", "AI Agent Memory!"), "/solutions/ai-agent-memory")
        self.assertEqual(bq.page_url("product", "x y"), "/article/x-y")

    def test_supporting_keywords_have_msv_and_drop_zero_volume(self):
        out, ctx, _ = finished()
        cell = bq.split_cell(bq.meta_cells(out)["Supporting Keywords"])
        self.assertTrue(all(re.search(r"\(MSV [\d,]+", item) for item in cell))
        self.assertIn("supabase alternatives (MSV 1,900)", cell)
        self.assertIn("agent backend scaffolding (MSV 0, entity coverage)", cell)
        self.assertFalse(any("supabase pricing" in item for item in cell), "unknown volume dropped")
        rows = bq.supporting_keywords(ctx["resolution"], [{"keyword": "agent backend scaffolding", "search_volume": 0}])
        self.assertNotIn("agent backend scaffolding", [r["keyword"] for r in rows], "untagged zero volume dropped")
        total = bq.meta_cells(out)["Total MSV"]
        self.assertTrue(total.startswith(f"{5000 + sum(r['msv'] for r in ctx['supporting']):,}"))

    def test_rejected_terms_cannot_reenter_from_other_sources(self):
        res = resolution()
        for status in ("blocked: insufficient relevant pages", "discarded: unrelated brand", "needs writer confirmation"):
            res["supporting_candidates"] = [{"keyword": "supabase unrelated", "msv": 90000, "status": status}]
            rows = bq.supporting_keywords(res,
                [{"keyword": "Supabase Unrelated", "search_volume": 90000}],
                [{"keyword": "supabase unrelated", "search_volume": 90000}],
                ["supabase unrelated"])
            self.assertEqual(rows, [], status)

    def test_unvalidated_terms_need_topical_overlap(self):
        res = resolution()
        res["supporting_candidates"] = [
            {"keyword": "best vacation alternatives", "msv": 90000, "status": "not SERP-validated"},
            {"keyword": "supabase authentication", "msv": 500, "status": "not SERP-validated"}]
        rows = bq.supporting_keywords(res,
            [{"keyword": "best holiday tools", "search_volume": 80000}])
        self.assertEqual([r["keyword"] for r in rows], ["supabase authentication"])

    def test_unknown_entity_volume_stays_unavailable_and_is_not_totaled(self):
        ctx = context()
        ctx["keyword_data"] = [{"keyword": "agent backend scaffolding", "search_volume": None}]
        ctx["semrush_related"] = []
        ctx["resolution"]["supporting_candidates"] = []
        out, _ = bq.apply_deterministic(FIXTURE, ctx)
        row = next(r for r in ctx["supporting"] if r["keyword"] == "agent backend scaffolding")
        self.assertIsNone(row["msv"])
        meta = bq.meta_cells(out)
        self.assertIn("agent backend scaffolding (MSV unavailable, entity coverage)", meta["Supporting Keywords"])
        self.assertIn("5,000", meta["Total MSV"])
        self.assertIn("unavailable volumes excluded", meta["Total MSV"])
        check = next(c for c in bq.run_checks(out, ctx) if c["id"] == "supporting_keywords")
        self.assertTrue(check["passed"], check)

    def test_entities_deduplicated(self):
        out, _, notes = finished()
        entities = [e[0] for e in bq.entity_list(out)]
        self.assertEqual(len(entities), len({e.casefold() for e in entities}))
        self.assertIn("Removed 1 duplicate entities", notes)
        self.assertEqual(bq.dedupe(["a", "A", "b"], key=str.casefold), ["a", "b"])

    def test_empty_data_replaced_with_one_line(self):
        out, _, _ = finished()
        body = out[bq.sections(out)["Link Landscape & Acquisition Angle"][1]:bq.sections(out)["Outline / Headings"][0]]
        self.assertEqual(body.strip(), bq.rules()["empty_data"]["backlinks"])
        self.assertTrue(bq.rows_all_empty([{"referring_domains": 0, "total_backlinks": None}], ["referring_domains", "total_backlinks"]))
        self.assertFalse(bq.rows_all_empty([{"referring_domains": 3}], ["referring_domains"]))
        out, _, _ = finished(ctx=context(llm_mentions_empty=True))
        self.assertIn(bq.rules()["empty_data"]["llm_mentions"], out)

    def test_unverified_secondary_cta_dropped(self):
        out, _, notes = finished()
        self.assertNotIn("unverified-kit", out)
        self.assertIn("tidb-vs-postgresql", out[bq.sections(out)["CTAs"][1]:])
        self.assertTrue(any("Dropped unverifiable CTA" in n for n in notes))
        text = FIXTURE.replace("**Secondary CTA:** Compare TiDB with PostgreSQL",
                               "**Secondary CTA:** Talk to sales (verify URL before publication)")
        out, _, _ = finished(text)
        self.assertNotIn("verify URL", out)

    def test_customer_claim_marked_unless_case_study_matches(self):
        line = "Manus cut query latency by 80% on TiDB Cloud."
        text = FIXTURE.replace("Cite Manus as an agent platform customer:", line + " Cite Manus:")
        out, ctx, _ = finished(text)
        self.assertIn("80% on TiDB Cloud. Cite Manus: https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/ "
                      "(verify against case study)", out)
        self.assertNotIn("case_studies", failing(out, ctx))
        out, ctx, _ = finished(text, context(case_study_text=lambda n: "latency fell 80% after Manus moved"))
        self.assertNotIn("verify against case study", out)

    def test_competitor_price_gets_marker_when_sourced(self):
        text = FIXTURE.replace("as of 2026, per https://supabase.com/pricing (verify before publication)",
                               "as of 2026, per https://supabase.com/pricing")
        out, ctx, _ = finished(text)
        self.assertIn("https://supabase.com/pricing (verify before publication)", out)

    def test_word_tiers_never_na(self):
        self.assertEqual(bq.word_tier(0)["minimum"], 1800)
        self.assertEqual(bq.word_tier(5000)["maximum"], 4500)
        with self.assertRaises(ValueError):
            bq.word_tier(None)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.good, self.ctx, _ = finished()

    def assertFails(self, check_id, old, new, ctx=None):
        self.assertIn(old, self.good, old)
        failures = failing(self.good.replace(old, new), ctx or self.ctx)
        self.assertIn(check_id, failures)
        return failures

    def test_fixture_passes_everything(self):
        self.assertEqual(failing(self.good, self.ctx), set())

    def test_meta(self):
        title = "Supabase Alternative for AI Agent Backends - PingCAP"
        self.assertFails("meta_title", title, "Supabase Alternative - PingCAP")
        self.assertFails("meta_title", title, "Supabase Alternative for AI Agent Backends and Memory Layers - PingCAP")
        self.assertFails("meta_title", title, "Supabase Alternative for AI Agent Backends | PingCAP")
        self.assertFails("meta_title", title, "Open-source Postgres backends for AI agent teams - PingCAP")
        desc = "Evaluating a Supabase alternative for AI agent backends?"
        self.assertFails("meta_description", desc, desc + " Plus a much longer explanation of everything.")
        self.assertFails("meta_description", "Evaluating a Supabase alternative", "Evaluating a Postgres option")
        self.assertFails("url_slug", "/compare/supabase-alternative", "/compare/tidb-cloud-zero-vs-supabase")
        self.assertFails("meta_search_intent", "| Search Intent | Commercial Investigation |", "| Search Intent | Informational |")

    def test_serp_table(self):
        self.assertFails("serp_table", "| 6 | Supabase pricing breakdown", "| 4 | Supabase pricing breakdown")
        self.assertFails("serp_table", "**Block 2:", "Rank 7 was not relevant.\n\n**Block 2:")
        few = context(relevant_serp=bq.relevant_serp(deep_serp(), 0.6)[:4])
        self.assertIn("serp_relevant_pages", failing(self.good, few))
        self.assertIn("aio_patterns_source", failing(self.good, context(serp_source_keyword="tidb cloud zero vs supabase")))

    def test_takeaways_and_eeat(self):
        bullet = "- Compare pricing models, not list prices, and verify every competitor figure before you publish."
        self.assertFails("key_takeaways", bullet, "- Compare pricing.")
        self.assertFails("key_takeaways", "- Agent backends that need many isolated tenants benefit from TiDB horizontal scale on TiKV.\n" + bullet + "\n", "")
        self.assertFails("key_takeaways", "**Key Takeaways**", "**Summary**")
        self.assertFails("eeat", "Add an expert review note", "Add a note")
        self.assertFails("eeat", "ratings, pricing, and feature availability", "pricing")

    def test_comparison_template(self):
        self.assertFails("template_sections", "## How TiDB solves agent backend sprawl", "## Why distributed SQL handles agent sprawl")
        self.assertFails("section_capabilities", "### Key differences\n\nSupabase relies", "Supabase relies")
        self.assertIn("section_integrations", failing(self.good.replace("SQL client", "client"), self.ctx))
        h2 = "## How TiDB solves agent backend sprawl with distributed SQL on TiKV"
        section = self.good[self.good.index(h2):self.good.index("## Supabase alternative FAQs")]
        self.assertIn("section_how_tidb_solves", failing(self.good.replace(section, section.replace("intro", "earlier")), self.ctx))
        h2 = "## How TiDB solves agent backend sprawl with distributed SQL on TiKV"
        section = self.good[self.good.index(h2):self.good.index("## Supabase alternative FAQs")]
        stripped = re.sub(r"(?i)TiKV|Raft|native VECTOR type|VECTOR", "the engine", section)
        self.assertIn("section_how_tidb_solves", failing(self.good.replace(section, stripped), self.ctx))
        moved = self.good.replace("## How should you choose between", "## Next steps between")
        self.assertIn("template_sections", failing(moved, self.ctx))

    def test_at_a_glance(self):
        self.assertFails("section_at_a_glance", "| Category | Supabase |", "| Area | Supabase |")
        self.assertFails("section_at_a_glance", "| Category | Supabase |", "| Category | Competitor |")
        self.assertFails("section_at_a_glance", "| Hosted Supabase MCP server for agents |", "| N/A |")
        failures = self.assertFails("section_at_a_glance", "| Storage | Supabase Storage for files and objects | Pair with object storage such as S3 | Supabase for bundled file storage |\n", "")
        self.assertIn("section_at_a_glance", failures)
        self.assertFails("section_at_a_glance", "Sources: https://supabase.com/docs (verify before publication)", "Sources: supabase docs")

    def test_reviews_pricing_and_ratings(self):
        self.assertIn("section_reviews", failing(self.good.replace("review count", "count"), self.ctx))
        self.assertFails("section_reviews", "G2, Capterra, and Clutch", "G2")
        self.assertFails("no_invented_ratings", "Supabase has a large community presence", "Supabase rates 4.6/5 on G2")
        self.assertFails("section_pricing", "as of 2026, per https://supabase.com/pricing", "per https://supabase.com/pricing")
        self.assertFails("section_pricing", "per https://supabase.com/pricing (verify", "per the website (verify")
        self.assertFails("section_pricing", "describe the Request Units billing model", "say TiDB costs $5 per month, and describe the Request Units billing model")

    def test_primary_cta(self):
        cta = "**Primary CTA:** [distributed SQL database for AI applications](https://www.pingcap.com/ai/)\n\n**Visual:** Table: decision"
        self.assertFails("primary_cta", cta, "**Visual:** Table: decision")
        self.assertFails("primary_cta", "[distributed SQL database for AI applications](https://www.pingcap.com/ai/)\n\n**Visual:** Table: decision",
                         "[AI database](https://www.pingcap.com/ai/)\n\n**Visual:** Table: decision")
        self.assertFails("primary_cta", "**Visual:** Table: billing model comparison.",
                         "**Primary CTA:** [distributed SQL database for AI applications](https://www.pingcap.com/ai/)\n\n**Visual:** Table: billing model comparison.")

    def test_internal_links(self):
        self.assertFails("internal_links_priority", "| distributed SQL database for AI applications | https://www.pingcap.com/ai/ |",
                         "| AI hub | https://www.pingcap.com/ai/ |")
        row = "| TiDB Cloud Zero for agents | https://www.pingcap.com/tidb/cloud/zero/ |"
        moved = self.good.replace("| How do deployment, governance, and multi-tenant architecture differ? " + row,
                                  "| What do support, reviews, and market signals show? " + row)
        self.assertIn(row, self.good)
        self.assertIn("internal_links_relevance", failing(moved, self.ctx))
        required = context(required_links=[{"url": "https://www.pingcap.com/tidb/cloud/zero/",
                                             "anchor": "TiDB Cloud Zero for agents", "section": "deployment"}])
        self.assertNotIn("internal_links_required", failing(self.good, required))
        required["required_links"][0]["section"] = "pricing"
        self.assertIn("internal_links_required", failing(self.good, required))
        required["required_links"][0]["anchor"] = "Zero"
        self.assertIn("internal_links_required", failing(self.good, required))

    def test_case_studies(self):
        self.assertFails("case_studies", "Cite Manus as an agent platform customer:", "Cite a leading global travel company:")
        self.assertFails("case_studies", "https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/",
                         "https://www.pingcap.com/case-study/some-other-customer/")
        self.assertFails("case_studies", "Cite Manus as an agent platform customer:", "Manus cut costs by 90%:")

    def test_faqs(self):
        self.assertFails("faqs", "- Use TiDB Data Migration tooling where it fits.",
                         "- Use TiDB Data Migration tooling where it fits.\n- Step four.\n- Step five.")
        self.assertFails("faqs", "**Answer guidance:**\n- Point to the dated", "**Answer guidance:**\nStart with context.\n- Point to the dated")
        self.assertFails("faqs", "### How much does Supabase cost for AI agent workloads?",
                         "### Why do penguins migrate south in winter?")
        self.assertFails("faqs", "### How do I migrate from Supabase to TiDB Cloud?", "### How do I move off it to TiDB Cloud?")
        self.assertFails("faqs", "### Does Supabase have a hosted MCP server for agents?", "### Does it have a hosted MCP server for agents?")
        self.assertFails("faqs", "**Rationale**: FAQ queries", "Answer in 3 to 5 sentences.\n\n**Rationale**: FAQ queries")
        not_alternative = context()
        not_alternative["resolution"] = {**not_alternative["resolution"], "primary_keyword": "supabase alternative"}
        self.assertIn("faqs", failing(self.good.replace("### How do I migrate from Supabase to TiDB Cloud?",
                                                        "### How do I move off it to TiDB Cloud?"), not_alternative))

    def test_style_lint(self):
        self.assertFails("style_lint", "Compare pricing models, not list prices",
                         "Compare pricing models — not list prices")
        self.assertFails("style_lint", "Primary CTA:** [distributed SQL database for AI applications](https://www.pingcap.com/ai/), in the decision H2.",
                         "Primary CTA:** [distributed SQL database for AI applications](https://www.pingcap.com/ai/) — in the decision H2.")
        self.assertFails("style_lint", "Cover MCP servers", "Seamlessly cover MCP servers")
        self.assertFails("style_lint", "Name the mechanism in the first sentence:", "TiDB is the best choice. Name the mechanism:")

    def test_product_facts(self):
        api = "POST https://zero.tidbapi.com/v1beta1/instances"
        self.assertFails("product_facts", api, "POST https://zero.tidbcloud.com/v1beta1/instances")
        self.assertFails("product_facts", api, "POST https://zero.tidbapi.com/v1alpha1/instances")
        self.assertFails("product_facts", "expire after 30 days", "expire after 14 days")
        self.assertFails("product_facts", "takes three clicks", "takes five clicks")
        self.assertFails("product_facts", "converts it to TiDB Cloud Starter.", "converts it to TiDB Cloud Dedicated.")
        self.assertIn("product_facts", failing(self.good.replace(
            "takes three clicks and converts it", "takes three clicks, and the claim converts it to TiDB Cloud Serverless and"), self.ctx))

    def test_sql_vector_syntax(self):
        bad = "SELECT id FROM agent_memory ORDER BY embedding <=> '[0.1, 0.2, 0.3]' LIMIT 5;"
        self.assertFails("sql_vector_syntax", "SELECT id, VEC_COSINE_DISTANCE(embedding, '[0.1, 0.2, 0.3]') AS distance\nFROM agent_memory\nORDER BY distance\nLIMIT 5;", bad)
        self.assertFails("sql_vector_syntax", "```postgres", "```mysql")
        self.assertEqual(failing(self.good.replace("```postgres", "```pgvector"), self.ctx), set())
        self.assertNotIn("sql_vector_syntax", failing(self.good.replace(
            "LIMIT 5;\n```\n\nFor contrast", "LIMIT 5;\nSELECT * FROM t WHERE a <=> NULL;\n```\n\nFor contrast"), self.ctx),
            "NULL-safe equality on a non-vector column is valid TiDB SQL")
        inline = self.good.replace("Show a TiDB vector query:", "In TiDB use `ORDER BY embedding <=> vec`:")
        self.assertIn("sql_vector_syntax", failing(inline, self.ctx))

    def test_word_count(self):
        checks = bq.run_checks(self.good, self.ctx, ["Top-level word budgets do not fit the selected MSV tier"])
        self.assertFalse(next(c for c in checks if c["id"] == "word_count")["passed"])

    def test_every_failure_maps_to_a_repair_unit(self):
        bad = self.good.replace("Cover MCP servers", "Seamlessly cover MCP servers").replace(
            "Supabase Alternative for AI Agent Backends - PingCAP", "Supabase Alternative - PingCAP")
        plan = bq.repair_plan(bq.run_checks(bad, self.ctx))
        self.assertEqual(set(plan), {"Meta Elements", "Outline / Headings::h2_4"})
        self.assertTrue(any("seamless" in v.casefold() for v in plan["Outline / Headings::h2_4"]))


def brief_namespace():
    """Execute the production brief.py functions without its credential-bound imports."""
    tree = ast.parse((ROOT / "brief.py").read_text())
    wanted = {"validate_serp_blocks", "markdown_lines", "resolve_internal_link_ids", "parse_word_budget",
              "normalize_brief_headings", "word_count_plan", "brief_sections", "validate_brief",
              "generate_brief", "_save_failed_brief", "build_system_prompt", "_BRIEF_SECTIONS"}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted or
            isinstance(n, ast.Assign) and any(getattr(t, "id", "") in wanted for t in n.targets)]
    ns = {"os": os, "tempfile": tempfile, "re": re, "json": json, "requests": requests, "urlparse": urlparse,
          "ANTHROPIC_API_KEY": "test", "ANTHROPIC_MODEL": "test",
          "load_brief_examples": lambda: "", "load_feedback": lambda: ""}
    exec(compile(ast.Module(body=body, type_ignores=[]), "brief.py", "exec"), ns)
    return ns


def reply(text, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)])


class RegressionFixtureTests(unittest.TestCase):
    """Title, comparison type and /ai/ priority link from the spec, with mocked APIs and Claude."""

    def run_generation(self, responses, relevant=None):
        """Sets self.client, self.folder and self.report before calling, so failures can be inspected."""
        ns = brief_namespace()
        self.client = Mock()
        self.client.messages.create.side_effect = responses
        ns["anthropic"] = Mock()
        ns["anthropic"].Anthropic.return_value = self.client
        ctx = context()
        relevant = ctx["relevant_serp"] if relevant is None else relevant
        self.report = {}
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        ns["os"] = types.SimpleNamespace(getenv=lambda key: None, getcwd=lambda: self.folder, path=os.path)
        return ns["generate_brief"](
            TITLE, "comparison", ctx["keyword_data"], relevant, ctx["paa"], [],
            llm_mentions_data={"questions": [{"question": "q"}], "sources": ["supabase.com"]},
            backlinks_data=[{"url": "https://example.com/1", "referring_domains": 0, "total_backlinks": 0, "domain_rank": 0}],
            semrush_data={"intent": {}, "related_keywords": ctx["semrush_related"]},
            internal_link_candidates=CANDIDATES,
            serp_features={"status": "returned", "ai_overview": [], "featured_snippet": []},
            keyword_resolution=ctx["resolution"], priority_link=PRIORITY,
            quality_context={"url_verifier": ctx["url_verifier"], "case_study_text": ctx["case_study_text"],
                             "competitor": "Supabase"},
            report=self.report)

    def saved_report(self):
        return json.loads(next(Path(self.folder).glob("brief_failed_*/validation.json")).read_text())

    def test_first_draft_violations_are_repaired_once_and_fixture_assertions_hold(self):
        bad = (FIXTURE
               .replace("Supabase Alternative for AI Agent Backends - PingCAP", "Supabase Alternative - PingCAP")
               .replace("Cover MCP servers for both products", "Seamlessly cover MCP servers for both products")
               .replace("- Explain what agents can do through it.",
                        "Supabase answers this well — explain what agents can do through it."))
        good_spans = bq.unit_spans(FIXTURE)

        def repair(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            units = re.findall(r"^## Unit (.+)$", prompt, re.M)
            return reply("\n".join(f"<<<UNIT {u}>>>\n{FIXTURE[slice(*good_spans[u])].rstrip()}\n<<<END UNIT>>>" for u in units))

        responses = [reply(bad)]
        result = self.run_generation(lambda **kw: responses.pop(0) if responses else repair(**kw))
        report, client = self.report, self.client
        self.assertEqual(client.messages.create.call_count, 2, "exactly one repair round")
        self.assertEqual(report["status"], "validated")
        self.assertEqual(set(report["repair"]["units_applied"]),
                         {"Meta Elements", "Outline / Headings::h2_4", "Outline / Headings::h2_9"})
        self.assertTrue(all(c["passed"] for c in report["checks"]), [c for c in report["checks"] if not c["passed"]])
        self.assertTrue({"meta_title", "style_lint", "faqs", "primary_cta", "sql_vector_syntax"}
                        <= {c["id"] for c in report["checks"]})
        self.assert_fixture_bar(result)
        first_prompt = client.messages.create.call_args_list[0].kwargs
        self.assertIn("distributed SQL database for AI applications", first_prompt["system"])
        self.assertIn("irrelevant pages already removed", first_prompt["messages"][0]["content"])
        self.assertNotIn("Competitor Backlinks Data", first_prompt["messages"][0]["content"], "empty data not sent")

    def assert_fixture_bar(self, text):
        meta = bq.meta_cells(text)
        self.assertEqual(meta["URL Structure"], "/compare/supabase-alternative")
        self.assertIn("supabase alternative", meta["Meta Title"].casefold())
        self.assertTrue(meta["Meta Title"].endswith(" - PingCAP"))
        parts = bq.outline_parts(text)
        outline = text[parts["start"]:parts["end"]]
        primary = [l for l in outline.splitlines() if "Primary CTA" in l]
        self.assertEqual(len(primary), 1)
        self.assertIn("(https://www.pingcap.com/ai/)", primary[0])
        self.assertNotIn("—", text)
        for phrase in bq.rules()["style"]["banned_phrases"]:
            self.assertIsNone(re.search(phrase["pattern"], text), phrase["id"])
        faq = next(text[s:e] for t, s, e in parts["h2s"] if "FAQ" in t)
        for answer in re.findall(r"\*\*Answer guidance:\*\*\n(.*?)(?=\n\n|\Z)", faq, re.S):
            lines = answer.strip().splitlines()
            self.assertTrue(all(l.startswith("- ") for l in lines))
            self.assertLessEqual(len(lines), 4)
        self.assertTrue(all("<=>" not in code for _, code in bq.tidb_sql_snippets(text)))
        self.assertIn("**Key Takeaways**", text)
        self.assertIn("author bio with relevant credentials", text)
        self.assertIn("expert review note", text)
        titles = [t for t, *_ in parts["h2s"]]
        self.assertTrue(any("pricing" in t.casefold() for t in titles))
        self.assertTrue(any("reviews" in t.casefold() for t in titles))
        self.assertTrue(any(t.startswith("How TiDB solves") for t in titles))
        serp_ranks = {r[0] for r in bq.table_rows(text[slice(*parts["preamble"])]) if r[0] != "#"}
        self.assertTrue(serp_ranks <= {str(r) for r in RELEVANT_RANKS})
        self.assertNotRegex(text[slice(*parts["preamble"])], r"(?i)not (topically )?relevant|excluded")

    def test_unrepaired_draft_fails_with_every_check_in_validation_json(self):
        bad = FIXTURE.replace("Cover MCP servers", "Seamlessly cover MCP servers")
        with self.assertRaisesRegex(ValueError, "style_lint"):
            self.run_generation([reply(bad), reply(bad)])
        report = self.saved_report()
        self.assertEqual(report["status"], "unvalidated")
        lint = next(c for c in report["checks"] if c["id"] == "style_lint")
        self.assertFalse(lint["passed"])
        self.assertTrue(any("seamless" in d.casefold() for d in lint["details"]))
        self.assertTrue(all({"id", "passed", "details"} <= set(c) for c in report["checks"]))
        self.assertIn("Outline / Headings::h2_4", report["repair"]["units_requested"])

    def test_too_few_relevant_pages_fails_before_any_claude_call(self):
        with self.assertRaisesRegex(ValueError, "serp_relevant_pages"):
            self.run_generation([], relevant=bq.relevant_serp(deep_serp(), 0.6)[:4])
        self.client.messages.create.assert_not_called()
        self.assertFalse(self.saved_report()["checks"][0]["passed"])

    def test_priority_link_required(self):
        ns = brief_namespace()
        with self.assertRaisesRegex(ValueError, "priority_link"):
            ns["generate_brief"](TITLE, "comparison", [], [], [], [], keyword_resolution=resolution())


class InternalLinkWeightingTests(unittest.TestCase):
    def test_hub_and_sibling_pages_outrank_old_posts(self):
        # Identical topical overlap, so only the configured path weights separate them.
        pages = [{"url": f"https://www.pingcap.com/{d}/supabase-alternative/", "title": "Supabase alternative",
                  "page_type": "article"} for d in ("article", "compare", "ai")]
        cfg = bq.rules()["internal_links"]
        picked = select_internal_link_candidates(pages, "supabase alternative", "blog", max_links=3,
                                                 path_weights=cfg["path_weights"], target_path="/compare/x",
                                                 same_directory_weight=cfg["same_directory_weight"])
        order = [p["url"] for p in sorted(picked, key=lambda p: -p["relevance_score"])]
        self.assertEqual(order[-1], pages[0]["url"])
        self.assertEqual(order[0], pages[1]["url"], "same-directory comparison page ranks first")
        unweighted = select_internal_link_candidates(pages, "supabase alternative", "blog", max_links=3)
        self.assertEqual(len({p["relevance_score"] for p in unweighted}), 1)


class DeepSerpTests(unittest.TestCase):
    def test_depth_twenty_uses_its_own_cache_and_payload(self):
        from keyword_resolver import Cache, ResearchAPI
        with tempfile.TemporaryDirectory() as d:
            api = ResearchAPI(load_config(), Cache(d), "login", "password", Mock(), "test", session=Mock())
            items = [{"type": "organic", "rank_group": i, "url": f"https://e.com/{i}"} for i in range(1, 21)]
            api.post = Mock(return_value=[{"items": items}])
            self.assertEqual(len(api.serp("kw")["organic"]), 10)
            self.assertEqual(len(api.serp("kw", 20)["organic"]), 20)
            self.assertEqual([c.args[1]["depth"] for c in api.post.call_args_list], [10, 20])
            api.serp("kw", 20)
            self.assertEqual(api.post.call_count, 2, "second depth-20 call is cached")


if __name__ == "__main__":
    unittest.main()
