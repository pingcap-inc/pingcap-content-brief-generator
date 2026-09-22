import unittest
from unittest.mock import Mock, patch

from internal_links import (
    _fetch_page_metadata,
    _xml_locations,
    classify_page_type,
    discover_sitemap_pages,
    is_eligible_url,
    select_internal_link_candidates,
    validate_internal_link_candidates,
)


class SitemapParsingTests(unittest.TestCase):
    def test_parses_sitemap_index(self):
        kind, urls = _xml_locations("""<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://www.pingcap.com/page-sitemap.xml</loc></sitemap>
        </sitemapindex>""")
        self.assertEqual(kind, "index")
        self.assertEqual(urls, ["https://www.pingcap.com/page-sitemap.xml"])

    def test_rejects_malformed_sitemap_xml(self):
        with self.assertRaisesRegex(ValueError, "Malformed sitemap XML"):
            _xml_locations("<sitemapindex>")

    def test_parses_empty_urlset(self):
        kind, urls = _xml_locations(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        )
        self.assertEqual(kind, "urls")
        self.assertEqual(urls, [])

    def test_filters_non_content_urls(self):
        self.assertTrue(is_eligible_url("https://www.pingcap.com/blog/tidb-scaling/"))
        self.assertFalse(is_eligible_url("https://www.pingcap.com/docs/tidb/stable/"))
        self.assertFalse(is_eligible_url("https://www.pingcap.com/zh/blog/example/"))
        self.assertFalse(is_eligible_url("https://example.com/blog/tidb/"))
        self.assertFalse(is_eligible_url("ftp://www.pingcap.com/blog/tidb/"))

    @patch("internal_links._get")
    def test_skips_failed_child_sitemap_and_keeps_available_pages(self, mock_get):
        root = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://www.pingcap.com/good.xml</loc></sitemap>
          <sitemap><loc>https://www.pingcap.com/bad.xml</loc></sitemap>
        </sitemapindex>"""
        good = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://www.pingcap.com/blog/live-page/</loc></url>
        </urlset>"""
        mock_get.side_effect = [root, good, RuntimeError("unavailable")]
        pages = discover_sitemap_pages()
        self.assertEqual([page["url"] for page in pages], [
            "https://www.pingcap.com/blog/live-page/"
        ])

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

    def test_excludes_explicitly_invalid_inventory_pages(self):
        pages = [
            {"url": "https://www.pingcap.com/blog/dead/", "title": "Scaling", "page_type": "blog", "is_live": False},
            {"url": "https://www.pingcap.com/blog/live/", "title": "Scaling", "page_type": "blog", "is_live": True, "indexable": True},
        ]
        selected = select_internal_link_candidates(pages, "scaling", "blog")
        self.assertEqual([page["url"] for page in selected], [
            "https://www.pingcap.com/blog/live/"
        ])


class CandidateValidationTests(unittest.TestCase):
    @staticmethod
    def response(html, url="https://www.pingcap.com/blog/live/", status=200):
        response = Mock()
        response.text = html
        response.url = url
        response.status_code = status
        response.headers = {}
        return response

    @patch("internal_links._get_response")
    def test_rejects_noindex_and_soft_404_pages(self, mock_get_response):
        page = {"url": "https://www.pingcap.com/blog/page/", "title": "Page"}
        mock_get_response.return_value = self.response(
            '<html><head><meta name="robots" content="noindex"></head></html>'
        )
        self.assertFalse(_fetch_page_metadata(page)["is_live"])

        mock_get_response.return_value = self.response(
            "<html><head><title>404 — Page Not Found</title></head></html>"
        )
        self.assertFalse(_fetch_page_metadata(page)["is_live"])

    @patch("internal_links._get_response")
    def test_keeps_final_redirect_url_and_filters_failed_candidates(self, mock_get_response):
        live_response = self.response(
            "<html><head><title>Live page</title></head><body><h1>Live page</h1></body></html>",
            url="https://www.pingcap.com/blog/final/",
        )
        mock_get_response.side_effect = [live_response, RuntimeError("gone")]
        candidates = [
            {"url": "https://www.pingcap.com/blog/old/", "title": "Old", "slot": 1},
            {"url": "https://www.pingcap.com/blog/gone/", "title": "Gone", "slot": 2},
        ]
        validated = validate_internal_link_candidates(candidates)
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["url"], "https://www.pingcap.com/blog/final/")
        self.assertEqual(validated[0]["original_url"], "https://www.pingcap.com/blog/old/")


if __name__ == "__main__":
    unittest.main()
