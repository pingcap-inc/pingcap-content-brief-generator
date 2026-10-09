import re
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
        text=text.replace('| Category | Supabase | TiDB product | Best fit |','| Criteria | Supabase | TiDB | Best fit |')
        corrected,_=bq.apply_deterministic(text,ctx)
        self.assertIn('| Category | Supabase | TiDB product | Best fit |',corrected)

    def test_swapped_product_columns_are_reordered(self):
        text,ctx,_=finished(ctx=context(competitor='Supabase'))
        row='| Database model | Managed Postgres per project | TiDB Cloud Zero distributed SQL, MySQL compatible | Supabase for Postgres teams; TiDB for MySQL ecosystems |'
        cells=row.strip('|').split('|')
        swapped=text.replace('| Category | Supabase | TiDB product | Best fit |','| Category | TiDB product | Supabase | Best fit |').replace(
            row,'|'+'|'.join([cells[0],cells[2],cells[1],cells[3]])+'|')
        corrected,_=bq.apply_deterministic(swapped,ctx)
        self.assertIn('| Category | Supabase | TiDB product | Best fit |',corrected)
        self.assertIn('| Database model | Managed Postgres per project | TiDB Cloud Zero',corrected)
        self.assertNotIn('section_at_a_glance',failing(corrected,ctx))

    def test_bare_domains_do_not_count_as_source_urls(self):
        spec=bq.glance_spec(bq.template_for('comparison'))
        text='| Category | MariaDB | TiDB product | Best fit |\n|---|---|---|---|\n**Sources:** mariadb.com/kb/ (verify before publication)'
        self.assertTrue(any('https://' in p for p in bq._glance_problems(text,spec,'MariaDB')))

    def test_unqualified_false_vector_claim_is_rejected(self):
        spec=bq.glance_spec(bq.template_for('comparison'))
        text='| Category | MariaDB | TiDB product | Best fit |\n|---|---|---|---|\n| Vector support | No native VECTOR type | VECTOR | Version-specific |'
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
    """Comparisons follow the approved Aug 2026 briefs; listicles follow the live /compare/ pages."""

    def test_comparison_follows_the_approved_briefs(self):
        template=bq.template_for('comparison')
        self.assertEqual(bq.glance_spec(template)['columns'],['Category','{competitor}','TiDB product','Best fit'])
        ids=[s['id'] for s in template['sections']]
        self.assertEqual(ids[:2],['aeo_answer','at_a_glance'])
        self.assertIn('reviews',ids)
        self.assertEqual(ids[-2:],['faqs','decision'])

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


class ApprovedBriefPatternTests(unittest.TestCase):
    """Patterns learned from the approved PingCAP brief library (Drive, 2025-2026)."""

    def _drop_in(self,text):
        return [d for d in bq._facts_check(text)['details'] if d.startswith('tidb_mysql_compatible_not_drop_in')]

    def test_drop_in_claim_is_rejected_but_question_is_allowed(self):
        self.assertTrue(self._drop_in('TiDB is a drop-in replacement for MySQL.'))
        self.assertEqual(self._drop_in('Is TiDB a drop-in replacement for MySQL?'),[])
        self.assertEqual(self._drop_in('TiDB is not a drop-in replacement; test stored procedures.'),[])

    def test_blog_template_matches_an_approved_guide_outline(self):
        template=bq.template_for('blog')
        titles=['What is persistent AI agent memory?','Why agent memory breaks in production','How does persistent memory work?',
                'What teams get wrong about agent memory','Checklist for production-ready agent memory',
                'Where TiDB fits for persistent agent memory','Build persistent agent memory with TiDB Cloud',
                'Persistent AI agent memory FAQs']
        found=bq.match_template_sections(titles,template)
        self.assertEqual((found['tidb_fit'],found['closing'],found['faqs']),(5,6,7))
        self.assertEqual(template['primary_cta_section'],'closing')

    def test_listicle_table_is_a_spec_and_intro_is_answer_first(self):
        base,_=bq.system_prompt_parts('listicle',{'url':'https://www.pingcap.com/ai/','anchor':'AI'},None)
        self.assertIn('table SPEC, not a filled table',base)
        self.assertIn('40 to 60 word',base)
        self.assertIn('never call it a drop-in replacement',base)


class PressurePointsRunTests(unittest.TestCase):
    """From the "Seven Pressure Points" blog run (brief_failed_f8tbb0e6)."""

    def test_author_expertise_wording_satisfies_eeat(self):
        text,ctx,_=finished()
        h1=text[slice(*bq.outline_parts(text)['h1'])]
        reworded=text.replace(h1,re.sub(r'(?i)credentials?','relevant expertise',h1))
        self.assertNotIn('eeat',failing(reworded,ctx))

    def test_blog_prompt_handles_numbered_titles(self):
        base,_=bq.system_prompt_parts('blog',{'url':'https://www.pingcap.com/tidb/','anchor':'TiDB'},None)
        self.assertIn('Numbered titles',base)
        self.assertIn('not a pre-written answer',base)
