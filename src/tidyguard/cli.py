"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, grants, guard, install
from .config import Config, ConfigError, load_config

EVENTS = ("session-start", "pre-tool", "stop")


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("hook payload is not a JSON object")
    return data


def run_hook(event: str) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        sys.stderr.write(f"tidyguard: config error: {exc}\n")
        # Without a usable policy, writes are blocked; other events must not trap the agent.
        return 2 if event == "pre-tool" else 1
    try:
        data = read_payload()
        if event == "pre-tool":
            guard.on_pre_tool(cfg, data)
            return 0
        handler = guard.on_session_start if event == "session-start" else guard.on_stop
        out = handler(cfg, data)
        if out:
            print(out)
        return 0
    except guard.Blocked as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001 - every failure must map to a hook exit code
        sys.stderr.write(f"tidyguard: internal error in {event}: {exc!r}\n")
        if event == "pre-tool" and cfg.fail_closed:
            sys.stderr.write("tidyguard: blocked because fail_closed = true\n")
            return 2
        return 1


def run_allow(cfg: Config, args: argparse.Namespace) -> int:
    if args.list:
        now = time.time()
        live = grants.active(cfg)
        for g in live:
            left = int((grants.expires(g) - now) / 60)
            print(f"{g.get('kind', '?'):<10} {g.get('repo', '?')}  {left} min left")
        if not live:
            print("no active grants")
        return 0
    if args.revoke:
        grants.revoke(cfg)
        print("all grants revoked")
        return 0
    if not args.kind:
        sys.stderr.write(
            "tidyguard allow: give a kind (docs, automation, all), --list or --revoke\n"
        )
        return 1
    if args.repo == "*":
        repo = "*"
    else:
        root = guard.repo_root(Path(args.repo or os.getcwd()).expanduser().resolve())
        if root is None:
            sys.stderr.write("tidyguard allow: not inside a git repo; pass a repo path or '*'\n")
            return 1
        repo = str(root)
    minutes = args.minutes or cfg.grant_minutes
    grants.add(cfg, args.kind, repo, minutes)
    print(f"allowed: {args.kind} for {repo} ({minutes} min)")
    return 0


def run_check(cfg: Config, args: argparse.Namespace) -> int:
    command = " ".join(args.command)
    try:
        guard.check_shell(cfg, command, Path(args.cwd).expanduser().resolve())
    except guard.Blocked as exc:
        print(f"deny: {exc}")
        return 2
    print("allow")
    return 0


def run_status(cfg: Config) -> int:
    print(f"tidyguard {__version__}")
    print(f"home:   {cfg.home}")
    print(f"config: {cfg.config_file if cfg.config_file.exists() else 'defaults (no config.toml)'}")
    rules = cfg.home / "rules.md"
    print(f"rules:  {rules if rules.exists() else 'not installed'}")
    for client in install.CLIENTS:
        state = "installed" if install.installed(client) else "not installed"
        print(f"{client + ':':<9}{state}")
    print(f"grants: {len(grants.active(cfg))} active")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tidyguard",
        description="Keep coding agents from leaving notes, rewritten specs, "
        "unrequested automation and leftovers behind.",
    )
    parser.add_argument("--version", action="version", version=f"tidyguard {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    hook = sub.add_parser("hook", help="run a hook event (payload JSON on stdin)")
    hook.add_argument("event", choices=EVENTS)

    allow = sub.add_parser("allow", help="grant agents a time-limited exception (user only)")
    allow.add_argument("kind", nargs="?", choices=grants.KINDS)
    allow.add_argument("repo", nargs="?", help="repo path or '*' (default: current repo)")
    allow.add_argument("minutes", nargs="?", type=int)
    allow.add_argument("--list", action="store_true", help="show active grants")
    allow.add_argument("--revoke", action="store_true", help="remove every grant")

    check = sub.add_parser("check", help="dry-run a shell command against the policy")
    check.add_argument("--cwd", default=".", help="directory the command would run in")
    check.add_argument("command", nargs=argparse.REMAINDER)

    for name, text in (("install", "register the hooks"), ("uninstall", "remove the hooks")):
        p = sub.add_parser(name, help=f"{text} for a client")
        p.add_argument("client", choices=install.CLIENTS)
        if name == "install":
            p.add_argument("--rules", action="store_true", help="also install the agent rules")

    sub.add_parser("rules", help="print the agent rules")
    sub.add_parser("status", help="show configuration and installed clients")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hook":
        return run_hook(args.event)
    try:
        cfg = load_config()
    except ConfigError as exc:
        sys.stderr.write(f"tidyguard: config error: {exc}\n")
        return 1
    if args.cmd == "allow":
        return run_allow(cfg, args)
    if args.cmd == "check":
        if args.command[:1] == ["--"]:
            args.command = args.command[1:]
        if not args.command:
            sys.stderr.write("tidyguard check: give a command, e.g. tidyguard check -- 'echo hi'\n")
            return 1
        return run_check(cfg, args)
    if args.cmd == "rules":
        installed = cfg.home / "rules.md"
        print(
            installed.read_text(encoding="utf-8")
            if installed.exists()
            else install.bundled("rules.md"),
            end="",
        )
        return 0
    if args.cmd == "status":
        return run_status(cfg)
    try:
        if args.cmd == "install":
            notes = install.install(cfg, args.client, args.rules)
        else:
            notes = install.uninstall(cfg, args.client)
    except install.InstallError as exc:
        sys.stderr.write(f"tidyguard {args.cmd}: {exc}\n")
        return 1
    for note in notes:
        print(note)
    return 0
