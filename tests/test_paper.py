import unittest

from research_swarm.paper import new_paper, sync_paper, edit_section, paper_view, export_paper, validate_paper_output


def snapshot(output=None, version=1):
    return {'project': {'round': 1}, 'nodes': [{'id': 'central', 'title': '研究主编', 'version': version, 'active': True,
        'kind': 'research', 'status': 'completed', 'input': {}, 'output': output}],
        'evidence': [{'id': 'e1', 'paperId': 'p1', 'type': 'full_text', 'locator': 'p. 3', 'quote': 'measured result'}],
        'papers': [{'id': 'p1', 'title': 'Real reference', 'authors': ['A'], 'year': 2025, 'doi': '10.123/a'}],
        'report': {'claims': [], 'unresolved': []}, 'history': []}


class PaperTests(unittest.TestCase):
    def test_model_updates_do_not_replace_a_user_section(self):
        paper = new_paper()
        edit_section(paper, 'method', {'markdown': '我的方法', 'expectedRevision': 0})
        output = {'structured': {'paperSections': [{'id': 'method', 'markdown': '模型的补充', 'evidenceIds': ['e1']}]}}
        self.assertTrue(sync_paper(paper, snapshot(output)))
        method = next(s for s in paper['sections'] if s['id'] == 'method')
        self.assertEqual(method['markdown'], '我的方法')
        self.assertEqual(method['suggestion']['markdown'], '模型的补充')
        self.assertEqual(method['revision'], 1)
        self.assertFalse(sync_paper(paper, snapshot(output)))
        with self.assertRaisesRegex(ValueError, '版本'):
            edit_section(paper, 'method', {'markdown': '旧的覆盖', 'expectedRevision': 0})

    def test_unknown_section_and_fabricated_citations_are_rejected(self):
        for section in ({'id': 'method', 'markdown': 'X', 'evidenceIds': ['fake']},
                        {'id': 'unknown', 'markdown': 'X', 'evidenceIds': []}):
            with self.assertRaises(ValueError):
                validate_paper_output({'paperSections': [section]}, {'e1'})

    def test_execution_receipt_is_not_scientific_success(self):
        state = snapshot()
        state['nodes'].append({'id': 'exp', 'active': True, 'version': 1, 'kind': 'experiment', 'title': '消融',
            'status': 'completed', 'input': {'experimentDesign': {'hypothesis': 'H', 'metrics': ['accuracy']}},
            'output': {'summary': '只有设计', 'claims': [], 'structured': {'status': 'executed'}}})
        view = paper_view(new_paper(), state)
        self.assertEqual(view['experiments'][0]['executionStatus'], 'unexecuted')
        state['history'] = [{'type': 'tool-executed', 'valid': True, 'nodeId': 'exp', 'version': 1,
            'execution': {'tool': 'python_run', 'status': 'completed', 'nodeId': 'exp', 'nodeVersion': 1, 'round': 1, 'receipt': 'runs/test/receipt.json'}}]
        self.assertEqual(paper_view(new_paper(), state)['experiments'][0]['executionStatus'], 'executed')
        state['history'][0]['valid'] = False
        self.assertEqual(paper_view(new_paper(), state)['experiments'][0]['executionStatus'], 'unexecuted')

    def test_export_marks_missing_sections_and_includes_only_real_references(self):
        paper = new_paper()
        edit_section(paper, 'method', {'markdown': '改进 A & B_1，待检验。', 'expectedRevision': 0})
        files = export_paper(paper, snapshot(), '我的课题')
        self.assertIn('待撰写', files['paper/manuscript.md'])
        self.assertIn('B\\_1', files['paper/manuscript.tex'])
        self.assertIn('Real reference', files['paper/references.bib'])
        self.assertIn('证据', files['paper/manuscript.md'])

    def test_changed_node_version_marks_previous_section_stale(self):
        paper = new_paper()
        sync_paper(paper, snapshot({'structured': {'paperSections': [{'id': 'results', 'markdown': '旧结果', 'evidenceIds': ['e1']}]}}))
        state = snapshot(None, version=2)
        view = paper_view(paper, state)
        self.assertTrue(next(s for s in view['sections'] if s['id'] == 'results')['stale'])

    def test_source_invalidation_revokes_approval_even_before_new_output(self):
        paper = new_paper()
        state = snapshot({'structured': {'paperSections': [{'id': 'results', 'markdown': '有局限的结果', 'evidenceIds': ['e1']}]}})
        sync_paper(paper, state)
        paper['approvedRevision'] = paper['revision']
        self.assertTrue(paper_view(paper, state)['approved'])
        state['project']['round'] = 2
        state['nodes'][0].update(output=None, version=2)
        self.assertFalse(paper_view(paper, state)['approved'])
        revision = paper['revision']
        self.assertTrue(sync_paper(paper, state))
        self.assertGreater(paper['revision'], revision)
        self.assertIsNone(paper['approvedRevision'])
        self.assertFalse(sync_paper(paper, state))

    def test_accepting_suggestion_keeps_its_source_round(self):
        paper = new_paper()
        edit_section(paper, 'method', {'markdown': '用户方法', 'expectedRevision': 0})
        state = snapshot({'structured': {'paperSections': [{'id': 'method', 'markdown': '第三轮建议', 'evidenceIds': ['e1']}]}})
        state['project']['round'] = 3
        sync_paper(paper, state)
        section = paper['sections'][3]
        edit_section(paper, 'method', {'markdown': '', 'expectedRevision': section['revision'],
            'acceptSuggestion': True, 'suggestionId': section['suggestion']['id']})
        self.assertEqual(section['round'], 3)
        self.assertFalse(paper_view(paper, state)['sections'][3]['stale'])


if __name__ == '__main__':
    unittest.main()
