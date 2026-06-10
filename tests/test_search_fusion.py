from glitch_brain_mcp.embeddings import vec_literal
from glitch_brain_mcp.memory import _rrf_fuse


def test_rrf_prefers_items_ranked_in_both_lists():
    fused = _rrf_fuse([[1, 2, 3], [2, 4]])
    # 2 appears in both lists -> highest fused score
    assert max(fused, key=fused.get) == 2
    # every listed id gets a score
    assert set(fused) == {1, 2, 3, 4}


def test_rrf_scores_follow_rank_order_within_a_single_list():
    fused = _rrf_fuse([[7, 8, 9]])
    assert fused[7] > fused[8] > fused[9]


def test_rrf_empty():
    assert _rrf_fuse([]) == {}
    assert _rrf_fuse([[], []]) == {}


def test_vec_literal_shape():
    lit = vec_literal([0.25, -1.0, 3.5e-05])
    assert lit.startswith("[") and lit.endswith("]")
    assert len(lit.split(",")) == 3
