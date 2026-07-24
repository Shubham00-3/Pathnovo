import json
from pathlib import Path

import pytest

from delta_chat.errors import PidNotFoundError
from delta_chat.pid.local_registry import LocalRegistryResolver


def test_resolver_missing(tmp_path: Path):
    reg = tmp_path / "registry.json"
    reg.write_text("{}", encoding="utf-8")
    r = LocalRegistryResolver(reg)
    with pytest.raises(PidNotFoundError):
        r.resolve("NOPE")


def test_resolver_ok(tmp_path: Path, monkeypatch):
    from delta_chat.config import project_root

    root = project_root()
    # use real registry if samples exist after generation; else local fixture
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")
    reg = tmp_path / "registry.json"
    # path must be under project root for resolve_under_root
    target = root / "tests" / "fixtures" / "_tmp_a.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 fixture content for hash")
    reg.write_text(
        json.dumps(
            {
                "PID-X": {
                    "underlying_document_id": "DOC-X",
                    "revision_label": "A",
                    "path": "tests/fixtures/_tmp_a.pdf",
                    "media_type": "application/pdf",
                }
            }
        ),
        encoding="utf-8",
    )
    r = LocalRegistryResolver(reg)
    doc = r.resolve("PID-X")
    assert doc.pid == "PID-X"
    assert doc.sha256
    assert doc.byte_size > 0
