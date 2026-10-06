"""Exercise the actual loopback protocol and CLI keyword plumbing without paid calls."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from keyword_confirmation import HTML, confirmation_screen
from keyword_resolver import ProviderError, Resolver, ResolutionError, confirm_resolution, load_config
from test_keyword_resolver import FixtureAPI, TITLE


class ConfirmationTests(unittest.TestCase):
    def test_warning_cannot_be_bypassed_over_http_and_override_keeps_candidates(self):
        api = FixtureAPI(); api.rank = True
        resolver = Resolver(api, load_config())
        proposal = resolver.resolve(TITLE, 'comparison')
        ready = threading.Event(); address = []; outcome = {}
        def opened(url):
            address.append(url); ready.set()
        def run():
            try:
                outcome['result'] = confirmation_screen(resolver, proposal, opened)
            except Exception as exc:
                outcome['error'] = exc
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(15))
        def post(action, data, origin=None):
            req = Request(address[0]+'/'+action, json.dumps(data).encode(),
                          {'Content-Type':'application/json', **({'Origin':origin} if origin else {})})
            with urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            with self.assertRaises(HTTPError) as denied:
                post('confirm', {'index':0,'name':'Writer','acknowledged':False})
            self.assertEqual(denied.exception.code, 422)
            with self.assertRaises(HTTPError) as denied:
                post('confirm', {'index':0,'name':'Writer','acknowledged':True}, 'https://untrusted.example')
            self.assertEqual(denied.exception.code, 403)
            override = post('override', {'keyword':'supabase alternatives'})
            self.assertGreater(len(override['candidates']), 1)
            post('confirm', {'index':0,'name':'Writer','acknowledged':True})
        finally:
            if thread.is_alive():
                try: post('cancel', {})
                except OSError: pass
            thread.join(5)
        self.assertNotIn('error', outcome)
        result = outcome['result']
        self.assertEqual(result['primary_keyword'], 'supabase alternatives')
        self.assertFalse(result['confirmation']['was_default'])
        self.assertTrue(result['warnings'][0]['acknowledged'])
        self.assertIn('supabase alternative', [r['keyword'] for r in result['supporting_candidates']])

    def serve(self, resolver, proposal):
        ready = threading.Event(); address = []; outcome = {}
        def run():
            try:
                outcome['result'] = confirmation_screen(resolver, proposal, lambda u: (address.append(u), ready.set()))
            except Exception as exc:
                outcome['error'] = exc
        thread = threading.Thread(target=run, daemon=True); thread.start()
        self.assertTrue(ready.wait(15))
        def post(action, data):
            req = Request(address[0]+'/'+action, json.dumps(data).encode(), {'Content-Type':'application/json'})
            try:
                with urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)
        def stop():
            if thread.is_alive():
                try: post('cancel', {})
                except OSError: pass
            thread.join(5)
        self.addCleanup(stop)
        return post, thread, outcome

    def test_provider_error_on_override_ends_run(self):
        api = FixtureAPI(); resolver = Resolver(api, load_config())
        proposal = resolver.resolve(TITLE, 'comparison')
        api.metrics = Mock(side_effect=ProviderError('DataForSEO down'))
        post, thread, outcome = self.serve(resolver, proposal)
        status, body = post('override', {'keyword':'supabase alternatives'})
        self.assertEqual((status, body.get('run_ended')), (422, True))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('result', outcome)
        self.assertIsInstance(outcome['error'], ProviderError)

    def test_bad_override_input_keeps_run_alive(self):
        api = FixtureAPI(); resolver = Resolver(api, load_config())
        proposal = resolver.resolve(TITLE, 'comparison')
        post, thread, outcome = self.serve(resolver, proposal)
        for keyword in ['', '   ', 'x'*101, None, 7]:
            status, body = post('override', {'keyword':keyword})
            self.assertEqual(status, 422, keyword)
            self.assertNotIn('run_ended', body)
            self.assertTrue(thread.is_alive())
        status, body = post('override', ['not', 'an', 'object'])
        self.assertEqual(status, 422)
        status, body = post('override', {'keyword':'supabase alternatives'})
        self.assertEqual(status, 200)
        self.assertEqual(body['options'][0]['keyword'], 'supabase alternatives')
        self.assertEqual(post('confirm', {'index':0,'name':'Writer'})[0], 200)
        thread.join(5)
        self.assertEqual(outcome['result']['primary_keyword'], 'supabase alternatives')

    def test_unavailable_override_metrics_keep_run_alive_and_options(self):
        api = FixtureAPI(); resolver = Resolver(api, load_config())
        proposal = resolver.resolve(TITLE, 'comparison')
        metrics = api.metrics
        api.metrics = lambda ks: {k:({'msv':None,'difficulty':None,'metric_status':'unavailable'} if k=='no data keyword' else v)
                                  for k, v in metrics(ks).items()}
        post, thread, outcome = self.serve(resolver, proposal)
        status, body = post('override', {'keyword':'no data keyword'})
        self.assertEqual(status, 422)
        self.assertIn('unavailable', body['error'])
        self.assertTrue(thread.is_alive())
        self.assertEqual(post('confirm', {'index':0,'name':'Writer'})[0], 200, 'original options still confirmable')
        thread.join(5)
        self.assertEqual(outcome['result']['primary_keyword'], 'supabase alternative')

    def test_weak_override_over_http_requires_acknowledgment(self):
        api = FixtureAPI(); resolver = Resolver(api, load_config())
        proposal = resolver.resolve(TITLE, 'comparison')
        api.volume = 40
        post, thread, outcome = self.serve(resolver, proposal)
        status, body = post('override', {'keyword':'tiny keyword'})
        self.assertEqual(status, 200)
        self.assertEqual(body['options'][0]['status'], 'needs writer confirmation')
        self.assertEqual(post('confirm', {'index':0,'name':'Writer','acknowledged':False})[0], 422)
        self.assertEqual(post('confirm', {'index':0,'name':'Writer','acknowledged':True})[0], 200)
        thread.join(5)
        self.assertTrue(outcome['result']['confirmation']['override_below_threshold'])

    @unittest.skipUnless(shutil.which('node'), 'Node required for browser-script unit test')
    def test_enter_arrow_and_acknowledgment_in_actual_ui_script(self):
        api = FixtureAPI(); api.rank = True
        proposal = Resolver(api, load_config()).resolve(TITLE, 'comparison')
        result = subprocess.run(['node', str(Path(__file__).with_name('keyword_confirmation_ui.cjs'))],
                                input=json.dumps({'html':HTML,'proposal':proposal}),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


OUT = io.StringIO()


class PipelineTests(unittest.TestCase):
    def test_confirmed_keyword_drives_calls_title_remains_angle_and_audit_is_saved(self):
        source = Path(__file__).resolve().parents[1] / 'brief.py'
        main = next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
        proposal = Resolver(FixtureAPI(), load_config()).resolve(TITLE, 'comparison')
        resolution = confirm_resolution(proposal,0,'Writer')
        events = []
        ns = dict(os=os,sys=sys,re=re,json=json,tempfile=tempfile,CONTENT_TYPES=['comparison'],
                  SCRIPT_DIR=str(source.parent),DATAFORSEO_LOGIN='test',DATAFORSEO_PASSWORD='test',
                  ANTHROPIC_API_KEY='test',ANTHROPIC_HAIKU_MODEL='test',SEMRUSH_API_KEY='test',
                  PINGCAP_DOMAIN='pingcap.com',SITEMAP_INVENTORY_FILE='unused',PINGCAP_SITEMAP_URL='unused',
                  anthropic=Mock(),record_api_cost=Mock(),validate_env=Mock(),
                  url_domain=lambda url:urlparse(url).hostname)
        for name, value in {'get_keyword_data':[], 'extract_headings_from_url':[], 'get_llm_mentions':{},
                            'get_backlinks_data':[], 'load_internal_link_inventory':([], 'fixture'),
                            'select_internal_link_candidates':[],
                            'validate_internal_link_candidates':[{'url':'https://www.pingcap.com/ai/','slot':1}],
                            'get_semrush_keyword_intent':{}, 'get_semrush_related_keywords':[],
                            'get_semrush_keyword_gap':[], 'get_semrush_domain_authority':[],
                            'generate_brief':'Draft', 'summarize_title':'Title', 'create_google_doc':'https://example.com/doc',
                            'print_cost_summary':None}.items():
            ns[name]=Mock(return_value=value)
        ns['get_keyword_data'].side_effect=lambda kw:events.append(('research',kw)) or []
        exec(compile(ast.Module(body=[main],type_ignores=[]),str(source),'exec'), ns)
        def confirm(*args):
            events.append(('confirmed',resolution['primary_keyword'])); return resolution
        with tempfile.TemporaryDirectory() as d, patch('keyword_resolver.ResearchAPI') as research, patch('keyword_resolver.Resolver') as resolver, patch('keyword_confirmation.confirmation_screen',side_effect=confirm), patch.object(sys,'argv',['brief.py',TITLE,'comparison','--priority-link-url','https://www.pingcap.com/ai/',
                                                                  '--priority-link-anchor','distributed SQL database for AI applications']), contextlib.redirect_stdout(OUT):
            resolver.return_value.resolve.return_value=proposal
            organic=[{'rank':i,'url':f'https://example{i}.com/page','title':f'Result {i}','description':''} for i in range(1,21)]
            research.return_value.serp.return_value={'organic':organic,'ai_overview':[],'paa_questions':['Q?','q?'],'featured_snippet':[]}
            research.return_value.relevance.return_value=[{'index':i,'relevance':0.9 if i%2==0 else 0.1,'page_type':'comparison',
                                                           'different_brand':False} for i in range(20)]
            research.return_value.extract.return_value={'competitor':'Supabase'}
            old=os.getcwd()
            try:
                os.chdir(d); ns['main']()
                report=json.loads(next(Path(d).glob('brief_run_*/validation.json')).read_text())
            finally: os.chdir(old)
        self.assertEqual([e[0] for e in events],['confirmed','research'])
        for name in ['get_llm_mentions','get_semrush_keyword_intent','get_semrush_related_keywords','get_semrush_keyword_gap']:
            self.assertEqual(ns[name].call_args.args[0],'supabase alternative',name)
        self.assertEqual(ns['select_internal_link_candidates'].call_args.args[1],'supabase alternative')
        self.assertEqual(ns['generate_brief'].call_args.args[0],TITLE)
        self.assertEqual(ns['generate_brief'].call_args.kwargs['keyword_resolution'],resolution)
        self.assertEqual(report['keyword_resolution'],resolution)
        self.assertEqual(report['status'],'validated')
        kwargs = ns['generate_brief'].call_args.kwargs
        self.assertEqual(kwargs['priority_link'], {'url':'https://www.pingcap.com/ai/',
                                                   'anchor':'distributed SQL database for AI applications'})
        self.assertEqual(kwargs['internal_link_candidates'][0]['selection_rule'], 'priority link (required)')
        self.assertEqual(kwargs['quality_context']['serp_source_keyword'], 'supabase alternative')
        self.assertEqual(research.return_value.serp.call_args.args, ('supabase alternative', 20))
        serp = ns['generate_brief'].call_args.args[3]
        self.assertEqual([r['rank'] for r in serp], list(range(1, 21, 2)), 'only relevant pages, ranks kept')
        self.assertEqual(ns['generate_brief'].call_args.args[4], ['Q?'], 'PAA deduplicated')


if __name__ == '__main__':
    unittest.main()
