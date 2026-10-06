import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from keyword_resolver import (Cache, ProviderError, ResearchAPI, Resolver, ResolutionError, SEOReviewRequired,
                              load_config, generate_candidates, score, confirm_resolution, brief_header,
                              validate_resolution)

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
        with self.assertRaisesRegex(ResolutionError,"Review the warnings and tick .I've reviewed these results"):
            confirm_resolution(p,0,'Akshata')
        r=confirm_resolution(p,0,'Akshata',True)
        self.assertIn('https://www.pingcap.com/existing/',r['warnings'][0]['message'])
        self.assertTrue(r['warnings'][0]['acknowledged'])

    def test_collision_discard_and_suspicion(self):
        self.api.volume=50000; self.api.collision=True
        self.cfg['collision_max_words']=20  # every automatic candidate is a suspect
        with self.assertRaises(SEOReviewRequired):
            self.resolver.resolve(TITLE,'comparison')
        self.cfg['collision_max_words']=2
        self.api.collision=False
        p=self.resolver.resolve(TITLE,'comparison','tiadvisor')
        self.assertTrue(any('collision' in w for w in p['options'][0]['warnings']))
        with self.assertRaises(ResolutionError):
            confirm_resolution(p,0,'Akshata')

    def assert_weak_override(self,p,expected):
        option=p['options'][0]
        self.assertEqual(len(p['options']),1)
        self.assertEqual(option['status'],'needs writer confirmation')
        self.assertEqual(option['threshold_failures'],expected)
        for message in expected:self.assertIn(message,option['warnings'])
        with self.assertRaisesRegex(ResolutionError,'Review the warnings'):
            confirm_resolution(p,0,'Akshata')
        r=confirm_resolution(p,0,'Akshata',True)
        self.assertTrue(r['confirmation']['override_below_threshold'])
        self.assertEqual(r['confirmation']['threshold_failures'],expected)
        self.assertIs(validate_resolution(r),r)
        header=brief_header(r)
        self.assertIn('Override below threshold: yes',header)
        for message in expected:self.assertIn(message.replace("'",""),header.replace("'",""))
        return r

    def test_weak_override_low_volume_needs_confirmation(self):
        self.api.volume=40
        p=self.resolver.resolve(TITLE,'comparison','tiny keyword')
        self.assert_weak_override(p,['Only 40 monthly searches (minimum 50).'])
        self.assertEqual(self.api.metrics_calls,[['tiny keyword']],'override is not expanded to parents')

    def test_weak_override_few_relevant_pages_needs_confirmation(self):
        self.api.relevant=3
        p=self.resolver.resolve(TITLE,'comparison','loose keyword')
        self.assert_weak_override(p,["3 of 10 top results match your article's angle (minimum 5)."])

    def test_weak_override_collision_needs_confirmation(self):
        self.api.volume=50000; self.api.collision=True
        p=self.resolver.resolve(TITLE,'comparison','tiadvisor')
        self.assertTrue(any('Brand-collision suspicion' in w for w in p['options'][0]['warnings']))
        self.assert_weak_override(p,['Top results look like a different brand or topic.'])

    def test_weak_override_reports_every_failed_check(self):
        self.api.volume=10; self.api.relevant=0
        p=self.resolver.resolve(TITLE,'comparison','two failures')
        self.assert_weak_override(p,['Only 10 monthly searches (minimum 50).',
                                     "0 of 10 top results match your article's angle (minimum 5)."])

    def test_strong_override_records_no_threshold_failure(self):
        p=self.resolver.resolve(TITLE,'comparison','supabase alternative')
        self.assertEqual(p['options'][0]['status'],'eligible')
        r=confirm_resolution(p,0,'Akshata')
        self.assertFalse(r['confirmation']['override_below_threshold'])
        self.assertEqual(r['confirmation']['threshold_failures'],[])
        self.assertIn('Override below threshold: no',brief_header(r))

    def test_automatic_path_still_routes_to_seo_owner(self):
        self.api.volume=10
        with self.assertRaises(SEOReviewRequired):self.resolver.resolve(TITLE,'comparison')
        self.api.volume=None; self.api.relevant=3
        with self.assertRaises(SEOReviewRequired):self.resolver.resolve(TITLE,'comparison')

    def test_proposal_carries_page_thresholds(self):
        p=self.resolver.resolve(TITLE,'comparison')
        self.assertEqual((p['relevance_threshold'],p['min_relevant_pages']),(0.6,5))

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
        with self.assertRaisesRegex(ResolutionError,'unavailable') as raised:
            self.resolver.resolve(TITLE,'comparison','test keyword')
        self.assertNotIsInstance(raised.exception,ProviderError,'writer can retry another phrasing')

    def test_duplicate_variants_deduplicated_before_metrics(self):
        self.api.variants=lambda head:['Supabase Alternative','supabase  alternative','supabase alternative']
        self.resolver.resolve(TITLE,'comparison')
        first=self.api.metrics_calls[0]
        self.assertEqual(len(first),len(set(first)))
        self.assertEqual(first.count('supabase alternative'),1)

    def test_parent_retry_runs_before_unavailable_metrics_error(self):
        self.api.metrics=lambda ks:(self.api.metrics_calls.append(ks) or {k:{'msv':None,'difficulty':None,'metric_status':'unavailable'} for k in ks})
        with self.assertRaisesRegex(ResolutionError,'unavailable for'):
            self.resolver.resolve(TITLE,'comparison')
        self.assertEqual(len(self.api.metrics_calls),2)
        self.assertIn('backend platform',self.api.metrics_calls[-1])

    def test_invalid_option_index_message(self):
        p=self.resolver.resolve(TITLE,'comparison')
        for index in [-1,len(p['options']),'0',None,True]:
            with self.assertRaisesRegex(ResolutionError,'option 1–'):
                confirm_resolution(p,index,'Akshata')

    def test_blank_confirmer_rejected(self):
        p=self.resolver.resolve(TITLE,'comparison')
        for name in ['','   ',None]:
            with self.assertRaisesRegex(ResolutionError,'name'):
                confirm_resolution(p,0,name)

    def test_invalid_override_text_rejected(self):
        for value in ['','   ','x'*101]:
            with self.assertRaisesRegex(ResolutionError,'at most 100'):
                self.resolver.resolve(TITLE,'comparison',value)

    def test_close_call(self):
        self.api.volume=200
        p=self.resolver.resolve(TITLE,'comparison')
        self.assertTrue(p['close_call'])


