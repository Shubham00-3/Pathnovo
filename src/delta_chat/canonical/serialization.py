"""Stable JSON serialization for canonical documents and deltas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def to_stable_dict(model: BaseModel, *, exclude_timestamps: bool = False) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    if exclude_timestamps:
        for key in ("generated_at", "timestamp", "created_at"):
            data.pop(key, None)
    return data


def dump_json(path: Path | str, data: Any, *, sort_keys: bool = True) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=sort_keys, ensure_ascii=False)
        f.write("\n")


def load_json(path: Path | str) -> Any:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)
