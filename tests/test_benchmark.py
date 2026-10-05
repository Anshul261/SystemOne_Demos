from benchmark import latency_stats, percentile, summarize_browser_runs


def test_percentile_interpolates_sorted_values():
    assert percentile([40, 10, 20, 30], 50) == 25
    assert percentile([10, 20], 95) == 19.5


def test_latency_stats():
    stats = latency_stats([10, 20, 30])
    assert stats["mean_ms"] == 20
    assert stats["p50_ms"] == 20


def test_browser_summary_counts_success_and_cache_hits():
    summary = summarize_browser_runs(
        [
            {
                "demo": "test",
                "success": True,
                "end_to_end_seconds": 2.0,
                "model_calls": 3,
                "model_total_ms": 30,
                "model_latency": {"p50_ms": 10},
                "field_cache_hits": 7,
                "site_timer": "00:02",
            },
            {
                "demo": "test",
                "success": False,
                "end_to_end_seconds": 1.0,
                "model_calls": 1,
                "model_total_ms": 10,
                "model_latency": {"p50_ms": 10},
                "error": "failed",
            },
        ]
    )["test"]
    assert summary["passed"] == 1
    assert summary["success_rate"] == 0.5
    assert summary["field_cache_hits_p50"] == 7
