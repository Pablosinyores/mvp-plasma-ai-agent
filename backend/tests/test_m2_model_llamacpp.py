"""Optional: verify a REAL completion from the llama.cpp model container.

Kept out of `make test` — only runs when MODEL_BACKEND=llamacpp and the server is reachable
(i.e. after `make model`). Confirms the OpenAI-compatible gateway path returns non-empty text.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from model.gateway import ModelGateway  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("MODEL_BACKEND", "stub").lower() != "llamacpp",
    reason="set MODEL_BACKEND=llamacpp and run `make model` to exercise the real model",
)


def test_llamacpp_returns_text():
    gw = ModelGateway()
    if not gw.health():
        pytest.skip("model server not reachable on {} — run `make model`".format(gw.base_url))
    out = gw.complete("Reply with the single word: pong.")
    assert isinstance(out, str) and out.strip()
