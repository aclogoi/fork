from fork.compare import results_equal, summarize


def test_multiset_unordered():
    assert results_equal([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])
    assert not results_equal([(1, "a"), (2, "b")], [(2, "b"), (1, "a")], ordered=True)


def test_float_tolerance_and_numeric_strings():
    assert results_equal([(1.0000000001,)], [(1,)])
    assert results_equal([("3.0",)], [(3,)])
    assert not results_equal([(1.01,)], [(1,)])


def test_none_and_length():
    assert not results_equal([(1,)], [(1,), (1,)])
    assert results_equal(None, None)
    assert not results_equal(None, [])


def test_summarize():
    assert summarize([(12,)], ["n"]) == "12"
    assert summarize([], ["n"]) == "no rows"
