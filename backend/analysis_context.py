"""Model-only metric projection; immutable collection/artifact data is untouched."""
from core import digest

METRIC_SOURCES = frozenset(('prometheus_metrics', 'loki_error_frequency',
                            'host_log_error_frequency', 'loki_rule_metric'))


def project_metric_series(data, limit):
    """Bound high-cardinality summaries with explicit, deterministic omissions.

    This is NOT metric aggregation or relevance ranking. Never truncate artifact
    indexes, candidate identities, query paths/parameters or stored evidence.
    Only bundled analysis contexts can use this escape hatch. Legacy evidence
    without immutable-artifact references keeps its existing fail-closed path.
    """
    if type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError('Invalid model metric series projection limit')
    evidence = data.get('evidence', {})
    context = evidence.get('artifact_context')
    if not isinstance(context, dict) or not context.get('index'):
        return data
    queries = []
    changed = False
    for query in evidence.get('queries', []):
        result = query.get('result', {})
        rows = result.get('series') if isinstance(result, dict) else None
        if (not isinstance(result, dict) or query.get('source') not in METRIC_SOURCES or
            result.get('format') != 'observed_metric_summary_v1' or
            not isinstance(rows, list) or len(rows) <= limit):
            queries.append(query)
            continue
        changed = True
        projected = {
            **result,
            'series': rows[:limit],
            'model_series_projection': {
                'format': 'explicit_metric_series_prefix_v1',
                'original_series_count': len(rows),
                'selected_series_count': limit,
                'omitted_series_count': len(rows) - limit,
                'original_series_sha256': digest(rows),
                'selection': 'Collected order prefix, not relevance ranking or an aggregate.',
                'limitation': ('Only selected series were supplied to the model. Omitted series were not reviewed. '
                               'The complete collected summary remains in the durable job snapshot. Consult '
                               'immutable evidence for upstream JSON and its original collection limits.'),
            },
        }
        queries.append({**query, 'result': projected})
    if not changed:
        return data
    return {**data, 'evidence': {**evidence, 'queries': queries}}
