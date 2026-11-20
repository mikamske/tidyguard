"""Register and remove tidyguard's hooks in the user-level config of each client.

Every change is idempotent: our entries are recognised by the `tidyguard hook` command
or by marker comments, replaced on install and dropped on uninstall. Other entries stay
untouched. Before a file is changed, its previous version is kept as `<file>.tidyguard-bak`
(one copy, overwritten by the next change).
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config

CLIENTS = ("claude", "codex", "opencode")
MARK_START = "<!-- tidyguard:start -->"
MARK_END = "<!-- tidyguard:end -->"

CLAUDE_EVENTS = (
    ("SessionStart", "startup|resume|clear|compact", "session-start", 20),
    ("PreToolUse", "Bash|Write|Edit|MultiEdit|NotebookEdit", "pre-tool", 15),
    ("Stop", None, "stop", 30),
)
CODEX_EVENTS = (
    ("SessionStart", "startup|resume|clear|compact", "session-start", 20),
    ("PreToolUse", "Bash|apply_patch|Edit|Write", "pre-tool", 15),
    ("Stop", None, "stop", 30),
)
CODEX_STATUS = {
    "session-start": "tidyguard: snapshot",
    "pre-tool": "tidyguard: write check",
    "stop": "tidyguard: cleanup check",
}


class InstallError(Exception):
    pass


def guard_argv() -> list[str]:
    exe = shutil.which("tidyguard")
    return [exe] if exe else [sys.executable, "-m", "tidyguard"]


def hook_command(event: str) -> str:
    return " ".join(shlex.quote(a) for a in [*guard_argv(), "hook", event])


def client_paths(client: str) -> dict[str, Path]:
    home = Path.home()
    if client == "claude":
        return {
            "hooks": home / ".claude" / "settings.json",
            "rules": home / ".claude" / "CLAUDE.md",
        }
    if client == "codex":
        return {"hooks": home / ".codex" / "hooks.json", "rules": home / ".codex" / "AGENTS.md"}
    if client == "opencode":
        return {"hooks": home / ".config" / "opencode" / "plugins" / "tidyguard.ts"}
    raise InstallError(f"unknown client {client!r}; choose one of {', '.join(CLIENTS)}")


def backup_and_write(path: Path, text: str) -> None:
    if path.is_symlink():
        raise InstallError(f"{path} is a symlink; edit its target by hand")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".tidyguard-bak"))
    tmp = path.with_name(path.name + ".tidyguard-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); not touching it") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path} does not contain a JSON object; not touching it")
    return data


def is_ours(group: Any) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    for hook in group["hooks"]:
        words = str(hook.get("command", "")).split() if isinstance(hook, dict) else []
        if "hook" in words and any("tidyguard" in w for w in words):
            return True
    return False


def strip_hooks(data: dict[str, Any]) -> dict[str, Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    for event in list(hooks):
        groups = hooks[event]
        if isinstance(groups, list):
            kept = [g for g in groups if not is_ours(g)]
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not hooks:
        del data["hooks"]
    return data


def add_hooks(data: dict[str, Any], client: str) -> dict[str, Any]:
    data = strip_hooks(data)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("existing 'hooks' setting is not an object; not touching it")
    for event, matcher, name, timeout in CLAUDE_EVENTS if client == "claude" else CODEX_EVENTS:
        entry: dict[str, Any] = {
            "type": "command",
            "command": hook_command(name),
            "timeout": timeout,
        }
        if client == "codex":
            entry["statusMessage"] = CODEX_STATUS[name]
        group: dict[str, Any] = {"hooks": [entry]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks.setdefault(event, []).append(group)
    return data


def strip_block(text: str) -> str:
    pattern = re.compile(rf"\n?{re.escape(MARK_START)}.*?{re.escape(MARK_END)}\n?", re.S)
    rest = pattern.sub("\n", text).strip("\n")
    return rest + "\n" if rest else ""


def rules_block(cfg: Config, client: str) -> str:
    rules = cfg.home / "rules.md"
    # Claude Code imports files with @path; Codex has no import, so it gets the text.
    body = f"@{rules}" if client == "claude" else rules.read_text(encoding="utf-8").strip()
    return f"{MARK_START}\n{body}\n{MARK_END}\n"


def ensure_rules_file(cfg: Config) -> bool:
    """Copy the bundled rules once. An existing (possibly edited) file is kept."""
    target = cfg.home / "rules.md"
    if target.exists():
        return False
    cfg.home.mkdir(parents=True, exist_ok=True)
    target.write_text(bundled("rules.md"), encoding="utf-8")
    return True


def bundled(name: str) -> str:
    return resources.files("tidyguard").joinpath(name).read_text(encoding="utf-8")


def install(cfg: Config, client: str, rules: bool) -> list[str]:
    paths = client_paths(client)
    if rules and "rules" in paths and paths["rules"].is_symlink():
        raise InstallError(
            f"{paths['rules']} is a symlink; add the rules by hand (see README) or run "
            "without --rules"
        )
    notes: list[str] = []
    if client == "opencode":
        plugin = bundled("opencode.ts").replace("__TIDYGUARD_ARGV__", json.dumps(guard_argv()))
        backup_and_write(paths["hooks"], plugin)
        notes.append(f"wrote {paths['hooks']}")
    else:
        data = add_hooks(read_json(paths["hooks"]), client)
        backup_and_write(paths["hooks"], json.dumps(data, indent=2) + "\n")
        notes.append(f"registered hooks in {paths['hooks']}")
        if client == "codex":
            notes.append("Codex runs new or changed hooks only after you review them with /hooks")
    if rules:
        if ensure_rules_file(cfg):
            notes.append(f"wrote rules to {cfg.home / 'rules.md'} (edit them there)")
        if client == "opencode":
            notes.append(
                f'add "{cfg.home / "rules.md"}" to "instructions" in your opencode.json(c)'
            )
        else:
            target = paths["rules"]
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            text = strip_block(current)
            text = (text + "\n" if text else "") + rules_block(cfg, client)
            backup_and_write(target, text)
            notes.append(f"linked the rules from {target}")
    return notes


def uninstall(cfg: Config, client: str) -> list[str]:
    paths = client_paths(client)
    notes: list[str] = []
    if client == "opencode":
        if paths["hooks"].exists():
            paths["hooks"].unlink()
            notes.append(f"removed {paths['hooks']}")
    elif paths["hooks"].exists():
        data = read_json(paths["hooks"])
        if "hooks" in data:
            backup_and_write(paths["hooks"], json.dumps(strip_hooks(data), indent=2) + "\n")
            notes.append(f"removed tidyguard hooks from {paths['hooks']}")
    rules = paths.get("rules")
    if rules and rules.exists() and not rules.is_symlink():
        current = rules.read_text(encoding="utf-8")
        if MARK_START in current:
            backup_and_write(rules, strip_block(current))
            notes.append(f"removed the rules block from {rules}")
    return notes or ["nothing to remove"]


def installed(client: str) -> bool:
    path = client_paths(client)["hooks"]
    if client == "opencode":
        return path.exists()
    try:
        data = read_json(path)
    except InstallError:
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(is_ours(g) for groups in hooks.values() if isinstance(groups, list) for g in groups)