class ResolutionContractTests(unittest.TestCase):
    def setUp(self):
        p=Resolver(FixtureAPI(),load_config()).resolve(TITLE,'comparison')
        self.valid=confirm_resolution(p,0,'Akshata')

    def mutated(self,path,value):
        import copy
        r=copy.deepcopy(self.valid);node=r
        for key in path[:-1]:node=node[key]
        if value is KeyError:del node[path[-1]]
        else:node[path[-1]]=value
        return r

    def test_confirmed_resolution_passes(self):
        self.assertIs(validate_resolution(self.valid),self.valid)

    def test_malformed_resolution_names_field(self):
        cases=[(['primary_keyword'],''),(['primary_keyword'],KeyError),(['title_angle'],None),
               (['primary_metrics','msv'],None),(['primary_metrics','msv'],-1),(['primary_metrics','difficulty'],101),
               (['scores'],[]),(['supporting_candidates'],{}),(['supporting_candidates',0,'scores'],{'total':None}),
               (['serp_snapshot','organic'],None),(['serp_snapshot','organic'],['x']),(['serp_snapshot','ai_overview'],KeyError),
               (['serp_snapshot','paa_questions'],'q'),(['warnings'],[{'message':'m','acknowledged':False}]),
               (['confirmation','confirmed_by'],' '),(['confirmation','was_default'],'yes'),(['confirmation'],KeyError),
               (['confirmation','override_below_threshold'],KeyError),(['confirmation','override_below_threshold'],True),
               (['confirmation','threshold_failures'],None),(['confirmation','threshold_failures'],['Only 1 monthly searches.'])]
        for path,value in cases:
            field='.'.join(str(p) if isinstance(p,str) else f'[{p}]' for p in path).replace('.[','[')
            with self.subTest(field=field,value=value):
                with self.assertRaisesRegex(ResolutionError,'field '+field.split('[')[0].split('.')[0]):
                    validate_resolution(self.mutated(path,value))
        with self.assertRaisesRegex(ResolutionError,'field resolution'):
            validate_resolution(None)


