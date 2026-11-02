# stdlib-only
from app.core.metrics import BUCKETS, MAX_SERIES, RequestMetrics, gauge, labels


def test_histogram_counts_buckets_sum_and_count():
    m = RequestMetrics()
    m.observe("get", "/api/v1/projects", 200, 0.004)
    m.observe("GET", "/api/v1/projects", 201, 0.3)
    row = m.snapshot()[("GET", "/api/v1/projects", "2xx")]
    assert row[BUCKETS.index(0.005)] == 1 and row[BUCKETS.index(0.5)] == 2
    assert row[-2] == 2 and abs(row[-1] - 0.304) < 1e-9
    text = "\n".join(m.render())
    assert 'le="+Inf"} 2' in text and "srs_http_request_duration_seconds_count" in text


def test_label_values_are_escaped():
    assert labels(route='a"b\\c\nd') == '{route="a\\"b\\\\c\\nd"}'


def test_cardinality_is_capped():
    m = RequestMetrics()
    for i in range(MAX_SERIES + 50):
        m.observe("GET", f"/r{i}", 200, 0.01)
    snap = m.snapshot()
    assert len(snap) <= MAX_SERIES + 1 and ("GET", "other", "2xx") in snap


def test_gauge_renders_help_type_and_rows():
    out = gauge("srs_x", "help text", [({"type": "a"}, 1), ({"type": "b"}, 2.5)])
    assert out[:2] == ["# HELP srs_x help text", "# TYPE srs_x gauge"] and 'srs_x{type="b"} 2.5' in out
