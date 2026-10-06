import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from keyword_resolver import (Cache, ResearchAPI, Resolver, ResolutionError, SEOReviewRequired,
                              load_config, generate_candidates, score, confirm_resolution, brief_header)

TITLE = 'TiDB Cloud Zero vs Supabase for AI Agent Backends'
ENTITIES = {'product':'TiDB Cloud Zero','competitor':'Supabase','category':'AI agent backend',
            'entity':'AI agent backend','task':'build AI agent backends','use_case':'AI agents',
            'head_entity':'Supabase','category_variants':['AI agent backends','agent backend platforms'],
            'parent_terms':['backend platform','serverless database']}


class FixtureAPI:
    def __init__(self):
        self.extract_calls = 0
        self.metrics_calls = []
        self.rank = False
        self.relevant = 10
        self.volume = None
        self.collision = False

    def extract(self, title):
        self.extract_calls += 1
        return ENTITIES

    def variants(self, head):
        return ['supabase alternative','supabase alternatives']

    def metrics(self, keywords):
        self.metrics_calls.append(keywords)
        return {k:{'msv':self.volume if self.volume is not None else 5000 if k=='supabase alternative' else 200,
                   'difficulty':30,'metric_status':'available'} for k in keywords}

    def serp(self, keyword):
        return {'organic':[{'rank':i+1,'url':('https://www.pingcap.com/existing/' if self.rank and i==0 else f'https://example.com/{i}'),
                            'title':'Comparison','description':'Agent backends'} for i in range(10)],
                'ai_overview_present':True,'pingcap_cited':False,'ai_overview':[],
                'cannibalization_source':'SERP fallback (GSC is not integrated)'}

    def relevance(self, title, keyword, results):
        return [{'index':i,'relevance':0.95 if i<self.relevant else 0.1,'page_type':'comparison',
                 'different_brand':self.collision} for i in range(10)]


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.api = FixtureAPI()
        self.resolver = Resolver(self.api,self.cfg)

    def test_requested_fixture_resolves_after_confirmation(self):
        p=self.resolver.resolve(TITLE,'comparison')
        self.assertEqual(p['options'][0]['keyword'],'supabase alternative')
        r=confirm_resolution(p,0,'Akshata Hire')
        self.assertEqual(r['primary_keyword'],'supabase alternative')
        self.assertEqual(r['title_angle'],TITLE)
        self.assertTrue(r['confirmation']['was_default'])
        self.assertTrue(r['supporting_candidates'])
        self.assertIn('Akshata Hire',brief_header(r))
        self.assertIn('supabase alternatives',brief_header(r))

    def test_config_candidate_generation_all_types(self):
        for kind in ['comparison','alternative','listicle','blog','explainer','guide','playbook','product','solution']:
            rows=generate_candidates(ENTITIES,kind,self.cfg,year=2026)
            self.assertTrue(10<=len(rows)<=20,(kind,len(rows)))
            self.assertEqual(len(rows),len(set(rows)))
            self.assertFalse(any('{' in k for k in rows))
        self.assertIn('supabase alternative',generate_candidates(ENTITIES,'comparison',self.cfg))
        self.cfg['patterns']['comparison']=['configured {competitor} pattern']*10
        self.assertEqual(generate_candidates(ENTITIES,'comparison',self.cfg)[0],'configured supabase pattern')

    def test_scoring_weights_and_authority(self):
        snap=self.api.serp('x'); pages=self.api.relevance(TITLE,'x',snap['organic'])
        result=score({'msv':1000,'difficulty':30},pages,snap,'comparison',self.cfg)
        self.assertAlmostEqual(result['total'],sum(result['weighted'].values()))
        self.assertEqual(result['weighted']['ai_opportunity'],0.1)
        snap['pingcap_cited']=True
        lower=score({'msv':1000,'difficulty':90},pages,snap,'comparison',self.cfg)
        self.assertLess(lower['total'],result['total'])

    def test_low_volume_tries_parents_then_routes_to_seo(self):
        self.api.volume=0
        with self.assertRaisesRegex(SEOReviewRequired,'SEO owner'):
            self.resolver.resolve(TITLE,'comparison')
        self.assertEqual(len(self.api.metrics_calls),2)
        self.assertIn('backend platform',self.api.metrics_calls[-1])

    def test_parent_retry_can_resolve(self):
        metrics=self.api.metrics
        self.api.metrics=lambda ks:{k:{**v,'msv':100 if 'backend platform' in k else 0} for k,v in metrics(ks).items()}
        p=self.resolver.resolve(TITLE,'comparison')
        self.assertIn('backend platform',p['options'][0]['keyword'])

    def test_insufficient_relevant_pages(self):
        self.api.relevant=4
        with self.assertRaisesRegex(SEOReviewRequired,'fewer than 5'):
            self.resolver.resolve(TITLE,'comparison')

    def test_enter_disabled_until_warning_acknowledged(self):
        self.api.rank=True
        p=self.resolver.resolve(TITLE,'comparison')
        with self.assertRaisesRegex(ResolutionError,'Acknowledge'):
            confirm_resolution(p,0,'Akshata')
        r=confirm_resolution(p,0,'Akshata',True)
        self.assertIn('https://www.pingcap.com/existing/',r['warnings'][0]['message'])
        self.assertTrue(r['warnings'][0]['acknowledged'])

    def test_collision_discard_and_suspicion(self):
        self.api.volume=50000; self.api.collision=True
        with self.assertRaises(SEOReviewRequired):
            self.resolver.resolve(TITLE,'comparison','tiadvisor')
        self.api.collision=False
        p=self.resolver.resolve(TITLE,'comparison','tiadvisor')
        self.assertTrue(any('collision' in w for w in p['options'][0]['warnings']))
        with self.assertRaises(ResolutionError):
            confirm_resolution(p,0,'Akshata')

    def test_override_only_validated_not_expanded(self):
        p=self.resolver.resolve(TITLE,'comparison','Supabase alternative')
        self.assertEqual(self.api.extract_calls,0)
        self.assertEqual(self.api.metrics_calls,[['supabase alternative']])
        r=confirm_resolution(p,0,'Akshata')
        self.assertFalse(r['confirmation']['was_default'])
        self.assertEqual(r['confirmation']['override_text'],'Supabase alternative')

    def test_api_failure_blocks(self):
        self.api.metrics=Mock(side_effect=ResolutionError('API down'))
        with self.assertRaisesRegex(ResolutionError,'API down'):
            self.resolver.resolve(TITLE,'comparison')

    def test_unknown_metrics_not_estimated(self):
        self.api.metrics=lambda ks:{k:{'msv':None,'difficulty':None,'metric_status':'unavailable'} for k in ks}
        with self.assertRaisesRegex(ResolutionError,'unavailable'):
            self.resolver.resolve(TITLE,'comparison','test keyword')

    def test_close_call(self):
        self.api.volume=200
        p=self.resolver.resolve(TITLE,'comparison')
        self.assertTrue(p['close_call'])


