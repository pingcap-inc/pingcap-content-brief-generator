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
        text=text.replace('| Category | Supabase | TiDB product | Best fit |','| Category | Supabase | TiDB | Best fit |')
        corrected,_=bq.apply_deterministic(text,ctx)
        self.assertIn('| Category | Supabase | TiDB product | Best fit |',corrected)

    def test_bare_domains_do_not_count_as_source_urls(self):
        spec=bq.template_for('comparison')['sections'][1]['table']
        text='| Category | MariaDB | TiDB product | Best fit |\n|---|---|---|---|\n**Sources:** mariadb.com/kb/ (verify before publication)'
        self.assertTrue(any('https://' in p for p in bq._glance_problems(text,spec,'MariaDB')))

    def test_unqualified_false_vector_claim_is_rejected(self):
        spec=bq.template_for('comparison')['sections'][1]['table']
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
