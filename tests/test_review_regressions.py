"""Regression coverage for the full generator review. External calls are mocked."""
import ast
import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import requests
import brief_quality as bq
from keyword_resolver import (Resolver, ResearchAPI, Cache, ProviderError, ResolutionError,
                             load_config, comparison_matches_title, validate_resolution)
from test_keyword_resolver import FixtureAPI, MARIADB_ENTITIES, TITLE
from test_brief_quality import finished, failing, context, reply
import test_brief_quality as quality_tests

ROOT = Path(__file__).resolve().parents[1]


def functions(*names):
    tree = ast.parse((ROOT/'brief.py').read_text())
    nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names
             or isinstance(n,ast.Assign) and any(getattr(t,'id',None)=='_HEADING_STYLE' for t in n.targets)]
    ns = dict(re=re, json=json, os=os, base64=base64, requests=requests, urlparse=urlparse,
              DATAFORSEO_LOGIN='fake-login', DATAFORSEO_PASSWORD='fake-password', SEMRUSH_API_KEY='fake-secret-key',
              _SKIP_FILES={'requirements.txt','feedback.txt','CLAUDE.md'})
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/'brief.py'),'exec'),ns)
    return ns


class ReviewTests(unittest.TestCase):
    def test_wrong_product_pair_cannot_enter_automatic_shortlist(self):
        api = FixtureAPI()
        api.extract = lambda title:{**MARIADB_ENTITIES,'entity':'relational database'}
        api.variants = lambda head:['mysql vs mariadb','postgresql vs mariadb','tidb vs mariadb']
        result = Resolver(api,load_config()).resolve('TiDB vs. MariaDB','comparison')
        measured = set(api.metrics_calls[0])
        self.assertNotIn('mysql vs mariadb',measured)
        self.assertNotIn('postgresql vs mariadb',measured)
        self.assertIn('tidb vs mariadb',measured)
        bad = next(r for r in result['candidates'] if r['keyword']=='mysql vs mariadb')
        self.assertTrue(bad['status'].startswith('discarded'))

    def test_pair_order_and_alternative_queries_are_supported(self):
        entities = {**MARIADB_ENTITIES,'entity':'relational database'}
        self.assertTrue(comparison_matches_title('mariadb vs tidb','TiDB vs MariaDB',entities))
        self.assertTrue(comparison_matches_title('mariadb alternatives','TiDB vs MariaDB',entities))

    def test_blocked_high_score_does_not_hide_eligible_candidate(self):
        api = FixtureAPI()
        def metrics(keywords):
            return {k:{'msv':5000 if k=='supabase alternative' else 100 if k=='supabase alternatives' else 1,
                       'difficulty':0 if k=='supabase alternative' else 99,'metric_status':'available'} for k in keywords}
        api.metrics = metrics
        def relevance(title,keyword,results):
            return [{'index':i,'relevance':0.99 if i<4 else 0.59,'page_type':'comparison','different_brand':False}
                    if keyword=='supabase alternative' else
                    {'index':i,'relevance':0.61 if i<5 else 0,'page_type':'comparison' if i<2 else 'forum','different_brand':False}
                    for i in range(10)]
        api.relevance = relevance
        result = Resolver(api,load_config()).resolve(TITLE,'comparison')
        self.assertEqual(result['options'][0]['keyword'],'supabase alternatives')

    def test_malformed_config_produces_readable_error(self):
        cfg = load_config()
        cfg['aliases']={'blog':[]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ResolutionError,'aliases'):
                load_config(path)

    def test_semrush_warning_and_traceback_never_include_key(self):
        ns=functions('semrush_get')
        response=requests.Response(); response.status_code=400
        response._content=b'ERROR key=fake-secret-key'; response.url='https://api.semrush.com/?key=fake-secret-key'
        with patch.object(requests,'get',return_value=response),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ns['semrush_get']({'type':'phrase_related'}),[])
            self.assertNotIn('fake-secret-key',output.getvalue())
            with self.assertRaises(RuntimeError) as caught:
                ns['semrush_get']({'type':'domain_organic'},strict=True)
            self.assertNotIn('fake-secret-key',str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_semrush_does_not_mutate_callers_parameters(self):
        ns=functions('semrush_get')
        response=Mock(text='Keyword;Position\nfoo;12')
        params={'type':'domain_organic'}
        with patch.object(requests,'get',return_value=response):
            ns['semrush_get'](params)
        self.assertNotIn('key',params)

    def test_dataforseo_task_error_is_not_treated_as_success(self):
        ns=functions('dataforseo_post')
        response=Mock(); response.json.return_value={'status_code':20000,'tasks':[{'status_code':40501,'status_message':'invalid field','result':None}]}
        with patch.object(requests,'post',return_value=response),self.assertRaisesRegex(RuntimeError,'40501'):
            ns['dataforseo_post']('test',[{}])

    def test_llm_mentions_uses_documented_nested_response_and_platform(self):
        ns=functions('get_llm_mentions')
        row={'question':'Does TiDB fit?', 'answer':'Supabase is suitable for this workload.',
             'sources':[{'domain':'supabase.com'}], 'brand_entities':['Supabase'], 'platform':'google'}
        ns['dataforseo_post']=Mock(return_value={'tasks':[{'result':[{'items':[row]}]}]})
        ns['record_api_cost']=Mock()
        result=ns['get_llm_mentions']('supabase alternative')
        endpoint,payload=ns['dataforseo_post'].call_args.args
        self.assertTrue(endpoint.endswith('search_mentions/live'))
        self.assertEqual(payload[0]['target'][0]['keyword'],'supabase alternative')
        self.assertEqual(result['sources'],['supabase.com'])
        self.assertEqual(result['model_name'],'google_ai_overview')
        self.assertFalse(result['tidb_mentioned'],'A question alone cannot prove an answer mention')
        self.assertIn('Supabase',result['competitor_brands'])

    def test_example_loader_excludes_readme_and_generated_output(self):
        ns=functions('load_brief_examples')
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory); (p/'docs/examples').mkdir(parents=True)
            for name in ('README.md','brief_old.md','AGENTS.md'):
                (p/name).write_text('must be excluded')
            (p/'sample_reviewed.md').write_text('approved root sample')
            (p/'docs/examples/approved.md').write_text('approved dedicated sample')
            ns['SCRIPT_DIR']=directory
            text=ns['load_brief_examples']()
            self.assertNotIn('must be excluded',text)
            self.assertIn('approved root sample',text)
            self.assertIn('approved dedicated sample',text)

    def test_changed_article_title_is_rejected(self):
        text,ctx,_=finished()
        text=text.replace('# '+TITLE,'# MySQL vs MariaDB')
        self.assertIn('article_title',failing(text,ctx))

    def test_wrong_competitor_cannot_validate_itself(self):
        text,ctx,_=finished(ctx=context(competitor='Supabase'))
        text=text.replace('| Criteria | TiDB | Supabase |','| Criteria | TiDB | MariaDB |')
        self.assertEqual(bq.competitor_name(text,ctx),'Supabase')
        self.assertIn('section_at_a_glance',failing(text,ctx))

    def test_spoofed_pingcap_hostname_is_external(self):
        self.assertEqual(bq._external_urls('https://evilpingcap.com/pricing'),['https://evilpingcap.com/pricing'])
        self.assertFalse(bq._pingcap_url('https://pingcap.com.evil.example/pricing'))
        self.assertTrue(bq._pingcap_url('https://www.pingcap.com/pricing'))

    def test_repair_instructions_allow_structure_corrections(self):
        self.assertNotIn('structure) unchanged',bq.repair_prompt('',{}))

    def test_docs_use_utf16_and_metadata_stays_out_of_outline(self):
        ns=functions('_docs_length','parse_inline','parse_markdown','_make_location','_make_range','_bold_requests','build_docs_requests')
        blocks=ns['parse_markdown']('## Meta 😀\nA 😀 **bold**\nNext')
        requests_list,end=ns['build_docs_requests'](blocks,use_heading_styles=False)
        styles=[r['updateParagraphStyle']['paragraphStyle']['namedStyleType'] for r in requests_list if 'updateParagraphStyle' in r]
        self.assertTrue(all(style=='NORMAL_TEXT' for style in styles))
        inserts=[r['insertText'] for r in requests_list if 'insertText' in r]
        self.assertEqual(end,1+sum(ns['_docs_length'](r['text']) for r in inserts))
        self.assertEqual(inserts[1]['location']['index'],1+ns['_docs_length']('Meta 😀\n'))
        bold=[r['updateTextStyle'] for r in requests_list if 'updateTextStyle' in r]
        self.assertEqual(bold[-1]['range']['startIndex'],inserts[1]['location']['index']+5)

    def test_code_fence_contents_are_not_exported_as_headings(self):
        ns=functions('parse_inline','parse_markdown')
        blocks=ns['parse_markdown']('```markdown\n## sample\n- literal list\n```\n## Real heading')
        self.assertEqual([b['type'] for b in blocks],['code','h2'])
        self.assertIn('## sample',blocks[0]['text'])


