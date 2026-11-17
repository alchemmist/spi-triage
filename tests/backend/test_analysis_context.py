"""Offline model-only projection: originals/references/budgets never rewritten."""
import copy
import unittest
from unittest.mock import Mock
from analysis_context import project_metric_series
from core import digest, Failure
from integrations import Model
import test_triage as fixture


def context():
    return {'alert': fixture.event(), 'preliminary_match': {'candidates': [{'number': 16, 'body': 'Existing report.'}]},
            'evidence': {'queries': [{'source': 'prometheus_metrics', 'path': '/api/datasources/proxy/uid/prometheus/api/v1/query_range',
             'parameters': {'query': 'fixture_metric'}, 'result': {'format': 'observed_metric_summary_v1',
              'series': [{'labels': {'id': 'unit-'+str(i), 'node': 'fixture-node'}, 'first': {'time': 1, 'value': i},
                          'last': {'time': 2, 'value': i}, 'minimum': i, 'maximum': i, 'sample_count': 2} for i in range(100)],
              'truncated_series': 7, 'limitation': 'No matching series or zero values do not establish service health.'}}],
             'artifact_context': {'index': [{'id': 'E001', 'path': 'metrics.json'}, {'id': 'E002', 'path': 'logs.json'}]},
             'missing': ['No trace collected.']}}


class AnalysisContextProjectionTests(unittest.TestCase):
    def test_complete_snapshot_and_all_references_untouched(self):
        data = context(); original = copy.deepcopy(data); out = project_metric_series(data, 16)
        self.assertEqual(data, original)
        self.assertEqual(out['preliminary_match'], original['preliminary_match'])
        self.assertEqual(out['evidence']['artifact_context'], original['evidence']['artifact_context'])
        self.assertEqual(out['evidence']['missing'], original['evidence']['missing'])
        query = out['evidence']['queries'][0]; before = original['evidence']['queries'][0]
        self.assertEqual(query['path'], before['path']); self.assertEqual(query['parameters'], before['parameters'])
        result = query['result']; meta = result['model_series_projection']
        self.assertEqual(result['series'], before['result']['series'][:16])
        self.assertEqual((meta['original_series_count'], meta['selected_series_count'], meta['omitted_series_count']), (100, 16, 84))
        self.assertEqual(meta['original_series_sha256'], digest(before['result']['series']))
        self.assertEqual(result['truncated_series'], 7)
        self.assertIn('not reviewed', meta['limitation']); self.assertIn('not relevance ranking', meta['selection'])

    def test_unknown_result_and_source_not_projected(self):
        for result in [[], None, {}, {'format': 'other', 'series': list(range(100))}]:
            data = context(); data['evidence']['queries'][0]['result'] = result
            self.assertIs(project_metric_series(data, 1), data)
        data = context(); data['evidence']['queries'][0]['source'] = 'loki_logs'
        self.assertIs(project_metric_series(data, 1), data)

    def test_no_bundle_and_small_query_no_change(self):
        data = context(); del data['evidence']['artifact_context']; self.assertIs(project_metric_series(data, 1), data)
        data = context(); data['evidence']['queries'][0]['result']['series'] = []
        self.assertIs(project_metric_series(data, 1), data)
        for limit in [0, 17, True, 1.5]:
            with self.assertRaises(ValueError): project_metric_series(context(), limit)

    def test_adaptive_projection_uses_complete_budget_and_calls_model_only_once(self):
        data = context(); model = Model(Mock(), 'unused'); captured = []
        for row in data['evidence']['queries'][0]['result']['series']:
            # Small scalar fields/cardinality, not one huge excerptable string.
            row['labels'].update({f'label_{i}': 'fixture-value-'+str(i)*4 for i in range(15)})
        evidence = copy.deepcopy(data['evidence']); artifact = evidence.pop('artifact_context')
        evidence['bundle'] = {'model_context': artifact}
        original = copy.deepcopy((data['alert'], data['preliminary_match'], evidence))
        class Captured(Exception): pass
        def call(task, projected, schema, deadline):
            captured.append(projected)
            self.assertLessEqual(model.budget_reservation(model.payload(task, projected, schema)), 30000)
            self.assertIn('model_series_projection', projected['evidence']['queries'][0]['result'])
            raise Captured()
        model.call = call
        with self.assertRaises(Captured): model.analyze(data['alert'], data['preliminary_match'], evidence)
        self.assertEqual(len(captured), 1); self.assertEqual(original, (data['alert'], data['preliminary_match'], evidence))
        model.http.request.assert_not_called()

    def test_irreducible_protected_metadata_stays_fatal_without_paid_request(self):
        data = context(); data['alert']['path'] = ['x'*120000]*5
        evidence = data['evidence']; artifact = evidence.pop('artifact_context'); evidence['bundle'] = {'model_context': artifact}
        model = Model(Mock(), 'unused')
        with self.assertRaises(Failure) as caught: model.analyze(data['alert'], data['preliminary_match'], evidence)
        self.assertEqual(caught.exception.code, 'LLM_CONTEXT_METADATA_TOO_LARGE'); model.http.request.assert_not_called()
