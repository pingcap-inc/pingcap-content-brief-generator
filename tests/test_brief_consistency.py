"""Exercise production functions without requiring credentials or optional SDK imports."""
import ast
import json
import re
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlparse

import requests

SOURCE = Path(__file__).resolve().parents[1] / 'brief.py'
tree = ast.parse(SOURCE.read_text())
FUNCTIONS = {'word_count_plan', 'brief_sections', 'split_brief', 'validate_brief',
             'check_pingcap_ranking', 'get_semrush_keyword_gap', 'get_serp_and_paa',
             'generate_brief', 'semrush_get', 'summarize_title', 'url_domain'}
CONSTANTS = {'_BRIEF_SECTIONS', '_BASE_INSTRUCTIONS', '_QUALITY_CHECKLIST'}
selected = [n for n in tree.body if
            isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS or
            isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]

def namespace():
    ns = {'re': re, 'json': json, 'requests': requests, 'urlparse': urlparse, 'PINGCAP_DOMAIN': 'pingcap.com', 'SEMRUSH_API_KEY': 'test'}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def valid_brief(ns):
    bodies = {name: 'Guidance.' for name in ns['_BRIEF_SECTIONS']}
    bodies['Meta Elements'] = '| Meta Title | Scaling guide |\n| Meta Description | A scaling guide. |\n| URL Structure | /blog/scaling/ |'
    bodies['Internal Links'] = 'No verified internal link candidates returned — refresh the sitemap inventory'
    bodies['Outline / Headings'] = ('**Block 1 — Top ranking pages table**\nNo SERP data returned\n'
        '**Block 2 — Patterns Favored by AI Overviews & LLMs**\nInsufficient SERP data\n'
        '## Scaling\nTarget: ~1800–2500 words\n**Visual:** None needed\n')
    return '\n\n'.join('### '+name+'\n\n'+bodies[name] for name in ns['_BRIEF_SECTIONS'])


class BudgetTests(unittest.TestCase):
    def test_boundaries_and_listicle_totals(self):
        ns = namespace()
        for volume, minimum, maximum in [(0,1800,2500),(499,1800,2500),(500,2500,3200),
                (1999,2500,3200),(2000,3000,3800),(4999,3000,3800),(5000,3500,4500)]:
            plan = ns['word_count_plan']([], {'intent':{'search_volume':volume}}, 'listicle')
            self.assertEqual((plan['minimum'],plan['maximum']), (minimum,maximum))
            self.assertEqual(sum(plan['section_budgets'].values()),plan['article_target'])
    def test_fallback_uses_seed_only(self):
        fn = namespace()['word_count_plan']
        rows = [{'search_volume':9000}, {'is_seed':True,'search_volume':800}]
        self.assertEqual(fn(rows, None, 'blog')['primary_keyword_msv'],800)
        self.assertEqual(fn(rows, {'intent':{'search_volume':'0'}}, 'blog')['source'],'SEMrush')
        self.assertIsNone(fn(rows[:1], None, 'blog')['primary_keyword_msv'])


class ResearchTests(unittest.TestCase):
    def test_serp_preserves_features(self):
        ns=namespace(); ns['record_api_cost']=Mock()
        ai={'type':'ai_overview','items':[{'text':'Observed answer'}]}
        snippet={'type':'featured_snippet','url':'https://example.com','description':'Answer'}
        ns['dataforseo_post']=Mock(return_value={'tasks':[{'result':[{'items':[ai,snippet,
            {'type':'organic','title':'Regular','rank_group':1}]}]}]})
        organic,paa,features=ns['get_serp_and_paa']('scaling')
        self.assertEqual(features['ai_overview'],[ai]);self.assertEqual(features['featured_snippet'],[snippet])
        self.assertEqual(len(organic),1)
    def test_no_features_is_not_claimed_absent(self):
        ns=namespace();ns['record_api_cost']=Mock()
        ns['dataforseo_post']=Mock(return_value={'tasks':[{'result':[{'items':[]}]}]})
        self.assertEqual(ns['get_serp_and_paa']('x')[2]['ai_overview'],[])
        ns['dataforseo_post'].return_value={}
        self.assertEqual(ns['get_serp_and_paa']('x')[2]['status'],'unavailable')
    def test_ranking_classifications(self):
        ns=namespace()
        for position,label in [(4,'existing coverage'),(23,'improvement opportunity'),(70,'improvement opportunity')]:
            ns['semrush_get']=Mock(return_value=[{'Ph':'scaling','Po':str(position),'Ur':'https://www.pingcap.com/blog/scaling/'}])
            result=ns['check_pingcap_ranking']('scaling')
            self.assertEqual(result['classification'],label);self.assertEqual(result['pingcap_position'],position)
        ns['semrush_get']=Mock(return_value=[])
        self.assertEqual(ns['check_pingcap_ranking']('scaling')['classification'],'potential content gap')
        ns['semrush_get']=Mock(side_effect=RuntimeError('API unavailable'))
        self.assertEqual(ns['check_pingcap_ranking']('scaling')['classification'],'unknown')
        ns['semrush_get']=Mock(side_effect=requests.ConnectionError('timeout'))
        self.assertEqual(ns['check_pingcap_ranking']('scaling')['classification'],'unknown')
        ns['semrush_get']=Mock(side_effect=AttributeError('bug'))
        with self.assertRaises(AttributeError):ns['check_pingcap_ranking']('scaling')
    def test_url_domain(self):
        ns=namespace()
        self.assertEqual(ns['url_domain']('https://www.example.com/a/b'),'example.com')
        self.assertEqual(ns['url_domain']('https://docs.www.example.com/'),'docs.www.example.com')
        for bad in ['notaurl','https:/malformed','','https://[::1']:
            self.assertEqual(ns['url_domain'](bad),'')
    def test_top_ten_excluded_and_keywords_deduplicated(self):
        ns=namespace()
        ns['semrush_get']=Mock(return_value=[{'Ph':'scaling database','Nq':'500','Po':'3'}])
        ns['check_pingcap_ranking']=Mock(return_value={'classification':'existing coverage'})
        self.assertEqual(ns['get_semrush_keyword_gap']('scaling',['a','b']),[])
        self.assertEqual(ns['check_pingcap_ranking'].call_count,1)
    def test_semrush_headers_and_failure_distinction(self):
        ns=namespace();resp=Mock();resp.text='Keyword;Position;URL\nscaling;4;https://www.pingcap.com/x/'
        ns['requests']=Mock();ns['requests'].get.return_value=resp
        self.assertEqual(ns['semrush_get']({}, strict=True)[0]['Po'],'4')
        resp.text='ERROR 50 :: NOTHING FOUND'
        self.assertEqual(ns['semrush_get']({},strict=True),[])
        resp.text='ERROR 120 :: API UNITS BALANCE IS ZERO'
        with self.assertRaises(RuntimeError):ns['semrush_get']({},strict=True)


