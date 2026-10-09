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
            self.assertIn(f"{bq.max_brief_words(bq.template_for(content_type))} words",base)
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

    def test_author_bio_is_not_required(self):
        # The author is unknown when the brief is written.
        self.assertNotIn('author_bio',[g['id'] for g in bq.rules()['eeat']])
        base,_=bq.system_prompt_parts('blog',{'url':'https://www.pingcap.com/tidb/','anchor':'TiDB'},None)
        self.assertIn('do not ask for an author bio',base)

    def test_missing_eeat_instruction_is_added_not_failed(self):
        text,ctx,_=finished()
        stripped=re.sub(r'(?i)[^.]*expert review[^.]*\.','',text,count=1)
        self.assertNotEqual(stripped,text)
        out,notes=bq.apply_deterministic(stripped,ctx)
        self.assertTrue(any('expert_review' in n for n in notes),notes)
        self.assertNotIn('eeat',failing(out,ctx))

    def test_blog_prompt_handles_numbered_titles(self):
        base,_=bq.system_prompt_parts('blog',{'url':'https://www.pingcap.com/tidb/','anchor':'TiDB'},None)
        self.assertIn('Numbered titles',base)
        self.assertIn('not a pre-written answer',base)


class SecondPressurePointsRunTests(unittest.TestCase):
    """From the second "Seven Pressure Points" run (brief_failed_jm1yocyx)."""

    def setUp(self):
        self.text,self.ctx,_=finished()
        self.ctx['plan']={'primary_keyword_msv':320,'tier':'<500','minimum':1800,'maximum':2500}

    def test_section_caps_never_add_up_past_the_total(self):
        # Pad every body H2 just under the static cap: the shared cap must still flag them.
        text=self.text
        for title,*_ in bq.outline_parts(text)['h2s'][2:8]:
            text=text.replace(f'## {title}\n',f'## {title}\n\n'+' '.join(['detail']*90)+'\n',1)
        check=next(c for c in bq.run_checks(text,self.ctx) if c['id']=='brief_length')
        cfg=bq.rules()['brief_length']
        if bq.brief_words(text)>cfg['max_words']*(1+cfg['overshoot_tolerance']):
            self.assertTrue(any('::h2_' in u for u in check['units']),check)

    def test_missing_h1_target_is_filled_within_the_tier(self):
        h1=self.text[slice(*bq.outline_parts(self.text)['h1'])]
        target=re.search(r'(?m)^Target:[^\n]*\n',h1).group(0)
        ctx=dict(self.ctx,plan={'primary_keyword_msv':5000,'tier':'5,000+','minimum':3500,'maximum':4500})
        out,notes=bq.apply_deterministic(self.text.replace(target,'',1),ctx)
        self.assertIn('Added the missing H1 Target line',notes)
        self.assertRegex(out[slice(*bq.outline_parts(out)['h1'])],r'(?m)^Target: ~\d+–\d+ words')

    def test_word_count_section_is_written_from_the_plan_and_keeps_extras(self):
        text=self.text+'\n---\n\n### Sources\n\n- https://example.com/a\n'
        out,_=bq.apply_deterministic(text,self.ctx)
        section=out[out.index('### Word Count Target'):]
        self.assertIn('Primary keyword MSV: 320. Tier: <500. Target: 1,800–2,500 words.',section)
        self.assertIn('### Sources',section)
        self.assertNotIn('brief_length',{c['id'] for c in bq.run_checks(out,self.ctx) if not c['passed']})

    def test_faq_ending_with_a_rule_is_still_bullets_only(self):
        last='- Compare billing models rather than list prices.\n'
        self.assertIn(last,self.text)
        self.assertNotIn('faqs',failing(self.text.replace(last,last+'\n---\n',1),self.ctx))


class ThirdPressurePointsRunTests(unittest.TestCase):
    """From the third "Seven Pressure Points" run (brief_failed_uf6e0vef)."""

    def test_blogs_allow_twelve_h2s(self):
        self.assertEqual(bq.template_for('blog')['max_h2'],12)
        self.assertEqual(bq.template_for('product')['max_h2'],10)

    def test_outline_rewrite_also_replaces_links(self):
        plan=bq.repair_plan([{'id':'template_sections','passed':False,'details':['12 H2s; maximum 10'],
                              'units':['Outline / Headings']}])
        self.assertIn('Internal Links',plan)
        self.assertIn('Outline / Headings',plan)


class ScalabilityExplainedRunTests(unittest.TestCase):
    """From "Database Scalability Explained" (brief_failed_i_95v1lj): 2,399 words, failed on two small overruns."""

    def test_small_section_overrun_passes_when_total_fits(self):
        text,ctx,_=finished()
        padded=text.replace('**Visual:** Table: billing model comparison.',
                            ' '.join(['note']*30)+'\n\n**Visual:** Table: billing model comparison.')
        self.assertLessEqual(bq.brief_words(padded),bq.rules()['brief_length']['max_words'])
        self.assertNotIn('brief_length',failing(padded,ctx))

    def test_badly_oversized_section_still_fails_when_total_fits(self):
        text,ctx,_=finished()
        padded=text.replace('**Visual:** Table: billing model comparison.',
                            ' '.join(['note']*200)+'\n\n**Visual:** Table: billing model comparison.')
        self.assertLessEqual(bq.brief_words(padded),bq.rules()['brief_length']['max_words'])
        self.assertIn('brief_length',failing(padded,ctx))


