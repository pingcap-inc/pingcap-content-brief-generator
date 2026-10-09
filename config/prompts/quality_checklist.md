## Pre-Output Quality Checklist

Before outputting the brief, verify every item below. Rewrite any section that
fails a check before proceeding. The generator re-checks most items in code.

1.  The whole brief is at most {{brief_length.max_words}} words (fenced code excluded), and no
    section exceeds its cap. Bullets, not paragraphs; no rule restated twice.
2.  Every H2 has a one-sentence Rationale (at most 20 words) and 2 to 3 guidance
    bullets (each at most 20 words); each H2 block is about {{brief_length.section_target_words}} words.
3.  H3s are heading lines only, except FAQ answer bullets and template-required tables.
4.  The brief contains NO standalone sections titled "Key Points to Cover",
    "Proof Points", "Data to Find", "Examples", "Visuals to Add",
    "Competitor Analysis", "Search Intent Analysis", or "PingCAP/TiDB Angle".
5.  Internal links are a four-column table (Section (H2), Anchor text, Target URL,
    Why) with at most {{internal_links.max_links}} unique supplied URLs, valid h2_N IDs, no more than two
    links per H2, and the priority link present. Each link sits in an H2 whose
    topic it supports.
6.  Meta Title is {{meta.title_min_chars}} to {{meta.title_max_chars}} characters, contains the primary keyword verbatim,
    ends with "{{meta.title_suffix}}", and contains no year.
7.  Meta Description is at most {{meta.description_max_chars}} characters and contains the primary keyword
    or a close variant.
8.  Search Intent is one of Commercial Investigation, Informational, Navigational,
    or Transactional, matching the confirmed search_intent.
9.  Entity Recognition Focus lists 10 to 15 specific named entities, each once.
10. Relevant LLM Queries are realistic, specific user prompts.
11. The outline opens with (1) a SERP table of {{serp.table_min_rows}} to {{serp.table_max_rows}} relevant pages only, with no
    mention of excluded pages, and (2) a "Patterns Favored by AI Overviews & LLMs"
    block based on the primary keyword's SERP.
12. Directly under the H1: a {{key_takeaways.label}} block of {{key_takeaways.min_bullets}} to {{key_takeaways.max_bullets}} bullets, each
    {{key_takeaways.min_words}} to {{key_takeaways.max_words}} words, then intro guidance with an author bio and credentials, an
    expert review note, and a methodology note about verifying ratings, pricing,
    and feature availability.
13. Every page-type template H2 is present in the template's order.
14. The FAQ H2 has {{faq.min_questions}} to {{faq.max_questions}} questions sourced from the Relevant LLM Queries and
    PAA; each answer guidance is 1 to {{faq.max_bullets}} bullets with no preamble.
15. Exactly one primary CTA, with the supplied anchor and URL verbatim, in the
    template's closing H2. No "verify URL" placeholders.
16. Every case study or customer proof point uses a customer from the roster with
    its roster URL. Anonymized examples are a failure; remove the slot instead.
    Customer-specific numbers match the case study or are marked "{{case_studies.verify_marker}}".
17. Every competitor pricing or feature claim has a source URL and the marker
    "{{claims.verify_marker}}". No invented review ratings.
18. SQL labeled TiDB or MySQL never uses <=> for vector distance.
19. Heading hierarchy is strictly H1 -> H2 -> H3 with no skips, and the
    outline has at most 10 H2s (12 for listicles and blogs).
20. Every H2 includes a "Target: ~X–Y words" line, no Visual line, and top-level
    targets sum to within the Word Count Plan tier.
21. The brief contains a standalone Target Audience section between Page Goal and
    Technical Notes, and a Writer Guardrails section with all six items.
22. No em dash characters, no banned words, and no "best" or "superior" claims
    about TiDB anywhere in the brief.

{{template_checklist}}