class OutputTests(unittest.TestCase):
    def test_split_all_heading_levels_and_trailing_metadata(self):
        ns=namespace()
        for level in [1,2,3]:
            text='### Meta Elements\nMeta\n'+'#'*level+' Outline / Headings\n## Article\nText\n### CTAs\nCTA'
            meta,outline=ns['split_brief'](text)
            self.assertIn('CTA',meta);self.assertNotIn('CTA',outline);self.assertIn('## Article',outline)
    def test_validator_accepts_valid_structure(self):
        ns=namespace();self.assertEqual(ns['validate_brief'](valid_brief(ns),'blog',[],{'minimum':1800,'maximum':2500}),[])
    def test_validator_rejects_missing_sections_and_excess_budget(self):
        ns=namespace();text=valid_brief(ns).replace('### Target Audience','### Missing').replace('1800–2500','3000–4000')
        errors=ns['validate_brief'](text,'blog',[],{'minimum':1800,'maximum':2500})
        self.assertTrue(any('sections' in e for e in errors));self.assertTrue(any('budgets' in e for e in errors))
    def test_validator_rejects_invented_links(self):
        ns=namespace();text=valid_brief(ns).replace('No verified internal link candidates returned — refresh the sitemap inventory',
            '| Section (H2) | Anchor text | Target URL | Why |\n|---|---|---|---|\n| Scaling | Learn | https://example.com/invented/ | Reason |')
        errors=ns['validate_brief'](text,'blog',[{'url':'https://www.pingcap.com/real/'}],{'minimum':1800,'maximum':2500})
        self.assertIn('Unverified or duplicate internal link',errors)
    def test_truncation_rejected(self):
        ns=namespace();ns.update(ANTHROPIC_API_KEY='test',ANTHROPIC_MODEL='test',
            load_brief_examples=lambda:'',load_feedback=lambda:'',build_system_prompt=lambda *args:'prompt')
        client=Mock();client.messages.create.return_value=types.SimpleNamespace(stop_reason='max_tokens')
        ns['anthropic']=Mock();ns['anthropic'].Anthropic.return_value=client
        with self.assertRaisesRegex(ValueError,'incomplete'):
            ns['generate_brief']('scaling','blog',[],[],[],[])
    def test_complete_generation_and_title(self):
        ns=namespace();ns.update(ANTHROPIC_API_KEY='test',ANTHROPIC_MODEL='test',
            ANTHROPIC_HAIKU_MODEL='title',load_brief_examples=lambda:'',load_feedback=lambda:'',
            build_system_prompt=lambda *args:'prompt')
        content=valid_brief(ns)
        client=Mock();client.messages.create.return_value=types.SimpleNamespace(
            stop_reason='end_turn',content=[types.SimpleNamespace(type='text',text=content)])
        ns['anthropic']=Mock();ns['anthropic'].Anthropic.return_value=client
        self.assertEqual(ns['generate_brief']('scaling','blog',[],[],[],[]),content)
        prompt=client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Word Count Plan',prompt);self.assertIn('SERP Features',prompt)
        client.messages.create.return_value=types.SimpleNamespace(content=[types.SimpleNamespace(text=' A title ')])
        self.assertEqual(ns['summarize_title']('topic'),'A title')

    def test_prompt_conflicts_removed(self):
        ns=namespace();prompt=ns['_BASE_INSTRUCTIONS']+ns['_QUALITY_CHECKLIST']
        self.assertNotIn('2,000–5,000',prompt)
        self.assertNotIn('diagram suggestion for each H3',prompt)
        self.assertNotIn('/article/[slug]/ for other types',prompt)
        self.assertIn('12 for listicles',prompt)

if __name__=='__main__':unittest.main()
