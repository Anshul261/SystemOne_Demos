import pytest

from benchmarks.run_repeatability import mean_interval, summarize


def test_mean_interval_uses_rounds_and_handles_no_variation():
    interval = mean_interval([7, 7, 7, 7, 7])
    assert interval['ci95_low'] == interval['ci95_high'] == 7
    varied = mean_interval([1, 2, 3, 4, 5])
    assert varied['mean'] == 3
    assert varied['ci95_low'] == pytest.approx(1.03675684, abs=1e-7)
    assert varied['ci95_high'] == pytest.approx(4.96324316, abs=1e-7)
    assert mean_interval([1, 2, 3, 4]) is None


def test_pooled_percentiles_and_throughput_are_not_averaged_round_statistics():
    rounds = []
    for index in range(1, 6):
        rounds.append({
            'round': index, 'measured_at': 'start', 'completed_at': 'end',
            'micro': {'samples_ms': [index*10, index*10+1],
                      'wall_seconds': index, 'latency': {'p50_ms': index*10+0.5, 'p95_ms': index*10+0.95}},
            'browser_runs': [{'demo': 'rpa', 'success': True,
                              'end_to_end_seconds': index+1, 'model_calls': 1,
                              'model_total_ms': 10, 'model_latency': {'p50_ms': 10}}],
        })
    result = summarize(rounds, {'system': {}, 'model': {}})
    assert result['micro']['latency']['p95_ms'] == 50.55
    assert result['micro']['requests_per_second'] == 0.67
    assert result['browser_summary']['rpa']['runs'] == 5
    assert result['browser_summary']['rpa']['round_mean_ci95_seconds']['mean'] == 4


def test_failed_round_does_not_receive_a_five_round_confidence_interval():
    rounds = []
    for index in range(1, 6):
        rounds.append({
            'round': index, 'measured_at': 'start', 'completed_at': 'end',
            'micro': {'samples_ms': [10], 'wall_seconds': 1,
                      'latency': {'p50_ms': 10, 'p95_ms': 10}},
            'browser_runs': [{'demo': 'rpa', 'success': index != 5,
                              'end_to_end_seconds': index, 'model_calls': 1,
                              'model_total_ms': 10, 'model_latency': {'p50_ms': 10}}],
        })
    stats = summarize(rounds, {'system': {}, 'model': {}})['browser_summary']['rpa']
    assert stats['passed'] == 4 and stats['runs'] == 5
    assert stats['round_mean_ci95_seconds'] is None
