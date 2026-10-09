"""Refresh mode (--refresh-url): optimize a live page, as in the approved refresh briefs."""
import re
import unittest

import brief_quality as bq
from test_brief_quality import finished, context, failing

PAGE = """<html><head><title>Old title</title><meta name="description" content="Old description."></head>
<body><header><h2>Products</h2></header><nav><h3>Docs</h3></nav>
<article><h1>Old H1</h1><h2>Why agents need memory</h2><p>Some words here.</p>
<h3>Short-term memory</h3><pre>SELECT 1;</pre><h2>Legacy section</h2>
<h2>Related Resources</h2><h2>TiDB Cloud Starter</h2></article>
<footer><h2>Company</h2></footer></body></html>"""


def refresh_ctx(headings):
    existing = {"url": "https://www.pingcap.com/compare/supabase-alternative/",
                "final_url": "https://www.pingcap.com/compare/supabase-alternative/", "status": 200,
                "redirected": False, "title": "", "meta_description": "", "code_blocks": 0,
                "images": 0, "word_count": 1200, "headings": headings}
    return context(existing_page=existing)


def with_refresh_labels(text):
    parts = bq.outline_parts(text)
    h1 = text[parts["h1"][0]:parts["h1"][1]].split("\n", 1)[0]
    text = text.replace(h1 + "\n", h1 + "\n\n**Current:** Old H1\n", 1)
    for title, *_ in bq.outline_parts(text)["h2s"]:
        text = text.replace(f"## {title}\n", f"## {title}\n\n**Current:** {title} (H2)\n**Change:** Keep\n", 1)
    return text


class PageReaderTests(unittest.TestCase):
    def test_reads_article_headings_without_site_chrome_or_widgets(self):
        page = bq.parse_existing_page(PAGE, "https://www.pingcap.com/blog/a/", "https://www.pingcap.com/blog/a/")
        self.assertEqual([h["text"] for h in page["headings"]],
                         ["Old H1", "Why agents need memory", "Short-term memory", "Legacy section"])
        self.assertEqual((page["title"], page["meta_description"], page["code_blocks"]),
                         ("Old title", "Old description.", 1))
        self.assertFalse(page["redirected"])

    def test_redirect_is_detected(self):
        page = bq.parse_existing_page("", "https://www.pingcap.com/blog/a/", "https://www.pingcap.com/", 200)
        self.assertTrue(page["redirected"])


class RefreshCheckTests(unittest.TestCase):
    def setUp(self):
        base, _, _ = finished()
        self.titles = [t for t, *_ in bq.outline_parts(base)["h2s"]]
        self.ctx = refresh_ctx([{"tag": "H1", "text": "Old H1"}] +
                               [{"tag": "H2", "text": t} for t in self.titles])
        self.text, _, _ = finished(with_refresh_labels(base), self.ctx)

    def test_labelled_refresh_passes_and_keeps_the_live_url(self):
        self.assertEqual(failing(self.text, self.ctx), set())
        self.assertEqual(bq.meta_cells(self.text)["URL Structure"], "/compare/supabase-alternative/")

    def test_missing_labels_are_sent_to_the_section(self):
        title = self.titles[3]
        broken = self.text.replace(f"**Current:** {title} (H2)\n**Change:** Keep\n", "", 1)
        check = next(c for c in bq.run_checks(broken, self.ctx) if c["id"] == "refresh_mapping")
        self.assertFalse(check["passed"])
        self.assertEqual(check["units"], ["Outline / Headings::h2_4"])

    def test_every_live_h2_must_be_mapped_or_listed_as_removed(self):
        ctx = refresh_ctx(self.ctx["existing_page"]["headings"] + [{"tag": "H2", "text": "Legacy pricing table"}])
        self.assertIn("refresh_mapping", failing(self.text, ctx))
        listed = self.text.replace("**Current:** Old H1\n",
                                   "**Current:** Old H1\n\n**Removed or merged from the live page:**\n"
                                   "- Legacy pricing table: merged into the pricing H2.\n", 1)
        self.assertNotIn("refresh_mapping", failing(listed, ctx))

    def test_new_pages_are_unaffected(self):
        text, ctx, _ = finished()
        self.assertNotIn("refresh_mapping", {c["id"] for c in bq.run_checks(text, ctx)})

    def test_prompt_block_carries_the_labels_and_page(self):
        block = bq.refresh_brief_block(self.ctx["existing_page"])
        for label in ("**Current:**", "**Change:**", "Removed or merged from the live page", "Keep, Rename"):
            self.assertIn(label, block)
        self.assertIn("supabase-alternative", block)


class StructuralUrlTests(unittest.TestCase):
    def test_refresh_accepts_the_live_path_instead_of_a_type_prefix(self):
        from test_brief_consistency import namespace, valid_brief
        ns = namespace()
        text = valid_brief(ns).replace("/blog/scaling/", "/tidb/cloud/")
        plan = {"minimum": 1800, "maximum": 2500}
        page = {"url": "https://www.pingcap.com/tidb/cloud/", "final_url": "https://www.pingcap.com/tidb/cloud/"}
        self.assertIn("Incorrect content-type URL structure", ns["validate_brief"](text, "blog", [], plan))
        self.assertNotIn("Incorrect content-type URL structure",
                         ns["validate_brief"](text, "blog", [], plan, existing_page=page))
