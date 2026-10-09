"""Exercise production functions without requiring credentials or optional SDK imports."""
import ast
import os
import tempfile
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
FUNCTIONS = {'_save_failed_brief', 'validate_serp_blocks', 'markdown_lines', 'resolve_internal_link_ids', 'unresolve_internal_link_ids', 'parse_word_budget', 'normalize_brief_headings', 'word_count_plan', 'brief_sections', 'split_brief', 'validate_brief',
             'check_pingcap_ranking', 'get_semrush_keyword_gap', 'get_serp_and_paa',
             'generate_brief', 'semrush_get', 'summarize_title', 'url_domain'}
CONSTANTS = {'_BRIEF_SECTIONS', '_BASE_INSTRUCTIONS', '_QUALITY_CHECKLIST'}
selected = [n for n in tree.body if
            isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS or
            isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets)]

SEED = [{'is_seed': True, 'keyword': 'scaling', 'search_volume': 100}]


def namespace():
    ns = {'os': os, 'tempfile': tempfile, 're': re, 'json': json, 'requests': requests, 'urlparse': urlparse, 'PINGCAP_DOMAIN': 'pingcap.com', 'SEMRUSH_API_KEY': 'test'}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def valid_brief(ns):
    bodies = {name: 'Guidance.' for name in ns['_BRIEF_SECTIONS']}
    bodies['Meta Elements'] = '| Meta Title | Scaling guide |\n| Meta Description | A scaling guide. |\n| URL Structure | /blog/scaling/ |'
    bodies['Internal Links'] = 'No verified internal link candidates returned; refresh the sitemap inventory'
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
        self.assertEqual(fn(rows, None, 'blog', primary_msv=320)['source'], 'Stage 0 (DataForSEO Google Ads)')
        with self.assertRaisesRegex(ValueError, 'MSV is unavailable'):
            fn(rows[:1], None, 'blog')  # No N/A tier: a measured MSV is required.


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
        ns=namespace();text=valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '| Section (H2) | Anchor text | Target URL | Why |\n|---|---|---|---|\n| Scaling | Learn | https://example.com/invented/ | Reason |')
        errors=ns['validate_brief'](text,'blog',[{'url':'https://www.pingcap.com/real/'}],{'minimum':1800,'maximum':2500})
        self.assertIn('Unverified or duplicate internal link',errors)
    def test_truncation_rejected(self):
        ns=namespace();ns.update(ANTHROPIC_API_KEY='test',ANTHROPIC_MODEL='test',
            load_brief_examples=lambda:'',load_feedback=lambda:'',build_system_prompt=lambda *args:'prompt')
        client=Mock();client.messages.create.return_value=types.SimpleNamespace(stop_reason='max_tokens', content=[types.SimpleNamespace(type='text', text='Partial output')])
        ns['anthropic']=Mock();ns['anthropic'].Anthropic.return_value=client
        with tempfile.TemporaryDirectory() as folder:
            ns['os'] = Mock(getenv=lambda key: None, getcwd=lambda: folder, path=os.path)
            with self.assertRaisesRegex(ValueError,'incomplete'):
                ns['generate_brief']('scaling','blog',SEED,[],[],[])
            drafts = list(Path(folder).glob('brief_failed_*/draft.md'))
            self.assertEqual(len(drafts), 1)
            self.assertIn('Partial output', drafts[0].read_text())
    def test_complete_generation_and_title(self):
        ns=namespace();ns.update(ANTHROPIC_API_KEY='test',ANTHROPIC_MODEL='test',
            ANTHROPIC_HAIKU_MODEL='title',load_brief_examples=lambda:'',load_feedback=lambda:'',
            build_system_prompt=lambda *args:'prompt')
        content=valid_brief(ns)
        client=Mock();client.messages.create.return_value=types.SimpleNamespace(
            stop_reason='end_turn',content=[types.SimpleNamespace(type='text',text=content)])
        ns['anthropic']=Mock();ns['anthropic'].Anthropic.return_value=client
        self.assertEqual(ns['generate_brief']('scaling','blog',SEED,[],[],[]),content)
        prompt=client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Word Count Plan',prompt);self.assertIn('SERP Features',prompt)
        client.messages.create.return_value=types.SimpleNamespace(content=[types.SimpleNamespace(text=' A title ')])
        self.assertEqual(ns['summarize_title']('topic'),'A title')

    def test_format_variants_do_not_count_as_article_h2s(self):
        ns = namespace()
        text = valid_brief(ns).replace('## Scaling', '# Article title\nTarget: ~100–100 words\n## Scaling')
        text = text.replace('1800–2500', '1700–2400')
        text = text.replace('### Schema Markup Recommendations',
                            '## Visual Recommendations Summary\nNotes.\n### Schema Markup Recommendations')
        variant = text.replace('# Article title', '## H1: Article title')
        self.assertEqual(ns['validate_brief'](variant, 'blog', [], {'minimum':1800,'maximum':2500}), [])
        normalized = ns['normalize_brief_headings'](variant)
        self.assertIn('# Article title', normalized)
        self.assertNotIn('## Visual Recommendations Summary', normalized)
        self.assertEqual(ns['normalize_brief_headings'](normalized), normalized)

    def test_normalization_preserves_code_and_other_sections(self):
        ns = namespace()
        for fence in ['```', '~~~']:
            sample = fence + '\n## H1: example\n## Visual Recommendations Summary\n' + fence
            text = valid_brief(ns).replace('## Scaling', sample + '\n## Scaling')
            text += '\n## H1: outside outline\n'
            self.assertEqual(ns['normalize_brief_headings'](text), text)
            self.assertEqual(ns['validate_brief'](text, 'blog', [], {'minimum':1800,'maximum':2500}), [])

    def test_comparison_allocations_fit_every_tier(self):
        ns = namespace()
        for volume in [0, 20, 500, 2000, 5000]:
            plan = ns['word_count_plan']([], {'intent':{'search_volume':volume}}, 'comparison')
            self.assertEqual(sum(plan['section_budgets'].values()), plan['article_target'])
            self.assertLessEqual(plan['article_target'], plan['maximum'])
            self.assertEqual(list(plan['section_budgets'])[2], 'At a glance')
            self.assertEqual(list(plan['section_budgets'])[-1], 'Decision endcap')

    def test_single_and_range_budget_formats(self):
        ns = namespace()
        for line, expected in [('Target: ~172 words', (172,172)),
                               ('**Target: ~172 words**', (172,172)),
                               ('**Target:** ~172 words', (172,172)),
                               ('Target: 1,800–2,500 words', (1800,2500)),
                               ('Target: ~1800-2500 words', (1800,2500))]:
            self.assertEqual(ns['parse_word_budget'](line), expected)
        for line in ['Target: included in parent budget', 'Target: -172 words',
                     'Target: ~words', 'Target: 172– words']:
            self.assertIsNone(ns['parse_word_budget'](line))

    def test_single_budgets_are_counted_and_invalid_budgets_rejected(self):
        ns = namespace()
        base = valid_brief(ns)
        plan = {'minimum':1800,'maximum':2500}
        self.assertEqual(ns['validate_brief'](base.replace('1800–2500','2150'), 'blog', [], plan), [])
        for budget in ['2600', '1700', '2500–1800', '0–2150']:
            errors = ns['validate_brief'](base.replace('1800–2500',budget), 'blog', [], plan)
            self.assertIn('Top-level word budgets do not fit the selected MSV tier', errors)
        errors = ns['validate_brief'](base.replace('Target: ~1800–2500 words',''), 'blog', [], plan)
        self.assertIn('Missing word budget: Scaling', errors)

    def test_unmatched_link_reports_exact_heading(self):
        ns = namespace()
        url = 'https://www.pingcap.com/example/'
        text = valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '| Wrong heading | Learn | ' + url + ' | Reason |')
        errors = ns['validate_brief'](text, 'blog', [{'url':url}], {'minimum':1800,'maximum':2500})
        self.assertTrue(any("'Wrong heading'" in e and 'valid h2_N ID' in e for e in errors))

    def test_renamed_heading_after_repair_keeps_its_link(self):
        ns = namespace()
        url = 'https://www.pingcap.com/example/'
        text = valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '| Section (H2) | Anchor text | Target URL | Why |\n| h2_1 | Learn | ' + url + ' | Reason |')
        resolved = ns['resolve_internal_link_ids'](text)
        self.assertIn('| Scaling | Learn |', resolved)
        ids = ns['unresolve_internal_link_ids'](resolved)
        self.assertIn('| h2_1 | Learn |', ids)
        self.assertIn('| Section (H2) | Anchor text |', ids)
        repaired = ns['resolve_internal_link_ids'](ids.replace('## Scaling', '## Scaling, renamed by repair'))
        self.assertIn('| Scaling, renamed by repair | Learn |', repaired)

    def test_link_ids_render_final_headings_and_preserve_other_cells(self):
        ns = namespace()
        url = 'https://www.pingcap.com/example/'
        text = valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '| Section (H2) | Anchor text | Target URL | Why |\n| h2_1 | Learn h2_1 | ' + url + ' | Reason h2_1 |')
        text = text.replace('## Scaling', '## A newly worded heading')
        resolved = ns['resolve_internal_link_ids'](text)
        self.assertIn('| A newly worded heading | Learn h2_1 | ' + url + ' | Reason h2_1 |', resolved)
        self.assertEqual(ns['resolve_internal_link_ids'](resolved), resolved)
        self.assertEqual(ns['validate_brief'](text, 'blog', [{'url':url}], {'minimum':1800,'maximum':2500}), [])
        for fence in ['```', '~~~']:
            sample = text.replace('## A newly worded heading', '# Intro\nTarget: ~100 words\n### Subheading\n' + fence + '\n## Sample\n' + fence + '\n## A newly worded heading')
            self.assertIn('| A newly worded heading |', ns['resolve_internal_link_ids'](sample))
        for bad in ['h2_0', 'h2_99', 'h2_01', 'h2_x']:
            errors = ns['validate_brief'](text.replace('| h2_1 |', '| '+bad+' |'), 'blog', [{'url':url}], {'minimum':1800,'maximum':2500})
            self.assertTrue(any('invalid H2 placement' in e for e in errors))

    def test_generation_exports_rendered_link_heading(self):
        ns = namespace()
        ns.update(ANTHROPIC_API_KEY='test', ANTHROPIC_MODEL='test',
                  load_brief_examples=lambda:'', load_feedback=lambda:'',
                  build_system_prompt=lambda *args:'prompt')
        url = 'https://www.pingcap.com/example/'
        text = valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
                                       '| h2_1 | Learn | '+url+' | Reason |')
        client = Mock()
        client.messages.create.return_value = types.SimpleNamespace(stop_reason='end_turn',
            content=[types.SimpleNamespace(type='text', text=text)])
        ns['anthropic'] = Mock()
        ns['anthropic'].Anthropic.return_value = client
        result = ns['generate_brief']('scaling','blog',SEED,[],[],[], internal_link_candidates=[{'url':url}])
        self.assertIn('| Scaling | Learn |', result)
        self.assertNotIn('| h2_1 |', result)
        self.assertEqual(client.messages.create.call_count, 1)

    def test_link_id_placement_cap_and_duplicate_urls(self):
        ns = namespace()
        urls = ['https://www.pingcap.com/'+str(i)+'/' for i in range(3)]
        text = valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '\n'.join('| h2_1 | Learn | '+url+' | Reason |' for url in urls))
        errors = ns['validate_brief'](text, 'blog', [{'url':url} for url in urls], {'minimum':1800,'maximum':2500})
        self.assertTrue(any('more than two links' in e for e in errors))
        errors = ns['validate_brief'](text.replace(urls[1],urls[0]), 'blog', [{'url':url} for url in urls], {'minimum':1800,'maximum':2500})
        self.assertIn('Unverified or duplicate internal link', errors)

    def test_formatted_ids(self):
        ns=namespace(); url='https://www.pingcap.com/example/'
        for key in ['**h2_1**', '`h2_1`', '__h2_1__']:
            text=valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
                '| '+key+' | Learn | '+url+' | Reason |')
            self.assertEqual(ns['validate_brief'](text,'blog',[{'url':url}],{'minimum':1800,'maximum':2500}),[])
            self.assertIn('| Scaling |',ns['resolve_internal_link_ids'](text))

    def test_empty_link_table_rejected(self):
        ns=namespace()
        text=valid_brief(ns).replace('No verified internal link candidates returned; refresh the sitemap inventory',
            '| Section (H2) | Anchor text | Target URL | Why |\n|---|---|---|---|')
        self.assertIn('Internal Links table contains no verified link recommendations',
            ns['validate_brief'](text,'blog',[{'url':'https://www.pingcap.com/x/'}],{'minimum':1800,'maximum':2500}))

    def test_missing_intro_budget_rejected(self):
        ns=namespace(); text=valid_brief(ns).replace('## Scaling','# Intro\nNo allocation.\n## Scaling')
        self.assertIn('Missing word budget: H1 introduction',ns['validate_brief'](text,'blog',[],{'minimum':1800,'maximum':2500}))

    def test_consistent_fences(self):
        ns=namespace()
        for sample in ['```markdown\n## Sample\n````\n',
                       '~~~~markdown\n```\n### CTAs\n## Sample\n~~~~~\n',
                       '```markdown\n```not-a-close\n## Sample\n```\n']:
            text=valid_brief(ns).replace('## Scaling',sample+'## Scaling')
            self.assertEqual(ns['validate_brief'](text,'blog',[],{'minimum':1800,'maximum':2500}),[])
            self.assertEqual(ns['normalize_brief_headings'](text),text)
            self.assertEqual(len(ns['brief_sections'](text)),len(ns['_BRIEF_SECTIONS']))

    def test_env_template_names_and_model(self):
        env=SOURCE.with_name('.env.example').read_text()
        self.assertNotIn('\\_',env)
        self.assertIn('ANTHROPIC_MODEL=claude-sonnet-4-6',env)
        self.assertIn('DRIVE_FOLDER_ID=',env)


    def test_serp_heading_variants_and_table(self):
        ns = namespace()
        table = '| # | Page | Key Angle |\n|---|---|---|\n| 1 | Example | Specific angle |\n'
        for title in ['### Block 1 — Top Ranking Pages', '**Block 1 — Top ranking pages table**',
                      '### SERP Competitor Table', '**### Block 1 — Top Ranking Pages**']:
            preamble = title + '\n' + table + '\n### Block 2 — Patterns Favored by AI Overviews & LLMs\n- Observed pattern.\n'
            self.assertEqual(ns['validate_serp_blocks'](preamble + '# Article\n'), [])
        text = valid_brief(ns).replace('**Block 1 — Top ranking pages table**\nNo SERP data returned',
                                     '### Block 1 — Top Ranking Pages\n' + table)
        self.assertEqual(ns['validate_brief'](text, 'blog', [], {'minimum':1800,'maximum':2500}), [])

    def test_serp_blocks_require_structure_and_content(self):
        ns = namespace()
        base = ('### Top Ranking Pages\nNo SERP data returned — manual review recommended\n'
                '### Patterns Favored by AI Overviews & LLMs\nInsufficient SERP data\n')
        self.assertEqual(ns['validate_serp_blocks'](base), [])
        for invalid in [base.replace('No SERP data returned — manual review recommended',''),
                        base.replace('Insufficient SERP data',''),
                        base.replace('### Top Ranking Pages','### Unrelated'),
                        '# Article\n' + base,
                        base + base,
                        base.replace('No SERP data returned — manual review recommended',
                                     '| # | Page | Key Angle |\n|---|---|---|')]:
            self.assertTrue(ns['validate_serp_blocks'](invalid))

    def test_prompt_conflicts_removed(self):
        import brief_quality
        base, checklist = brief_quality.system_prompt_parts('listicle', {'url':'https://www.pingcap.com/ai/','anchor':'AI'})
        prompt = base + checklist
        self.assertNotIn('2,000–5,000',prompt)
        self.assertNotIn('diagram suggestion for each H3',prompt)
        self.assertNotIn('/article/[slug]/ for other types',prompt)
        self.assertIn('12 for listicles',prompt)
        self.assertNotIn('3–5 sentences', prompt)

if __name__=='__main__':unittest.main()