class ProviderTests(unittest.TestCase):
    def test_cache_expires_and_is_per_keyword(self):
        with tempfile.TemporaryDirectory() as d:
            now=[100]
            cache=Cache(d,86400,lambda:now[0]);cache.put(['serp','a'],{'ok':1})
            self.assertEqual(cache.get(['serp','a']),{'ok':1})
            self.assertIsNone(cache.get(['serp','b']))
            now[0]+=86400
            self.assertIsNone(cache.get(['serp','a']))

    def test_metrics_endpoints_and_per_keyword_cache(self):
        with tempfile.TemporaryDirectory() as d:
            api=ResearchAPI(load_config(),Cache(d),'login','password',Mock(),'test',session=Mock())
            api.post=Mock(side_effect=[ [{'keyword':'a','search_volume':50},{'keyword':'b','search_volume':100}],
                                       [{'items':[{'keyword':'a','keyword_difficulty':10},{'keyword':'b','keyword_difficulty':20}]}] ])
            result=api.metrics(['a','b'])
            self.assertEqual(result['a']['msv'],50)
            self.assertEqual(api.metrics(['b']),{'b':result['b']})
            self.assertEqual(api.post.call_count,2)
            self.assertEqual(api.post.call_args_list[0].args[0],'keywords_data/google_ads/search_volume/live')
            self.assertEqual(api.post.call_args_list[1].args[0],'dataforseo_labs/google/bulk_keyword_difficulty/live')
            self.assertEqual(api.post.call_args_list[0].args[1]['location_code'],2840)

    def test_task_failure_not_silently_empty(self):
        response=Mock();response.json.return_value={'status_code':20000,'tasks':[{'status_code':40100,'status_message':'bad auth'}]}
        session=Mock();session.post.return_value=response
        api=ResearchAPI(load_config(),Mock(),'login','password',Mock(),'test',session=session)
        with self.assertRaises(ResolutionError):api.post('x',{})

    def test_serp_citations_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            api=ResearchAPI(load_config(),Cache(d),'login','password',Mock(),'test',session=Mock())
            api.post=Mock(return_value=[{'items':[{'type':'ai_overview','references':[{'url':'https://www.pingcap.com/article/x'}]},
                {'type':'organic','rank_group':1,'url':'https://www.pingcap.com/x','title':'Example'}]}])
            snap=api.serp('a')
            self.assertTrue(snap['pingcap_cited'])
            self.assertTrue(snap['ai_overview_present'])
            self.assertIn('fallback',snap['cannibalization_source'])


if __name__=='__main__':unittest.main()
