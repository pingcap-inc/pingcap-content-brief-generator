import unittest
from keyword_resolver import Resolver, ResolutionError, SEOReviewRequired, load_config, comparison_matches_title
from test_keyword_resolver import FixtureAPI, MARIADB_ENTITIES
from test_brief_quality import finished, context, failing
import brief_quality as bq

class FollowupTests(unittest.TestCase):
    def test_wrong_manual_comparison_stops_before_metrics(self):
        api=FixtureAPI(); api.extract=lambda title:MARIADB_ENTITIES
        with self.assertRaisesRegex(ResolutionError,'compares different products'):
            Resolver(api,load_config()).resolve('TiDB vs. MariaDB','comparison','mariadb vs mysql')
        self.assertEqual(api.metrics_calls,[])

    def test_matching_manual_comparison_is_allowed(self):
        api=FixtureAPI(); api.extract=lambda title:MARIADB_ENTITIES
        result=Resolver(api,load_config()).resolve('TiDB vs. MariaDB','comparison','mariadb vs tidb')
        self.assertEqual(result['options'][0]['keyword'],'mariadb vs tidb')

    def test_spaced_comparison_cannot_bypass_pair_check(self):
        self.assertFalse(comparison_matches_title('mariadb v s postgresql','TiDB vs. MariaDB',MARIADB_ENTITIES))
        self.assertFalse(comparison_matches_title('mariadb v/s mysql','TiDB vs. MariaDB',MARIADB_ENTITIES))

    def test_installation_intent_cannot_enter_comparison_shortlist(self):
        api=FixtureAPI(); original=api.relevance
        api.relevance=lambda *args:[dict(p,page_type='documentation') for p in original(*args)]
        with self.assertRaises(SEOReviewRequired):
            Resolver(api,load_config()).resolve('TiDB vs. MariaDB','comparison')

    def test_model_title_suffix_is_restored_deterministically(self):
        text,ctx,_=finished()
        text=text.replace('# '+ctx['resolution']['title_angle'],'# '+ctx['resolution']['title_angle']+': Which Fits Your Scale?')
        corrected,notes=bq.apply_deterministic(text,ctx)
        self.assertNotIn('article_title',failing(corrected,ctx))
        self.assertIn('Restored the supplied article H1',notes)

    def test_equivalent_tidb_column_is_normalized(self):
        text,ctx,_=finished(ctx=context(competitor='Supabase'))
        text=text.replace('| Criteria | TiDB | Supabase |','| Category | TiDB product | Supabase |')
        corrected,_=bq.apply_deterministic(text,ctx)
        self.assertIn('| Criteria | TiDB | Supabase |',corrected)

    def test_bare_domains_do_not_count_as_source_urls(self):
        spec=bq.glance_spec(bq.template_for('comparison'))
        text='| Criteria | TiDB | MariaDB |\n|---|---|---|\n**Sources:** mariadb.com/kb/ (verify before publication)'
        self.assertTrue(any('https://' in p for p in bq._glance_problems(text,spec,'MariaDB')))

    def test_unqualified_false_vector_claim_is_rejected(self):
        spec=bq.glance_spec(bq.template_for('comparison'))
        text='| Criteria | TiDB | MariaDB |\n|---|---|---|\n| Vector support | VECTOR | No native VECTOR type |'
        self.assertTrue(any('11.7.1' in p for p in bq._glance_problems(text,spec,'MariaDB')))

    def test_repeated_technical_link_topic_is_not_discarded(self):
        text,ctx,_=finished()
        parts=bq.outline_parts(text); first=parts['h2s'][0][0]
        # Repeat the term across the whole outline, reproducing the ubiquity filter.
        text=text.replace('**Rationale**:','**Rationale**: Discuss MySQL scaling and compatibility.')
        ctx['link_candidates'] += [{'url':'https://www.pingcap.com/solutions/modernize-mysql-workloads/',
                                   'title':'MySQL Scaling Without Limits'}]
        text=text.replace('| Section (H2) | Anchor text | Target URL | Why |',
            '| Section (H2) | Anchor text | Target URL | Why |\n| '+first+' | modernize | https://www.pingcap.com/solutions/modernize-mysql-workloads/ | scaling context |')
        result=next(c for c in bq._link_checks(text,ctx,bq.outline_parts(text)) if c['id']=='internal_links_relevance')
        self.assertFalse(any('modernize-mysql' in d for d in result['details']),result)

    def test_link_vocabulary_matches_compatibility_and_scaling_variants(self):
        self.assertEqual(bq._stems({'compatible','scalable'}),bq._stems({'compatibility','scaling'}))


