"""MaxSim alignment and masking without model or API calls."""

from threading import Lock
from types import SimpleNamespace

import numpy as np
import torch

from experimental.u_10o0o.embedding_ablation.kure_v2 import maxsim
from experimental.u_10o0o.table_rag.kure_v2 import KureV2Retriever


def test_maxsim_excludes_padded_document_tokens():
    q = torch.tensor([[[1.0, 0.0], [0.0, 1.0]]])
    docs = torch.tensor([[[1.0, 0.0], [100.0, 100.0]], [[0.5, 0.5], [0.5, 0.5]]])
    valid = torch.tensor([[True, False], [True, True]])
    np.testing.assert_allclose(maxsim(q, docs, valid).numpy(), [[1.0, 1.0]])


def test_combined_dense_scores_keep_original_and_table_row_identity():
    r = object.__new__(KureV2Retriever)
    r.units = [{"id": "page0"}, {"id": "page1"}, {"id": "table0"}, {"id": "table1"}]
    r.embedding_lock = Lock()
    r.index = SimpleNamespace(score=lambda q: torch.tensor([[0.2, 0.7, 0.7, 0.1]]))
    result = r._dense_rank(torch.ones((64, 128)))
    assert [p["id"] for p, _ in result] == ["page1", "table0", "page0", "table1"]
