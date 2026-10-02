import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.evaluate_concepts import decision, evaluate


class ConceptEvaluationTests(unittest.TestCase):
    def test_offline_cases_are_not_reported_as_model_successes(self):
        cases = json.loads((Path(__file__).parents[1]/'benchmarks/concept-cases.json').read_text(encoding='utf-8'))
        result = evaluate(cases)
        self.assertEqual(result['caseCount'], 16)
        self.assertEqual(result['evaluatedCount'], 0)
        self.assertIsNone(result['falseSearchCount'])
        self.assertTrue(all(row['status'] == 'not_run' and row['passed'] is None for row in result['cases']))

    def test_errors_false_searches_and_guessed_ambiguity_are_separate(self):
        cases = [{'id': 'known', 'category': 'known', 'input': 'RAG', 'expected': ['skip']},
                 {'id': 'ambiguous', 'category': 'ambiguous', 'input': 'ABC', 'expected': ['lookup', 'clarify'], 'terms': ['ABC']},
                 {'id': 'error', 'category': 'unknown', 'input': 'JEV', 'expected': ['lookup']}]
        settings = Mock()
        settings.chat.side_effect = [json.dumps({'action': 'resolve_concepts', 'concepts': [
            {'term': 'RAG', 'domain': 'AI', 'reason': 'unknown', 'core': True}]}), json.dumps({'action': 'draft'}), RuntimeError('offline')]
        settings.safe_error.return_value = 'offline'
        result = evaluate(cases, settings)
        self.assertEqual(result['falseSearchCount'], 1)
        self.assertEqual(result['ambiguousGuessedCount'], 1)
        self.assertEqual(result['errorCount'], 1)
        self.assertEqual(result['tavilyRequests'], 0)

    def test_wrong_acronym_is_not_counted_as_correct_lookup(self):
        case = {'input': 'JEV 相比 LLM 有什么优势', 'terms': ['JEV']}
        self.assertEqual(decision({'action': 'resolve_concepts', 'concepts': [
            {'term': 'LLM', 'domain': 'AI', 'reason': 'unknown', 'core': True}]}, case), 'wrong_term')
