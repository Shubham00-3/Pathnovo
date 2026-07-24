from pathlib import Path

import fitz
import pytest
from scripts.make_synthetic_pid_pair import _assert_fixture_integrity
from scripts.make_synthetic_pid_pair import main as make_pair

from delta_chat.config import project_root


def test_synthetic_pair_no_hidden_text_layer(tmp_path: Path):
    out = tmp_path / "syn"
    make_pair(seed=42, out_dir=out)
    path_a = out / "lift_rev_a.pdf"
    path_b = out / "lift_rev_b.pdf"
    _assert_fixture_integrity(path_a, path_b)

    tb = fitz.open(path_b)[0].get_text("text")
    assert "HH 245" not in tb
    assert "HH 250" in tb
    assert "12000" not in tb
    assert "12500" in tb
    assert '4"-PG-1002-A1' not in tb


def test_repo_samples_if_present():
    root = project_root()
    a = root / "data/samples/synthetic_native/lift_rev_a.pdf"
    b = root / "data/samples/synthetic_native/lift_rev_b.pdf"
    if not a.exists():
        pytest.skip("samples not generated")
    _assert_fixture_integrity(a, b)
