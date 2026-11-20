"""Policy core shared by every client adapter.

Events (hook JSON on stdin, see cli.py):

- session-start: snapshot repo and machine state, prune expired records, print context.
- pre-tool:      block notes/specs written into repos, edits to spec and agent rule files,
                 new automation, and changes to tidyguard itself, unless the user granted it.
- stop:          diff against the session-start snapshot and make the agent clean up
                 (or justify) every new artifact once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from . import grants
from .config import Config

UNTRACKED_CAP = 5000
SCRATCH_CAP = 2000
GRANT_HINT = "run `tidyguard allow {kind}` in their own terminal (agents never run it)"
SHELL_TOOLS = ("bash", "shell", "exec_command", "local_shell")

AUTOMATION_ANY = re.compile(r"(^|/)\.husky/")
AUTOMATION_NEW = re.compile(r"(^|/)\.github/workflows/")
BASH_AUTOMATION = re.compile(
    r"\bcrontab\s+(?!-l\b)\S"
    r"|\blaunchctl\s+(load|bootstrap|enable|submit)\b"
    r"|\bsystemctl\s+--user\s+(enable|link|start)\b"
    r"|Library/LaunchAgents/"
    r"|\.config/systemd/user/"
    r"|\.git/hooks/"
    r"|\bgit\s+config\b[^;&|\n]*\bcore\.hooksPath\b"
    r"|\bhusky\s+(install|init)\b"
)
USER_COMMAND = re.compile(
    r"(^|[\s;&|(/`])tidyguard\s+(allow|uninstall)\b|-m\s+tidyguard\s+(allow|uninstall)\b"
)
# Shell operations that can change a file named on the same command line.
SHELL_WRITE = re.compile(
    r"(?<![0-9&<])>(?!&)|\btee\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i"
    r"|\b(rm|mv|cp|ln|install|truncate|unlink|chmod|dd|python3?|node|ruby|jq)\b"
)
FILE_COMMANDS = re.compile(r"(?:^|[\s;&|(`])(cp|mv|touch|install|ln)\s+([^;&|\n`)]*)")
IN_PLACE = re.compile(r"\b(?:sed|perl)\s+(?:-[a-zA-Z]*i\S*|--in-place\S*)\s+([^;&|\n`)]*)")


class Blocked(Exception):
    """A pre-tool check denied the call; the message is shown to the agent."""


# ---------- helpers ----------


def git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def repo_root(path: Path) -> Path | None:
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    out = git(probe, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def state_path(cfg: Config, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return cfg.state_dir / f"{safe}.json"


def scratch_dir(cfg: Config, repo: Path | None) -> Path:
    return cfg.scratch_root / (repo.name if repo else "_global")


def resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return Path(os.path.normpath(path))


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------- snapshot ----------


def list_dir(path: Path) -> list[str]:
    try:
        return sorted(p.name for p in path.iterdir())
    except OSError:
        return []


def list_tree(path: Path, cap: int) -> list[str]:
    out: list[str] = []
    for current, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            out.append(os.path.relpath(os.path.join(current, name), path))
            if len(out) >= cap:
                return out
    return out


def crontab_hash() -> str:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return hashlib.sha256(result.stdout.encode()).hexdigest()[:16]


def snapshot(cfg: Config, cwd: Path) -> dict[str, Any]:
    snap: dict[str, Any] = {key: list_dir(path) for key, path in cfg.automation_dirs.items()}
    snap["crontab"] = crontab_hash()
    root = repo_root(cwd)
    snap["scratch"] = list_tree(scratch_dir(cfg, root), SCRATCH_CAP)
    if root is None:
        return snap
    snap["repo"] = str(root)
    untracked = (git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines()
    snap["untracked"] = untracked[:UNTRACKED_CAP]
    worktrees = git(root, "worktree", "list", "--porcelain") or ""
    snap["worktrees"] = [
        line[len("worktree ") :] for line in worktrees.splitlines() if line.startswith("worktree ")
    ]
    refs = git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads") or ""
    snap["branches"] = refs.split()
    stashes = git(root, "stash", "list", "--format=%H %gs") or ""
    snap["stashes"] = stashes.splitlines()
    common = (git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") or "").strip()
    git_dir = (git(root, "rev-parse", "--absolute-git-dir") or "").strip()
    if common and git_dir and Path(common).resolve() != Path(git_dir).resolve():
        snap["current_worktree"] = str(root)
        branch = (git(root, "branch", "--show-current") or "").strip()
        snap["current_worktree_branch"] = branch or "detached HEAD"
    hooks = list_dir(Path(common) / "hooks") if common else []
    snap["git_hooks"] = [h for h in hooks if not h.endswith(".sample")]
    return snap


LABELS = {
    "untracked": "new untracked file",
    "worktrees": "new worktree",
    "branches": "new branch",
    "stashes": "new stash entry",
    "git_hooks": "new git hook",
    "scratch": "scratch file left over",
    "launch_agents": "new LaunchAgent (~/Library/LaunchAgents)",
    "systemd_user": "new systemd user unit (~/.config/systemd/user)",
    "codex_automations": "new Codex automation",
}
MACHINE_KEYS = ("launch_agents", "systemd_user", "codex_automations", "crontab")


def new_items(base: dict[str, Any], cur: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, label in LABELS.items():
        before = set(base.get(key, []))
        out += [f"{label}: {item}" for item in cur.get(key, []) if item not in before]
    if "crontab" in cur and cur.get("crontab") != base.get("crontab"):
        out.append("crontab was changed")
    return out


def prune(cfg: Config) -> None:
    """Expire this guard's own session records. Scratch is never deleted by age:
    it can hold live worktrees and unmerged work, and age proves neither ownership
    nor that the work is finished."""
    now = time.time()
    max_age = cfg.state_max_age_days * 86400
    if cfg.state_dir.exists():
        for path in cfg.state_dir.glob("*.json"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if now - path.stat().st_mtime > max_age:
                    path.unlink()
            except FileNotFoundError:
                continue  # Another guard process expired the same record.
    grants.prune(cfg)


# ---------- context ----------


def session_context(cfg: Config, root: Path | None) -> str:
    return (
        "tidyguard is active. Do not write notes, plans, specs, handoffs or status reports into "
        "the repository and do not edit spec or agent rule files (AGENTS.md, CLAUDE.md) unless "
        "the user asks for exactly that; plans and status belong in the chat. Do not create "
        "automation (cron, launchd, systemd, git hooks, CI workflows) without an explicit "
        "request; requested automation ships with retention and a removal command. "
        f"Scratch files go to {scratch_dir(cfg, root)} (outside the repo) and are deleted before "
        "you finish. Everything you create is either a deliverable named in your final report "
        "under `Left behind:` or removed. If tidyguard blocks a call, do not work around it: "
        "show the content in the chat and ask the user."
    )


def on_session_start(cfg: Config, data: dict[str, Any]) -> str:
    prune(cfg)
    cwd = Path(data.get("cwd") or os.getcwd())
    path = state_path(cfg, str(data.get("session_id", "")))
    if not path.exists():
        write_json(path, {"baseline": snapshot(cfg, cwd), "acked": {}})
    else:
        state = load_json(path, {})
        state.pop("reviewed_worktree", None)
        write_json(path, state)
    ctx = session_context(cfg, repo_root(cwd))
    if data.get("_client") == "opencode":
        return ctx
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    )


# ---------- pre-tool ----------


def patch_targets(text: str) -> list[tuple[str, str]]:
    """(path, action) pairs from an apply_patch envelope; action is add, update or delete."""
    actions = {
        "Add File": "add",
        "Update File": "update",
        "Delete File": "delete",
        "Move to": "add",
    }
    pattern = r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$"
    return [(m.group(2).strip(), actions[m.group(1)]) for m in re.finditer(pattern, text, re.M)]


def patch_removes_guard(text: str, target: str) -> bool:
    """True if the patch section for `target` deletes a line mentioning tidyguard
    without adding one back."""
    section: list[str] = []
    active = False
    for line in text.splitlines():
        if line.startswith("*** "):
            active = line.endswith(f": {target}")
            continue
        if active:
            section.append(line)
    removed = any(line.startswith("-") and "tidyguard" in line for line in section)
    added = any(line.startswith("+") and "tidyguard" in line for line in section)
    return removed and not added


def write_targets(tool: str, ti: Any) -> list[tuple[str, str | None]]:
    """(path, action) pairs; action None means 'decide from the filesystem'."""
    if isinstance(ti, str):
        return list(patch_targets(ti))
    if not isinstance(ti, dict):
        return []
    for key in ("patchText", "patch"):
        if isinstance(ti.get(key), str):
            return list(patch_targets(ti[key]))
    if tool.lower() in ("apply_patch", "patch") and isinstance(ti.get("command"), str):
        return list(patch_targets(ti["command"]))
    for key in ("file_path", "filePath", "path", "notebook_path"):
        if isinstance(ti.get(key), str):
            return [(ti[key], None)]
    return []


def patch_text(ti: Any) -> str:
    if isinstance(ti, str):
        return ti
    if isinstance(ti, dict):
        for key in ("patchText", "patch", "command"):
            if isinstance(ti.get(key), str):
                return str(ti[key])
    return ""


def own_files(cfg: Config) -> list[Path]:
    return [cfg.config_file, cfg.grants_file, cfg.state_dir, cfg.home / "rules.md"]


def check_own_path(cfg: Config, path: Path) -> None:
    if inside(path, cfg.home) and not inside(path, cfg.scratch_root):
        raise Blocked(
            f"tidyguard: {path} belongs to tidyguard itself (config, grants, rules, session "
            "records). Agents do not change it. Ask the user."
        )


def guarded_hook_files(cfg: Config) -> list[Path]:
    """Shared hook configs that currently register tidyguard."""
    return [p for p in cfg.hook_files if "tidyguard" in read_text(p)]


def check_hook_file(cfg: Config, path: Path, raw: str, action: str | None, ti: Any) -> None:
    """Allow edits to shared hook configs, but not ones that drop tidyguard from them."""
    if path not in guarded_hook_files(cfg):
        return
    removes = False
    if action == "delete":
        removes = True
    elif action is not None:
        removes = patch_removes_guard(patch_text(ti), raw)
    elif isinstance(ti, dict):
        if isinstance(ti.get("content"), str):
            removes = "tidyguard" not in ti["content"]
        edits = ti.get("edits")
        for edit in edits if isinstance(edits, list) else [ti]:
            if not isinstance(edit, dict):
                continue
            old = edit.get("old_string", edit.get("oldString"))
            new = edit.get("new_string", edit.get("newString"))
            if isinstance(old, str) and isinstance(new, str):
                removes = removes or ("tidyguard" in old and "tidyguard" not in new)
    if removes:
        raise Blocked(
            f"tidyguard: this change would remove tidyguard's hooks from {path}. "
            "Agents do not disable the guard. Ask the user (`tidyguard uninstall`)."
        )


def check_path(cfg: Config, raw: str, is_new: bool | None, cwd: Path) -> None:
    path = resolve(raw, cwd)
    if is_new is None:
        is_new = not path.exists()
    check_own_path(cfg, path)

    if any(inside(path, d) for d in cfg.automation_dirs.values()):
        if cfg.block_automation and not grants.allowed(cfg, None, "automation"):
            raise Blocked(
                f"tidyguard: new automation blocked ({path}). Scheduled jobs only on the user's "
                f"explicit request; to allow it, the user can {GRANT_HINT.format(kind='automation')}. "
                "Do the task once by hand instead."
            )
        return

    parts = path.parts
    if ".git" in parts:
        # git cannot resolve a work tree from inside .git, so hooks are matched by path.
        i = parts.index(".git")
        if parts[i + 1 : i + 2] == ("hooks",):
            repo = Path(*parts[:i])
            if cfg.block_automation and not grants.allowed(cfg, repo, "automation"):
                raise Blocked(
                    f"tidyguard: git hook blocked ({path}). New hooks only on the user's "
                    f"explicit request; to allow it, the user can "
                    f"{GRANT_HINT.format(kind='automation')}."
                )
        return

    root = repo_root(path)
    if root is None:
        return
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    low = rel.lower()

    if AUTOMATION_ANY.search(low) or (is_new and AUTOMATION_NEW.search(low)):
        if cfg.block_automation and not grants.allowed(cfg, root, "automation"):
            raise Blocked(
                f"tidyguard: automation blocked ({rel}). New hooks and workflows only on the "
                f"user's explicit request; to allow it, the user can "
                f"{GRANT_HINT.format(kind='automation')}."
            )
        return

    if Path(low).suffix not in cfg.doc_extensions or any(p.search(low) for p in cfg.doc_exempt):
        return
    is_rule = Path(low).name in cfg.rule_files
    is_spec = bool(cfg.spec_re.search(low))
    if (is_rule or is_spec or (is_new and cfg.block_new_docs)) and not grants.allowed(
        cfg, root, "docs"
    ):
        why = (
            "agent rule file"
            if is_rule
            else "spec/plan/handoff file"
            if is_spec
            else "new note or doc file in the repo"
        )
        raise Blocked(
            f"tidyguard: {why} blocked ({rel}). Agents do not write specs, plans, handoffs or "
            f"rule files. Plans and status belong in the chat; scratch files go to "
            f"{scratch_dir(cfg, root)}. If the user explicitly asked for this file, show the "
            f"content in the chat and ask them to {GRANT_HINT.format(kind='docs')}. "
            "Do not work around this block with the shell."
        )


def path_variants(path: Path) -> list[str]:
    out = [str(path)]
    home = Path.home()
    if inside(path, home):
        rel = os.path.relpath(path, home)
        out += [f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"]
    return out


def shell_words(text: str) -> list[str]:
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.split()


def shell_doc_targets(cfg: Config, cmd: str) -> list[tuple[str, bool | None]]:
    """Doc files a shell command writes to: redirects, tee, cp/mv/touch/install/ln
    destinations, in-place edits and one-line interpreter writes."""
    ext = "|".join(re.escape(e.lstrip(".")) for e in sorted(cfg.doc_extensions))
    if not ext:
        return []
    doc = rf"[^\s'\"|;&<>()`]+\.(?:{ext})"
    found: list[tuple[str, bool | None]] = []
    for m in re.finditer(rf"(?:>>?|\btee\s+(?:-a\s+)?)\s*['\"]?({doc})(?=$|[\s'\";&|)`])", cmd):
        found.append((m.group(1), None))
    for pattern in (
        rf"\bopen\(\s*['\"]({doc})['\"]\s*,\s*['\"][wax]",
        rf"\bPath\(\s*['\"]({doc})['\"]\s*\)\s*\.write_(?:text|bytes)\(",
        rf"\bwriteFileSync\(\s*['\"]({doc})['\"]",
    ):
        found += [(m.group(1), None) for m in re.finditer(pattern, cmd)]
    doc_suffix = tuple(cfg.doc_extensions)
    for m in FILE_COMMANDS.finditer(cmd):
        args = [a for a in shell_words(m.group(2)) if not a.startswith("-")]
        targets = args if m.group(1) == "touch" else args[-1:]
        found += [(a, None) for a in targets if a.lower().endswith(doc_suffix)]
    for m in IN_PLACE.finditer(cmd):
        args = shell_words(m.group(1))
        found += [(a, False) for a in args if a.lower().endswith(doc_suffix)]
    return [(t, n) for t, n in found if not t.startswith("/dev/")]


def check_shell(cfg: Config, cmd: str, cwd: Path) -> None:
    if USER_COMMAND.search(cmd):
        raise Blocked(
            "tidyguard: `tidyguard allow` and `tidyguard uninstall` are the user's commands. "
            "Agents never run them; ask the user."
        )
    if SHELL_WRITE.search(cmd):
        for protected in [*own_files(cfg), *guarded_hook_files(cfg)]:
            if any(v in cmd for v in path_variants(protected)):
                raise Blocked(
                    f"tidyguard: shell command touches {protected}, which protects or configures "
                    "the guard. Agents do not change it. Ask the user."
                )
    if cfg.block_automation and BASH_AUTOMATION.search(cmd):
        if not grants.allowed(cfg, repo_root(cwd), "automation"):
            raise Blocked(
                "tidyguard: automation (crontab, launchd, systemd, git hooks) blocked. Only on the "
                f"user's explicit request; to allow it, the user can "
                f"{GRANT_HINT.format(kind='automation')}."
            )
    for target, is_new in shell_doc_targets(cfg, cmd):
        check_path(cfg, target, is_new, cwd)


def on_pre_tool(cfg: Config, data: dict[str, Any]) -> None:
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input")
    cwd = Path(data.get("cwd") or os.getcwd())

    if tool.lower() in SHELL_TOOLS:
        cmd = ti.get("command") if isinstance(ti, dict) else ti
        if isinstance(cmd, list):
            cmd = " ".join(map(str, cmd))
        if isinstance(cmd, str):
            check_shell(cfg, cmd, cwd)
        return

    for raw, action in write_targets(tool, ti):
        check_hook_file(cfg, resolve(raw, cwd), raw, action, ti)
        is_new = None if action is None else action == "add"
        check_path(cfg, raw, is_new, cwd)


# ---------- stop ----------


def on_stop(cfg: Config, data: dict[str, Any]) -> str:
    if data.get("stop_hook_active") or not cfg.stop_review:
        return ""
    cwd = Path(data.get("cwd") or os.getcwd())
    path = state_path(cfg, str(data.get("session_id", "")))
    state = load_json(path, None)
    preview = bool(data.get("preview"))
    cur = snapshot(cfg, cwd)
    if state is None:
        write_json(path, {"baseline": cur, "acked": {}})
        return ""
    base = state.get("baseline", {})
    if base.get("repo") != cur.get("repo"):
        # The agent moved to another repo: compare only machine-wide state.
        cur_cmp = {k: v for k, v in cur.items() if k in MACHINE_KEYS}
        base = {k: v for k, v in base.items() if k in cur_cmp}
    else:
        cur_cmp = cur
    acked = {} if preview else state.get("acked", {})
    merged: dict[str, Any] = {}
    for key in set(base) | set(acked):
        if isinstance(base.get(key, []), list):
            merged[key] = sorted(set(base.get(key, [])) | set(acked.get(key, [])))
        else:
            merged[key] = acked.get(key, base.get(key))
    items = new_items(merged, cur_cmp)
    worktree = cur.get("current_worktree")
    review = {"path": worktree, "branch": cur.get("current_worktree_branch")}
    if worktree and (preview or state.get("reviewed_worktree") != review):
        items.insert(0, f"working worktree of this session: {worktree} ({review['branch']})")
        if not preview:
            state["reviewed_worktree"] = review
    if not items:
        return ""
    if not preview:
        state["acked"] = {k: v for k, v in cur_cmp.items() if k != "repo"}
        write_json(path, state)
    shown = items[: cfg.report_cap]
    more = f"\n... and {len(items) - cfg.report_cap} more" if len(items) > cfg.report_cap else ""
    reason = (
        "tidyguard cleanup check: artifacts created during this session:\n- "
        + "\n- ".join(shown)
        + more
        + "\n\nFor each item: (a) deliverable of this task: name it in your final report under "
        "`Left behind:`; (b) your own scratch file, test leftover, stash or abandoned attempt: "
        "remove it now (worktree: `git worktree remove`; delete a branch only if you created "
        "it); (c) not yours (a parallel agent or the user): do not touch it, only report it. "
        "A pushed PR alone is no reason to keep a local worker checkout. For every checkout you "
        "keep, give a concrete reason and who cleans it up. Automation nobody asked for is "
        "removed. Then write the final report."
    )
    if data.get("_client") == "opencode":
        return reason
    return json.dumps({"decision": "block", "reason": reason})