class DraftPreservationTests(unittest.TestCase):
    run_generation = quality_tests.RegressionFixtureTests.run_generation
    saved_report = quality_tests.RegressionFixtureTests.saved_report
    def test_paid_draft_is_saved_when_repair_request_fails(self):
        from test_brief_quality import FIXTURE
        bad=FIXTURE.replace('Cover MCP servers','Seamlessly cover MCP servers')
        with self.assertRaisesRegex(ValueError,'Draft repair request failed'):
            self.run_generation([reply(bad),RuntimeError('repair service unavailable')])
        report=self.saved_report()
        self.assertEqual(report['failure_stage'],'Draft repair request')
        self.assertIn('Seamlessly',next(Path(self.folder).glob('brief_failed_*/draft.md')).read_text())

    def test_paid_draft_is_saved_when_check_crashes(self):
        from test_brief_quality import FIXTURE
        with patch.object(bq,'run_checks',side_effect=RuntimeError('check crashed')):
            with self.assertRaisesRegex(ValueError,'Draft validation failed'):
                self.run_generation([reply(FIXTURE)])
        self.assertEqual(self.saved_report()['failure_stage'],'Draft validation')

class ResearchMetricTests(unittest.TestCase):
    def test_failed_backlinks_stay_unknown_while_measured_zero_is_retained(self):
        ns=functions('get_backlinks_data'); ns['record_api_cost']=Mock()
        ns['dataforseo_post']=Mock(side_effect=[RuntimeError('unavailable'),{'tasks':[{'result':[]}]},
            {'tasks':[{'result':[{'referring_domains':0,'backlinks':0,'rank':0}]}]}, {'tasks':[{'result':[]}]}])
        with redirect_stdout(io.StringIO()):
            rows=ns['get_backlinks_data'](['https://a.example','https://b.example'])
        self.assertIsNone(rows[0]['referring_domains'])
        self.assertEqual(rows[1]['referring_domains'],0)
        self.assertEqual(rows[1]['total_backlinks'],0)
        self.assertEqual(rows[1]['domain_rank'],0)
        self.assertNotIn('dofollow',rows[1]['link_types'])

    def test_domain_headers_are_parsed_and_rank_is_labeled_correctly(self):
        ns=functions('semrush_get','get_semrush_domain_authority','url_domain')
        response=Mock(text='Domain;Rank;Organic Keywords;Organic Traffic;Adwords Keywords\nexample.com;123;456;789;10')
        with patch.object(requests,'get',return_value=response):
            row=ns['get_semrush_domain_authority'](['https://example.com/x'])[0]
        self.assertEqual(row['semrush_rank'],'123')
        self.assertEqual(row['organic_traffic_est'],'789')
        self.assertIsNone(row['authority_score'])

    def test_nonexistent_schema_type_is_rejected(self):
        text,ctx,_=finished()
        text=text.replace('### Schema Markup Recommendations','### Schema Markup Recommendations\nComparisonTable')
        self.assertIn('schema_types',failing(text,ctx))