class TierFitRunTests(unittest.TestCase):
    """From brief_failed_o5wrtqr8: H2 Target upper bounds summed to 2,530 for a 1,800-2,500 tier."""

    def test_targets_are_scaled_into_the_tier_keeping_formatting(self):
        text,ctx,_=finished()
        ctx=dict(ctx,plan={'primary_keyword_msv':5000,'tier':'5,000+','minimum':3500,'maximum':4000})
        out,notes=bq.apply_deterministic(text,ctx)
        self.assertTrue(any('Scaled section Target ranges' in n for n in notes),notes)
        parts=bq.outline_parts(out)
        spans=([parts['h1']] if parts['h1'] else [])+[(s,e) for _,s,e in parts['h2s']]
        his=[]; los=[]
        for s,e in spans:
            m=re.search(r'Target: ~(\d+)–(\d+) words',out[s:e])
            los.append(int(m.group(1))); his.append(int(m.group(2)))
        self.assertLessEqual(sum(his),4000)
        self.assertGreaterEqual(sum(los),3500)
        self.assertNotIn('words words',out)

    def test_bold_target_lines_keep_their_markers(self):
        text,ctx,_=finished()
        text=text.replace('Target: ~288–352 words','**Target:** ~288–352 words',1)
        ctx=dict(ctx,plan={'primary_keyword_msv':5000,'tier':'5,000+','minimum':3500,'maximum':4000})
        out,_=bq.apply_deterministic(text,ctx)
        self.assertRegex(out,r'\*\*Target:\*\* ~\d+–\d+ words')


class MultiTenantListicleRunTests(unittest.TestCase):
    """From "Best Database for Multi-Tenant AI Apps" (brief_failed_oejb314f)."""

    def test_roster_url_followed_by_semicolon_is_accepted(self):
        text,ctx,_=finished()
        url=bq.roster()[0]['url']
        bad=text.replace('Cite Manus as an agent platform customer: https://www.pingcap.com/case-study/manus-agentic-ai-database-tidb/',
                         f'Cite a customer: {url}; verify numbers.')
        self.assertNotEqual(bad,text)
        self.assertFalse(any('not in the customer roster' in d for c in bq.run_checks(bad,ctx) for d in c['details']))

    def test_spotlight_heading_variants_match(self):
        spec=next(s for s in bq.template_for('listicle')['sections'] if s['id']=='tidb_spotlight')
        for title in ['When does TiDB fit a multi-tenant AI app?','When Is TiDB the Best HTAP Database?','How TiDB handles tenants']:
            self.assertTrue(any(re.search(p,title) for p in spec['match']),title)
        self.assertFalse(any(re.search(p,'How do you choose the right database?') for p in spec['match']))

    def test_year_is_removed_from_meta_title(self):
        text,ctx,_=finished()
        title='Supabase Alternative for AI Agent Backends - PingCAP'
        out,notes=bq.apply_deterministic(text.replace(title,'Supabase Alternative for AI Agent Backends 2025 - PingCAP'),ctx)
        self.assertEqual(bq.meta_cells(out)['Meta Title'],title)
        self.assertIn('Removed the year from the Meta Title',notes)


class ListicleCapTests(unittest.TestCase):
    """Listicles get a 2,800-word cap (user decision); other types stay at 2,500."""

    def test_caps_by_type(self):
        self.assertEqual(bq.max_brief_words(bq.template_for('listicle')),2800)
        for t in ('comparison','blog','solution','product'):
            self.assertEqual(bq.max_brief_words(bq.template_for(t)),2500)

    def test_prompt_states_the_type_cap(self):
        base,_=bq.system_prompt_parts('listicle',{'url':'https://www.pingcap.com/ai/','anchor':'AI'},None)
        self.assertIn('2800 words',base)
        base,_=bq.system_prompt_parts('blog',{'url':'https://www.pingcap.com/ai/','anchor':'AI'},None)
        self.assertIn('2500 words',base)


class AimBelowCapTests(unittest.TestCase):
    """Models overshoot a stated limit, so the prompt and repairs aim below the cap."""

    def test_prompt_aims_below_the_cap(self):
        for t,cap in (('listicle',2800),('blog',2500)):
            base,_=bq.system_prompt_parts(t,{'url':'https://www.pingcap.com/tidb/','anchor':'TiDB'},None)
            target=int(re.search(r'Aim for about (\d+) words',base).group(1))
            self.assertLess(target,cap)
            self.assertIn(f'hard maximum is {cap} words',base)

    def test_repair_messages_give_a_concrete_target(self):
        text,ctx,_=finished()
        padded=text.replace('**Visual:** Table: billing model comparison.',
                            ' '.join(['note']*700)+'\n\n**Visual:** Table: billing model comparison.')
        check=next(c for c in bq.run_checks(padded,ctx) if c['id']=='brief_length')
        self.assertTrue(any('Rewrite it to about' in d for d in check['details']),check['details'])


