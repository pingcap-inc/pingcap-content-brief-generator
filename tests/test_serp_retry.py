import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from keyword_resolver import Cache, ProviderError, ResearchAPI, load_config


def pages(count):
    return [{'index':i, 'relevance':0.8, 'page_type':'comparison',
             'different_brand':False} for i in range(count)]


def reply(value, stop='end_turn'):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type='text', text=json.dumps(value))])


class SERPRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = Mock()
        self.api = ResearchAPI(load_config(), Cache(self.directory.name), 'login', 'password',
                               self.client, 'test-model', session=Mock())
        self.results = [{'rank':i+1, 'url':f'https://example.com/{i}',
                         'title':'Result', 'description':'Snippet'} for i in range(19)]

    def run_relevance(self):
        with redirect_stdout(io.StringIO()):
            return self.api.relevance('TiDB vs. MariaDB', 'mariadb alternatives', self.results)

    def test_20_judgments_for_19_results_retry_and_cache_only_valid(self):
        self.client.messages.create.side_effect = [reply({'pages':pages(20)}), reply({'pages':pages(19)})]
        self.assertEqual(len(self.run_relevance()),19)
        self.assertEqual(self.client.messages.create.call_count,2)
        saved = list(Path(self.directory.name).glob('*.json'))
        self.assertEqual(len(saved),1)
        self.assertEqual(len(json.loads(saved[0].read_text())['value']['pages']),19)
        request = self.client.messages.create.call_args_list[0].kwargs
        self.assertIn('exactly 19',request['system'])
        data = json.loads(request['messages'][0]['content'])
        self.assertEqual([r['index'] for r in data['results']],list(range(19)))

    def test_repeated_wrong_count_stops_after_two_calls(self):
        self.client.messages.create.return_value = reply({'pages':pages(20)})
        with self.assertRaisesRegex(ProviderError,'20 pages; expected 19'):
            self.run_relevance()
        self.assertEqual(self.client.messages.create.call_count,2)
        self.assertEqual(list(Path(self.directory.name).glob('*.json')),[])

    def test_duplicate_indices_retry(self):
        bad = pages(19)
        bad[-1]['index'] = 0
        self.client.messages.create.side_effect = [reply({'pages':bad}),reply({'pages':pages(19)})]
        self.assertEqual(len(self.run_relevance()),19)

    def test_mixed_index_types_retry(self):
        bad = pages(19)
        bad[-1]['index'] = '18'
        self.client.messages.create.side_effect = [reply({'pages':bad}),reply({'pages':pages(19)})]
        self.assertEqual(len(self.run_relevance()),19)

    def test_valid_response_is_sorted_and_reused(self):
        self.client.messages.create.return_value = reply({'pages':list(reversed(pages(19)))})
        self.assertEqual([p['index'] for p in self.run_relevance()],list(range(19)))
        self.assertEqual(len(self.run_relevance()),19)
        self.assertEqual(self.client.messages.create.call_count,1)

    def test_empty_results_do_not_call_llm(self):
        self.results = []
        self.assertEqual(self.run_relevance(),[])
        self.client.messages.create.assert_not_called()

    def test_provider_failure_is_not_retried(self):
        self.client.messages.create.side_effect = RuntimeError('service unavailable')
        with self.assertRaises(ProviderError):
            self.run_relevance()
        self.assertEqual(self.client.messages.create.call_count,1)
