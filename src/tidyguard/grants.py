"""Time-limited permissions the user grants from their own terminal."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import Config

KINDS = ("docs", "automation", "all")


def load(cfg: Config) -> list[dict[str, Any]]:
    try:
        data = json.loads(cfg.grants_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [g for g in data if isinstance(g, dict)] if isinstance(data, list) else []


def save(cfg: Config, items: list[dict[str, Any]]) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    tmp = cfg.grants_file.with_name(f"grants.json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(cfg.grants_file)


def expires(grant: dict[str, Any]) -> float:
    try:
        return float(grant.get("expires", 0))
    except (TypeError, ValueError):
        return 0.0


def active(cfg: Config) -> list[dict[str, Any]]:
    now = time.time()
    return [g for g in load(cfg) if expires(g) >= now]


def allowed(cfg: Config, repo: Path | None, kind: str) -> bool:
    for grant in active(cfg):
        if grant.get("kind") not in (kind, "all"):
            continue
        scope = grant.get("repo")
        if scope == "*" or (repo is not None and str(repo) == scope):
            return True
    return False


def add(cfg: Config, kind: str, repo: str, minutes: int) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if minutes < 1:
        raise ValueError("minutes must be at least 1")
    grant = {"kind": kind, "repo": repo, "expires": time.time() + minutes * 60}
    save(cfg, [*active(cfg), grant])
    return grant


def revoke(cfg: Config) -> None:
    save(cfg, [])


def prune(cfg: Config) -> None:
    current = load(cfg)
    live = active(cfg)
    if len(live) != len(current):
        save(cfg, live)