class OvershootToleranceTests(unittest.TestCase):
    """Up to 10% over the cap passes with a warning (content team decision)."""

    def _pad(self,words):
        text,ctx,_=finished()
        # Meta Elements has no section cap, so only the total moves.
        return text.replace('### Page Goal',' '.join(['note']*words)+'\n\n### Page Goal',1),ctx

    def test_within_tolerance_passes_with_warning(self):
        text,ctx=self._pad(2500+150-bq.brief_words(finished()[0]))
        check=next(c for c in bq.run_checks(text,ctx) if c['id']=='brief_length')
        self.assertTrue(check['passed'],check)
        self.assertTrue(check.get('warnings'))

    def test_beyond_tolerance_fails(self):
        text,ctx=self._pad(2500+400-bq.brief_words(finished()[0]))
        self.assertIn('brief_length',failing(text,ctx))


class UnvalidatedDraftUploadTests(unittest.TestCase):
    """Failed briefs still reach the Drive folder, marked UNVALIDATED with their failures on top."""

    def _namespace(self, calls):
        import ast, os, pathlib
        from unittest.mock import Mock
        source = (pathlib.Path(__file__).resolve().parents[1] / "brief.py").read_text()
        wanted = {"drive_doc_title", "upload_unvalidated_draft"}
        body = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        ns = {"os": os, "re": re, "summarize_title": Mock(return_value="Multi-Tenant AI Databases"),
              "create_google_doc": lambda title, content: calls.append((title, content)) or "https://docs.google.com/document/d/x/edit"}
        exec(compile(ast.Module(body=body, type_ignores=[]), "brief.py", "exec"), ns)
        return ns

    def test_failed_draft_is_uploaded_with_its_failures(self):
        import tempfile, os
        calls = []
        ns = self._namespace(calls)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "draft.md"), "w") as f:
                f.write("<!-- UNVALIDATED DRAFT: not approved. -->\n\n# Brief body")
            url = ns["upload_unvalidated_draft"]("Best Database for Multi-Tenant AI Apps", "listicle",
                                                 {"draft_dir": d, "errors": ["brief_length: Brief is 3200 words"]})
        self.assertTrue(url.startswith("https://docs.google.com/"))
        title, content = calls[0]
        self.assertEqual(title, "[UNVALIDATED] Content Brief: Multi-Tenant AI Databases [listicle]")
        self.assertIn("- brief_length: Brief is 3200 words", content)
        self.assertIn("# Brief body", content)
        self.assertNotIn("<!--", content)

    def test_nothing_to_upload_without_a_draft(self):
        calls = []
        self.assertIsNone(self._namespace(calls)["upload_unvalidated_draft"]("T", "blog", {}))
        self.assertEqual(calls, [])


class MariaDBComparisonRerunTests(unittest.TestCase):
    """From the TiDB vs. MariaDB rerun (brief_failed_2dzpg4zs)."""

    def test_banned_word_inside_a_url_is_not_flagged(self):
        text,ctx,_=finished()
        url='https://www.pingcap.com/webinars/accelerate-growth-with-a-mysql-alternative-battle-tested-at-any-scale/'
        withurl=text.replace('Cover MCP servers for both products,',f'Cover MCP servers for both products (see {url}),')
        self.assertNotEqual(withurl,text)
        self.assertNotIn('style_lint',failing(withurl,ctx))
        self.assertIn('style_lint',failing(text.replace('Cover MCP servers','Cover battle-tested MCP servers'),ctx))

    def test_verify_url_note_is_removed(self):
        text,ctx,_=finished()
        noted=text.replace('Cover MCP servers for both products,','Cover MCP servers for both products (verify URL before including),')
        self.assertNotEqual(noted,text)
        out,notes=bq.apply_deterministic(noted,ctx)
        self.assertNotIn('verify URL',out)
        self.assertIn("Removed 'verify URL' placeholder notes",notes)
        self.assertNotIn('primary_cta',failing(out,ctx))


class RunFolderTests(unittest.TestCase):
    """Local folders are named "<title> [<type>] vN" so runs are easy to find."""

    def test_versions_increment_per_title_and_type(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            a=bq.run_folder('TiDB vs. MariaDB','comparison',root)
            b=bq.run_folder('TiDB vs. MariaDB','comparison',root)
            c=bq.run_folder('TiDB vs. MariaDB','blog',root)
            self.assertEqual([os.path.basename(p) for p in (a,b,c)],
                             ['TiDB vs. MariaDB [comparison] v1','TiDB vs. MariaDB [comparison] v2','TiDB vs. MariaDB [blog] v1'])

    def test_unsafe_characters_are_replaced(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            path=bq.run_folder('CI/CD: Why? "Databases" break','blog',root)
            self.assertEqual(os.path.basename(path),'CI CD Why Databases break [blog] v1')
