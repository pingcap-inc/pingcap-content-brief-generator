import unittest

from internal_links import (
    _xml_locations,
    classify_page_type,
    is_eligible_url,
    select_internal_link_candidates,
)


class SitemapParsingTests(unittest.TestCase):
    def test_parses_sitemap_index(self):
        kind, urls = _xml_locations("""<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://www.pingcap.com/page-sitemap.xml</loc></sitemap>
        </sitemapindex>""")
        self.assertEqual(kind, "index")
        self.assertEqual(urls, ["https://www.pingcap.com/page-sitemap.xml"])

    def test_filters_non_content_urls(self):
        self.assertTrue(is_eligible_url("https://www.pingcap.com/blog/tidb-scaling/"))
        self.assertFalse(is_eligible_url("https://www.pingcap.com/docs/tidb/stable/"))
        self.assertFalse(is_eligible_url("https://www.pingcap.com/zh/blog/example/"))
        self.assertFalse(is_eligible_url("https://example.com/blog/tidb/"))

    def test_classifies_core_page_types(self):
        self.assertEqual(
            classify_page_type("https://www.pingcap.com/compare/tidb-vs-mysql/"),
            "comparison",
        )
        self.assertEqual(
            classify_page_type("https://www.pingcap.com/case-study/acme/"),
            "case study",
        )
        self.assertEqual(
            classify_page_type("https://www.pingcap.com/compare/"),
            "hub",
        )


class CandidateSelectionTests(unittest.TestCase):
    def test_applies_rules_and_caps_candidates(self):
        pages = [
            {"url": "https://www.pingcap.com/tidb-scaling-guide/", "title": "TiDB scaling guide", "h1": "", "meta_description": "", "primary_keyword": "database scaling", "page_type": "pillar"},
            {"url": "https://www.pingcap.com/compare/", "title": "Database comparisons", "h1": "", "meta_description": "Scaling database comparisons", "primary_keyword": "", "page_type": "hub"},
            {"url": "https://www.pingcap.com/compare/tidb-vs-mysql/", "title": "TiDB vs MySQL", "h1": "", "meta_description": "Database scaling", "primary_keyword": "", "page_type": "comparison"},
            {"url": "https://www.pingcap.com/compare/tidb-vs-postgresql/", "title": "TiDB vs PostgreSQL", "h1": "", "meta_description": "Database scaling", "primary_keyword": "", "page_type": "comparison"},
            {"url": "https://www.pingcap.com/blog/scale-mysql/", "title": "How to scale MySQL", "h1": "", "meta_description": "Database scaling", "primary_keyword": "", "page_type": "blog"},
            {"url": "https://www.pingcap.com/blog/another-scaling-post/", "title": "Another scaling post", "h1": "", "meta_description": "Database scaling", "primary_keyword": "", "page_type": "blog"},
        ]
        selected = select_internal_link_candidates(
            pages, "database scaling", "comparison", max_links=5
        )
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0]["selection_rule"], "governing pillar page")
        self.assertEqual(selected[1]["selection_rule"], "relevant topic hub")
        self.assertEqual(
            [item["selection_rule"] for item in selected].count("sibling comparison page"),
            2,
        )
        self.assertEqual(len({item["url"] for item in selected}), 5)


if __name__ == "__main__":
    unittest.main()
