"""Configuration loading and hashing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from delta_chat.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return ROOT


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise ConfigError(f"Config not found: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping")
    return data


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    env_path = os.environ.get("DELTA_CHAT_CONFIG")
    cfg_path = path or env_path or "config/default.yaml"
    cfg = load_yaml(cfg_path)
    # Environment overrides for LLM
    if os.environ.get("LLM_PROVIDER"):
        cfg.setdefault("llm", {})["provider"] = os.environ["LLM_PROVIDER"]
    if os.environ.get("LLM_MODEL"):
        cfg.setdefault("llm", {})["model"] = os.environ["LLM_MODEL"]
    if os.environ.get("CAPTURE_LLM_CONTENT"):
        cfg.setdefault("llm", {})["capture_content"] = (
            os.environ["CAPTURE_LLM_CONTENT"].lower() in {"1", "true", "yes"}
        )
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    blob = json.dumps(cfg, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def resolve_under_root(path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a path and ensure it stays under the project or data root."""
    base = root or ROOT
    p = Path(path)
    if not p.is_absolute():
        p = (base / p).resolve()
    else:
        p = p.resolve()
    base_r = base.resolve()
    try:
        p.relative_to(base_r)
    except ValueError as exc:
        from delta_chat.errors import PathEscapeError

        raise PathEscapeError(
            f"Path escapes allowed root: {p}",
            details={"path": str(p), "root": str(base_r)},
        ) from exc
    return p
