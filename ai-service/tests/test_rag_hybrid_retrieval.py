"""固定混合检索算法的合成验证，不读取实验事实标签。"""

import math

import pytest

from ai_service.orchestration.rag_hybrid_retrieval import bm25_top, rrf_top, tokenize


def test_tokenize_normalizes_and_keeps_segment_boundaries():
    assert tokenize("退款流程，退 款 AＢＣ_１２ Foo42") == [
        "退款", "款流", "流程", "退", "款", "abc_12", "foo42"
    ]
    assert tokenize("中文ABC规则") == ["中文", "abc", "规则"]
    assert tokenize("! \t🙂") == []
    assert tokenize("") == []


def test_tokenize_retains_repeated_tokens_and_supplementary_han():
    assert tokenize("哈哈哈 echo ECHO") == ["哈哈", "哈哈", "echo", "echo"]
    assert tokenize("𠀀中") == ["𠀀中"]


def test_bm25_matches_manual_formula_with_length_normalization():
    chunks = [{"content": "apple apple pear"}, {"content": "apple"}, {"content": "pear"}]
    result = dict(bm25_top(chunks, "APPLE apple"))
    idf = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    expected_first = idf * (2 * 2.2) / (2 + 1.2 * (0.25 + 0.75 * 3 / (5 / 3)))
    expected_second = idf * 2.2 / (1 + 1.2 * (0.25 + 0.75 / (5 / 3)))
    assert result == pytest.approx({0: expected_first, 1: expected_second})
    assert bm25_top(chunks, "apple apple") == bm25_top(chunks, "apple")


def test_bm25_empty_documents_count_in_corpus_statistics():
    result = bm25_top([{"content": "x"}, {"content": ""}], "x")
    expected = math.log(2) * 2.2 / (1 + 1.2 * (0.25 + 0.75 * 2))
    assert result[0][0] == 0
    assert result[0][1] == pytest.approx(expected)


@pytest.mark.parametrize("chunks,question", [([], "x"), ([{"content": ""}], "x"),
    ([{"content": "x"}], ""), ([{"content": "x"}], "unknown")])
def test_bm25_empty_or_unmatched_returns_no_candidates(chunks, question):
    assert bm25_top(chunks, question) == []


def test_bm25_stable_ties_and_limit():
    chunks = [{"content": "同分"} for _ in range(10)]
    assert [index for index, _ in bm25_top(chunks, "同分")] == list(range(8))
    assert bm25_top(chunks, "同分", limit=0) == []


def test_rrf_manual_overlap_scores_and_original_index_tie_order():
    result = rrf_top([3, 1], [1, 3])
    assert [index for index, _ in result] == [1, 3]
    assert [score for _, score in result] == pytest.approx([1 / 61 + 1 / 62] * 2)


def test_rrf_single_route_fallback_and_no_routes():
    expected = [(4, 1 / 61), (2, 1 / 62)]
    assert rrf_top([4, 2], []) == expected
    assert rrf_top([], [4, 2]) == expected
    assert rrf_top([], []) == []
    assert rrf_top([0], [0], limit=0) == []


def test_rrf_fusion_truncates_union_to_eight():
    result = rrf_top(list(range(8)), list(range(8, 16)))
    assert [index for index, _ in result] == [0, 8, 1, 9, 2, 10, 3, 11]
    assert len(result) == 8


@pytest.mark.parametrize("indices", [[1, 1], [-1], [True], [1.5], ["1"], [None]])
def test_rrf_rejects_duplicate_or_invalid_indices(indices):
    with pytest.raises(ValueError):
        rrf_top(indices, [])
    with pytest.raises(ValueError):
        rrf_top([], indices)


@pytest.mark.parametrize("limit", [-1, True, 1.5, "8", None])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        bm25_top([], "", limit=limit)
    with pytest.raises(ValueError):
        rrf_top([], [], limit=limit)


@pytest.mark.parametrize("chunks", [[{}], [{"content": None}], ["x"], None, ()])
def test_bm25_rejects_malformed_chunks(chunks):
    with pytest.raises(ValueError):
        bm25_top(chunks, "x")


def test_invalid_text_and_rank_containers_are_rejected():
    with pytest.raises(ValueError):
        tokenize(None)
    with pytest.raises(ValueError):
        bm25_top([], None)
    with pytest.raises(ValueError):
        rrf_top((1, 2), [])
    with pytest.raises(ValueError):
        rrf_top([], None)


def test_ranking_does_not_mutate_inputs_or_read_other_chunk_fields():
    chunks = [{"content": "same", "rubric": object()}, {"content": "same"}]
    dense, sparse = [1, 0], [0]
    assert [index for index, _ in bm25_top(chunks, "same")] == [0, 1]
    rrf_top(dense, sparse)
    assert dense == [1, 0] and sparse == [0]
    assert chunks[0]["content"] == "same" and chunks[1] == {"content": "same"}
