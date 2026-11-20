"""Paths and policy settings. Every setting has a default; the config file is optional."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """The config file exists but cannot be used."""


DEFAULT_DOC_EXTENSIONS = (".md", ".mdx", ".txt", ".rst")
DEFAULT_RULE_FILES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "GEMINI.md")
DEFAULT_SPEC_WORDS = (
    "spec",
    "specs",
    "specification",
    "plan",
    "plans",
    "handoff",
    "handoffs",
    "roadmap",
    "prd",
    "adr",
    "adrs",
    "requirements",
)
DEFAULT_DOC_EXEMPT = (r"(^|/)\.changeset/",)
DEFAULT_HOOK_FILES = (
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
    "~/.codex/hooks.json",
    "~/.codex/config.toml",
    "~/.config/opencode/plugins/tidyguard.ts",
)

# Machine-wide places where agents tend to leave scheduled jobs behind.
AUTOMATION_DIRS = {
    "launch_agents": "~/Library/LaunchAgents",
    "systemd_user": "~/.config/systemd/user",
    "codex_automations": "~/.codex/automations",
}


def guard_home() -> Path:
    raw = os.environ.get("TIDYGUARD_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".tidyguard"


@dataclass(frozen=True)
class Config:
    home: Path
    doc_extensions: frozenset[str]
    rule_files: frozenset[str]
    spec_re: re.Pattern[str]
    doc_exempt: tuple[re.Pattern[str], ...]
    block_new_docs: bool
    block_automation: bool
    stop_review: bool
    report_cap: int
    state_max_age_days: int
    grant_minutes: int
    fail_closed: bool
    hook_files: tuple[Path, ...]
    automation_dirs: dict[str, Path]

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def grants_file(self) -> Path:
        return self.home / "grants.json"

    @property
    def state_dir(self) -> Path:
        return self.home / "sessions"

    @property
    def scratch_root(self) -> Path:
        return self.home / "scratch"


_SCHEMA: dict[str, dict[str, type]] = {
    "": {"fail_closed": bool},
    "docs": {
        "extensions": list,
        "rule_files": list,
        "spec_words": list,
        "exempt": list,
        "block_new": bool,
    },
    "automation": {"block": bool},
    "stop": {"review": bool, "report_cap": int},
    "grants": {"default_minutes": int},
    "state": {"max_age_days": int},
    "protect": {"hook_files": list},
}


def _validate(data: dict[str, Any]) -> None:
    for key, value in data.items():
        if key in _SCHEMA and key != "":
            if not isinstance(value, dict):
                raise ConfigError(f"[{key}] must be a table")
            for sub, sub_value in value.items():
                expected = _SCHEMA[key].get(sub)
                if expected is None:
                    raise ConfigError(f"unknown setting {key}.{sub}")
                if not isinstance(sub_value, expected) or (
                    expected is int and isinstance(sub_value, bool)
                ):
                    raise ConfigError(f"{key}.{sub} must be {expected.__name__}")
                if isinstance(sub_value, list) and not all(isinstance(i, str) for i in sub_value):
                    raise ConfigError(f"{key}.{sub} must be a list of strings")
        elif key in _SCHEMA[""]:
            if not isinstance(value, _SCHEMA[""][key]):
                raise ConfigError(f"{key} must be {_SCHEMA[''][key].__name__}")
        else:
            raise ConfigError(f"unknown setting {key}")


def load_config(home: Path | None = None) -> Config:
    home = home or guard_home()
    path = home / "config.toml"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        _validate(data)

    def get(section: str, key: str, default: Any) -> Any:
        return data.get(section, {}).get(key, default)

    words = get("docs", "spec_words", list(DEFAULT_SPEC_WORDS))
    spec_re = re.compile(
        r"(^|[/_.\-])(" + "|".join(re.escape(w.lower()) for w in words) + r")([/_.\-]|$)"
    )
    try:
        exempt = tuple(re.compile(p) for p in get("docs", "exempt", list(DEFAULT_DOC_EXEMPT)))
    except re.error as exc:
        raise ConfigError(f"docs.exempt: {exc}") from exc

    return Config(
        home=home,
        doc_extensions=frozenset(
            e.lower() for e in get("docs", "extensions", list(DEFAULT_DOC_EXTENSIONS))
        ),
        rule_files=frozenset(
            f.lower() for f in get("docs", "rule_files", list(DEFAULT_RULE_FILES))
        ),
        spec_re=spec_re,
        doc_exempt=exempt,
        block_new_docs=get("docs", "block_new", True),
        block_automation=get("automation", "block", True),
        stop_review=get("stop", "review", True),
        report_cap=max(1, get("stop", "report_cap", 30)),
        state_max_age_days=max(1, get("state", "max_age_days", 7)),
        grant_minutes=max(1, get("grants", "default_minutes", 60)),
        fail_closed=data.get("fail_closed", True),
        hook_files=tuple(
            Path(p).expanduser() for p in get("protect", "hook_files", list(DEFAULT_HOOK_FILES))
        ),
        automation_dirs={k: Path(v).expanduser() for k, v in AUTOMATION_DIRS.items()},
    )