class LinkReviewTests(unittest.TestCase):
    def test_offdomain_redirect_is_blocked_before_another_request(self):
        import internal_links as il
        response=Mock(status_code=302,headers={'Location':'http://127.0.0.1/private'})
        with patch.object(requests,'get',return_value=response) as get:
            with self.assertRaisesRegex(ValueError,'allowed PingCAP'):
                il._get_response('https://www.pingcap.com/blog/example')
        self.assertEqual(get.call_count,1)

    def test_relative_canonical_and_no_title_do_not_reject_live_page(self):
        import internal_links as il
        response=Mock(status_code=200,url='https://www.pingcap.com/blog/example',
                      text='<link rel="canonical" href="/blog/example">',headers={})
        with patch.object(il,'_get_response',return_value=response):
            row=il._fetch_page_metadata({'url':response.url})
        self.assertTrue(row['is_live'])
        self.assertEqual(row['canonical_url'],response.url)

    def test_malformed_inventory_falls_back(self):
        import internal_links as il
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'inventory.json'; path.write_text('["invalid"]')
            with patch.object(il,'discover_sitemap_pages',return_value=[{'url':'https://www.pingcap.com/ai/'}]):
                rows,source=il.load_internal_link_inventory(path)
        self.assertEqual(source,'live sitemap fallback')
        self.assertEqual(len(rows),1)

    def test_small_link_limit_is_respected(self):
        import internal_links as il
        pages=[{'url':f'https://www.pingcap.com/{t}/tidb','title':'TiDB','page_type':t}
               for t in ['pillar','hub','comparison']]
        self.assertEqual(len(il.select_internal_link_candidates(pages,'TiDB','comparison',max_links=1)),1)
        self.assertEqual(il.select_internal_link_candidates(pages,'TiDB','comparison',max_links=0),[])

