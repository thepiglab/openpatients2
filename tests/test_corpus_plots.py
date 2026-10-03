import pytest

from openpatients2.corpus_plots import histogram


def test_histograms_preserve_counts_and_expose_log_edges_and_percentiles():
    values=[100,200,200,400,1000,5000,10000]
    linear=histogram(values); log=histogram(values,logarithmic=True)
    assert sum(linear['counts'])==sum(log['counts'])==len(values)
    assert linear['markers']==log['markers']
    assert linear['markers']['median']==400
    assert log['edges'][0]==pytest.approx(100) and log['edges'][-1]==pytest.approx(10000)
    assert all(b>a for a,b in zip(log['edges'],log['edges'][1:]))


def test_empty_or_invalid_histogram_is_not_a_zero_distribution():
    for values in ([],[-1],[float('nan')]):
        with pytest.raises(ValueError): histogram(values)
    with pytest.raises(ValueError): histogram([0,1],logarithmic=True)
    assert sum(histogram([42]*10)['counts'])==10