class CompareHouseFormatTests(unittest.TestCase):
    """Comparison and listicle outlines follow the live pingcap.com/compare/ pages."""

    def test_comparison_leads_with_glance_and_closes_with_faqs(self):
        template=bq.template_for('comparison')
        titles=['TiDB vs OceanBase at a Glance','What Makes TiDB and OceanBase Architecturally Different?',
                'How Do TiDB and OceanBase Compare on Compatibility and Migration?',
                'Which Database Performs Better as Workloads Grow?',
                'Which Platform Is Better for AI and Vector Workloads?',
                'Which Platform Is Easier to Run in Cloud-Native Environments?',
                'How Should Buyers Compare Pricing Models and Total Cost?',
                'Who Should Choose TiDB vs OceanBase?','How TiDB Helps Teams Outgrow OceanBase Limits',
                'TiDB vs OceanBase FAQs']
        found=bq.match_template_sections(titles,template)
        self.assertEqual([found[s['id']] for s in template['sections']],list(range(10)))
        self.assertEqual(bq.glance_spec(template)['columns'],['Criteria','TiDB','{competitor}'])

    def test_listicle_spotlight_uses_live_heading(self):
        template=bq.template_for('listicle')
        spec=next(s for s in template['sections'] if s['id']=='tidb_spotlight')
        import re
        self.assertTrue(any(re.search(p,'When Is TiDB the Best HTAP Database?') for p in spec['match']))


class SecondMariaDBRunTests(unittest.TestCase):
    """Failures from the "mariadb alternative" run (brief_failed_yw9rjm43)."""

    def _vector_problems(self,text):
        return [d for d in bq._facts_check(text)['details'] if d.startswith('mariadb_vector_support')]

    def test_version_qualified_vector_guardrail_is_not_a_false_claim(self):
        guard=('Do not claim MariaDB has no native vector support without specifying that this '
               'limitation applies only to versions prior to 11.7.1.')
        self.assertEqual(self._vector_problems(guard),[])
        self.assertEqual(self._vector_problems('Teams on MariaDB 10.11 lack native vector support before version 11.7.'),[])
        self.assertTrue(self._vector_problems('MariaDB has no native vector support, so use TiDB.'))
        self.assertTrue(self._vector_problems('MariaDB lacks native vector support. Version 11.7.1 is not covered here.'))

    def test_non_roster_case_studies_are_never_offered_as_links(self):
        roster_url=bq.roster()[0]['url']
        self.assertTrue(bq.link_allowed(roster_url))
        self.assertFalse(bq.link_allowed('https://www.pingcap.com/case-study/zalopay-using-a-scale-out-mysql-alternative-to-serve-millions-of-users/'))
        self.assertTrue(bq.link_allowed('https://www.pingcap.com/case-studies/'))
        self.assertTrue(bq.link_allowed('https://www.pingcap.com/compare/mysql-compatible-database/'))


class BriefLengthTests(unittest.TestCase):
    """Briefs stay within the approved sample length (about 2,500 words) for every type."""

    def test_fixture_is_within_length(self):
        text,ctx,_=finished()
        self.assertNotIn('brief_length',failing(text,ctx))
        self.assertLessEqual(bq.brief_words(text),bq.rules()['brief_length']['max_words'])

    def test_overlong_h2_is_sent_to_repair(self):
        text,ctx,_=finished()
        padded=text.replace('**Visual:** Table: billing model comparison.',
                            ' '.join(['Explain the billing model in more depth.']*40)+'\n\n**Visual:** Table: billing model comparison.')
        check=next(c for c in bq.run_checks(padded,ctx) if c['id']=='brief_length')
        self.assertFalse(check['passed'])
        self.assertEqual(check['units'],['Outline / Headings::h2_7'])

    def test_overlong_brief_is_rejected(self):
        text,ctx,_=finished()
        filler=' '.join(['word']*600)
        padded=text
        for unit in ('**Rationale**: Architecture queries','**Rationale**: Developers check','**Rationale**: Scale and availability',
                     '**Rationale**: AI queries'):
            padded=padded.replace(unit,'```\n'+filler+'\n```\n'+unit)
        self.assertNotIn('brief_length',failing(padded,ctx),'fenced code is not counted')
        long=text.replace('### Schema Markup Recommendations',(filler+'\n\n')*1+'### Schema Markup Recommendations')
        self.assertIn('brief_length',failing(long,ctx))

    def test_prompt_states_the_cap(self):
        for content_type in ['comparison','listicle','solution','blog']:
            base,checklist=bq.system_prompt_parts(content_type,{'url':'https://www.pingcap.com/ai/','anchor':'AI'},'Supabase')
            self.assertIn('2500 words',base)
            self.assertNotIn('at least 50%',base+checklist)
            self.assertNotIn('and a Visual line',checklist)