class EarlyResearchGateTests(unittest.TestCase):
    def test_relevance_failure_saves_evidence_before_remaining_research(self):
        import sys
        import types
        import keyword_resolver as kr
        import keyword_confirmation as kc
        ns=functions('main')
        api=Mock()
        api.serp.return_value={'organic':[{'url':'https://example.com/a','rank':1}],
                              'paa_questions':[], 'ai_overview':[]}
        api.relevance.return_value=[{'index':0,'relevance':0.1,'page_type':'forum','different_brand':False}]
        resolution={'primary_keyword':'mariadb alternatives'}
        ns.update(sys=sys,tempfile=tempfile,CONTENT_TYPES=['comparison'],
                  SCRIPT_DIR=str(ROOT),DATAFORSEO_LOGIN='test',DATAFORSEO_PASSWORD='test',
                  ANTHROPIC_API_KEY='test',ANTHROPIC_HAIKU_MODEL='test',
                  anthropic=types.SimpleNamespace(Anthropic=Mock()),record_api_cost=Mock(),
                  validate_env=Mock(),get_keyword_data=Mock(return_value=[]),
                  extract_headings_from_url=Mock(),PINGCAP_DOMAIN='pingcap.com')
        argv=['brief.py','TiDB vs. MariaDB','comparison','--priority-link-url','https://www.pingcap.com/ai/',
              '--priority-link-anchor','TiDB AI']
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys,'argv',argv),patch.object(os,'getcwd',return_value=folder),\
                 patch.object(kr,'ResearchAPI',return_value=api),patch.object(kr,'Resolver'),\
                 patch.object(kc,'confirmation_screen',return_value=resolution),\
                 patch.object(kr,'validate_resolution',return_value=resolution),redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    ns['main']()
            report=json.loads(next(Path(folder).glob('brief_run_*/validation.json')).read_text())
        self.assertEqual(caught.exception.code,1)
        self.assertEqual(report['status'],'research_blocked')
        self.assertEqual(report['serp_evidence'][0]['relevance'],0.1)
        ns['extract_headings_from_url'].assert_not_called()
