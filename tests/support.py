"""Shared fixture: an isolated HOME, TIDYGUARD_HOME and git repositories."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tidyguard import guard
from tidyguard.config import Config, load_config


class GuardCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.home = self.root / "home"
        self.home.mkdir()
        self.guard_home = self.home / ".tidyguard"
        env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "TIDYGUARD_HOME": str(self.guard_home),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        cron = mock.patch.object(guard, "crontab_hash", return_value="")
        cron.start()
        self.addCleanup(cron.stop)

    @property
    def cfg(self) -> Config:
        return load_config()

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
        ).stdout

    def repository(self, name: str = "repo") -> Path:
        repo = self.root / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.git(
            repo,
            "-c",
            "user.name=Guard Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "initial",
        )
        return repo

    def pre(self, tool: str, tool_input: Any, cwd: Path) -> str | None:
        """Run a pre-tool check; returns the block message or None if allowed."""
        try:
            guard.on_pre_tool(
                self.cfg, {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}
            )
        except guard.Blocked as exc:
            return str(exc)
        return None

    def bash(self, command: str, cwd: Path) -> str | None:
        return self.pre("Bash", {"command": command}, cwd)

    def start(self, cwd: Path, session: str = "s1", **extra: Any) -> str:
        return guard.on_session_start(self.cfg, {"session_id": session, "cwd": str(cwd), **extra})

    def stop(self, cwd: Path, session: str = "s1", **extra: Any) -> str:
        return guard.on_stop(
            self.cfg, {"session_id": session, "cwd": str(cwd), "_client": "opencode", **extra}
        )


@contextlib.contextmanager
def captured() -> Any:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err