class ProviderTests(unittest.TestCase):
    def test_cache_expires_and_is_per_keyword(self):
        with tempfile.TemporaryDirectory() as d:
            now=[100]
            cache=Cache(d,86400,lambda:now[0]);cache.put(['serp','a'],{'ok':1})
            self.assertEqual(cache.get(['serp','a']),{'ok':1})
            self.assertIsNone(cache.get(['serp','b']))
            now[0]+=86400
            self.assertIsNone(cache.get(['serp','a']))

    def test_future_dated_cache_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            now=[1000]
            cache=Cache(d,86400,lambda:now[0]);cache.put(['serp','a'],{'ok':1})
            now[0]=500  # clock moved backwards
            self.assertIsNone(cache.get(['serp','a']))

    def test_corrupt_cache_entry_is_a_miss(self):
        with tempfile.TemporaryDirectory() as d:
            cache=Cache(d);cache.put(['serp','a'],{'ok':1})
            for f in Path(d).glob('*.json'):f.write_text('{not json')
            self.assertIsNone(cache.get(['serp','a']))

    def test_metrics_response_case_and_spacing_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            api=ResearchAPI(load_config(),Cache(d),'login','password',Mock(),'test',session=Mock())
            api.post=Mock(side_effect=[[{'keyword':'Supabase  Alternative','search_volume':900}],
                                       [{'items':[{'keyword':'SUPABASE ALTERNATIVE','keyword_difficulty':40}]}]])
            row=api.metrics(['supabase alternative'])['supabase alternative']
            self.assertEqual((row['msv'],row['difficulty'],row['metric_status']),(900,40,'available'))

    def test_missing_and_invalid_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            api=ResearchAPI(load_config(),Cache(d),'login','password',Mock(),'test',session=Mock())
            api.post=Mock(side_effect=[[{'keyword':'a','search_volume':None}],[{'items':[]}]])
            self.assertEqual(api.metrics(['a'])['a']['metric_status'],'unavailable')
            api.post=Mock(side_effect=[[{'keyword':'b','search_volume':-5}],[{'items':[]}]])
            with self.assertRaisesRegex(ProviderError,'invalid search volume for b'):api.metrics(['b'])
            api.post=Mock(side_effect=[[{'keyword':'c','search_volume':5}],[{'items':[{'keyword':'c','keyword_difficulty':150}]}]])
            with self.assertRaisesRegex(ProviderError,'invalid keyword difficulty for c'):api.metrics(['c'])

    def test_judgment_errors_name_keyword_and_counts(self):
        api=ResearchAPI(load_config(),Mock(),'login','password',Mock(),'test',session=Mock())
        api.judge=Mock(return_value={'pages':[{'index':0,'relevance':0.5,'page_type':'docs','different_brand':False}]})
        with self.assertRaisesRegex(ResolutionError,'"kw" returned 1 pages; expected 2'):
            api.relevance(TITLE,'kw',[{},{}])
        api.judge=Mock(return_value={'pages':[{'index':0,'relevance':2,'page_type':'docs','different_brand':False}]})
        with self.assertRaisesRegex(ResolutionError,'invalid fields at result 0'):
            api.relevance(TITLE,'kw',[{}])

    def test_entity_extraction_names_missing_fields(self):
        api=ResearchAPI(load_config(),Mock(),'login','password',Mock(),'test',session=Mock())
        api.judge=Mock(return_value={**ENTITIES,'task':'','head_entity':None})
        with self.assertRaisesRegex(ResolutionError,'task, head_entity'):
            api.extract(TITLE)

    def test_config_errors_name_fields(self):
        base=json.loads((Path(__file__).resolve().parents[1]/'config/keyword_resolver.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'c.json'
            for change,pattern in [({'min_msv':KeyError},'missing: min_msv'),({'min_msv':0},'positive integers: min_msv'),
                                   ({'relevance_threshold':1.5},'zero and one: relevance_threshold')]:
                cfg={**base}
                for k,v in change.items():
                    if v is KeyError:del cfg[k]
                    else:cfg[k]=v
                path.write_text(json.dumps(cfg))
                with self.assertRaisesRegex(ResolutionError,pattern):load_config(path)
            path.write_text('[]')
            with self.assertRaisesRegex(ResolutionError,'JSON object'):load_config(path)

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
        with self.assertRaises(ProviderError):api.post('x',{})
        session.post.side_effect=OSError('network down')
        with self.assertRaisesRegex(ProviderError,'OSError'):api.post('x',{})

    def test_llm_semrush_and_serp_failures_are_provider_errors(self):
        client=Mock();client.messages.create.side_effect=RuntimeError('overloaded')
        with tempfile.TemporaryDirectory() as d:
            api=ResearchAPI(load_config(),Cache(d),'login','password',client,'test',session=Mock())
            with self.assertRaises(ProviderError):api.extract(TITLE)
            client.messages.create.side_effect=None
            client.messages.create.return_value=Mock(stop_reason='max_tokens',content=[])
            with self.assertRaisesRegex(ProviderError,'truncated'):api.relevance(TITLE,'kw',[{}])
            api.judge=Mock(return_value=['not an object'])
            with self.assertRaises(ProviderError):api.extract(TITLE)
            api.post=Mock(return_value=[{'items':None}])
            with self.assertRaisesRegex(ProviderError,'SERP response is unavailable'):api.serp('a')
            api.post=Mock(return_value=[])
            api.semrush_key='key'
            for text in ['ERROR 120 :: WRONG KEY','garbage']:
                api.session.get.return_value=Mock(text=text)
                with self.assertRaises(ProviderError):api.variants('head '+text)
            api.session.get.side_effect=OSError('down')
            with self.assertRaises(ProviderError):api.variants('head down')
            api.session.get.side_effect=None
            api.session.get.return_value=Mock(text='ERROR 50 :: NOTHING FOUND')
            self.assertEqual(api.variants('head nothing'),[])

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
