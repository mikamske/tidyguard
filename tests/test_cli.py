from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import GuardCase, captured

from tidyguard import cli, guard, install
from tidyguard.config import ConfigError, load_config


def hook(event: str, payload: object) -> tuple[int, str, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    with mock.patch.object(sys, "stdin", stdin), captured() as (out, err):
        code = cli.main(["hook", event])
    return code, out.getvalue(), err.getvalue()


class HookContractTests(GuardCase):
    def test_block_is_exit_2_with_reason_on_stderr(self) -> None:
        repo = self.repository()
        code, out, err = hook(
            "pre-tool",
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(repo / "PLAN.md")},
                "cwd": str(repo),
            },
        )
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("tidyguard:", err)

    def test_allow_is_exit_0_and_silent(self) -> None:
        repo = self.repository()
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(repo)}
        self.assertEqual((0, "", ""), hook("pre-tool", payload))

    def test_broken_config_fails_closed_for_writes_only(self) -> None:
        self.guard_home.mkdir()
        (self.guard_home / "config.toml").write_text("not = [valid")
        self.assertEqual(2, hook("pre-tool", {})[0])
        self.assertEqual(1, hook("stop", {})[0])

    def test_unreadable_payload_fails_closed(self) -> None:
        code, _, err = hook("pre-tool", "{not json")
        self.assertEqual(2, code)
        self.assertIn("fail_closed", err)

    def test_fail_open_can_be_chosen(self) -> None:
        self.guard_home.mkdir()
        (self.guard_home / "config.toml").write_text("fail_closed = false\n")
        self.assertEqual(1, hook("pre-tool", "{not json")[0])

    def test_internal_error_in_stop_never_traps_the_agent(self) -> None:
        with mock.patch.object(guard, "on_stop", side_effect=RuntimeError("boom")):
            code, _, err = hook("stop", {})
        self.assertEqual(1, code)
        self.assertIn("boom", err)

    def test_session_start_prints_context_json(self) -> None:
        repo = self.repository()
        code, out, _ = hook("session-start", {"session_id": "a", "cwd": str(repo)})
        self.assertEqual(0, code)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.guard_home / "scratch" / repo.name), ctx)

    def test_installed_entry_point_runs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tidyguard", "hook", "pre-tool"],
            input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "tidyguard allow docs"}}
            ),
            capture_output=True,
            text=True,
            env=os.environ,
        )
        self.assertEqual(2, result.returncode)


class AllowCommandTests(GuardCase):
    def run_cli(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        old = os.getcwd()
        os.chdir(cwd or self.root)
        try:
            with captured() as (out, err):
                code = cli.main(list(args))
        finally:
            os.chdir(old)
        return code, out.getvalue() + err.getvalue()

    def test_grant_list_revoke(self) -> None:
        repo = self.repository()
        code, out = self.run_cli("allow", "docs", cwd=repo)
        self.assertEqual(0, code)
        self.assertIn(f"docs for {repo} (60 min)", out)
        self.assertIn("docs", self.run_cli("allow", "--list")[1])
        self.run_cli("allow", "--revoke")
        self.assertIn("no active grants", self.run_cli("allow", "--list")[1])

    def test_grant_outside_repo_needs_explicit_scope(self) -> None:
        self.assertEqual(1, self.run_cli("allow", "docs")[0])
        code, out = self.run_cli("allow", "automation", "*", "15")
        self.assertEqual(0, code)
        self.assertIn("(15 min)", out)

    def test_grants_file_is_private(self) -> None:
        self.run_cli("allow", "all", "*", "5")
        self.assertEqual(0o600, self.cfg.grants_file.stat().st_mode & 0o777)

    def test_check_dry_run(self) -> None:
        repo = self.repository()
        self.assertEqual(2, self.run_cli("check", "--cwd", str(repo), "--", "echo x > PLAN.md")[0])
        code, out = self.run_cli("check", "--cwd", str(repo), "--", "ls")
        self.assertEqual((0, "allow\n"), (code, out))

    def test_rules_and_status(self) -> None:
        code, out = self.run_cli("rules")
        self.assertEqual(0, code)
        self.assertIn("Left behind:", out)
        code, out = self.run_cli("status")
        self.assertEqual(0, code)
        self.assertIn("claude:  not installed", out)


class ConfigTests(GuardCase):
    def write(self, text: str) -> None:
        self.guard_home.mkdir(exist_ok=True)
        (self.guard_home / "config.toml").write_text(text)

    def test_defaults_without_file(self) -> None:
        cfg = load_config()
        self.assertTrue(cfg.fail_closed)
        self.assertIn(".md", cfg.doc_extensions)
        self.assertEqual(self.guard_home, cfg.home)

    def test_rejects_unknown_and_mistyped_settings(self) -> None:
        for text in (
            "colour = 1\n",
            "[docs]\nextensionz = []\n",
            "[stop]\nreport_cap = true\n",
            "[docs]\nspec_words = [1]\n",
            "fail_closed = 'yes'\n",
            "docs = 3\n",
            "[docs]\nexempt = ['(']\n",
        ):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(ConfigError):
                    load_config()

    def test_custom_policy(self) -> None:
        self.write('[docs]\nextensions = [".adoc"]\nspec_words = ["design"]\nblock_new = false\n')
        repo = self.repository()
        self.assertIsNone(self.pre("Write", {"file_path": str(repo / "NOTES.md")}, repo))
        self.assertIsNone(self.pre("Write", {"file_path": str(repo / "notes.adoc")}, repo))
        (repo / "design.adoc").write_text("x")
        self.assertIsNotNone(self.pre("Edit", {"file_path": str(repo / "design.adoc")}, repo))


class InstallTests(GuardCase):
    def test_claude_install_is_idempotent_and_keeps_other_hooks(self) -> None:
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir()
        other = {"type": "command", "command": "bash ~/update.sh"}
        settings.write_text(
            json.dumps({"model": "x", "hooks": {"SessionStart": [{"hooks": [other]}]}})
        )
        for _ in range(2):
            install.install(self.cfg, "claude", rules=False)
        data = json.loads(settings.read_text())
        self.assertEqual("x", data["model"])
        self.assertEqual(2, len(data["hooks"]["SessionStart"]))
        self.assertEqual(1, len(data["hooks"]["PreToolUse"]))
        self.assertIn("MultiEdit", data["hooks"]["PreToolUse"][0]["matcher"])
        self.assertTrue(settings.with_name("settings.json.tidyguard-bak").exists())
        self.assertTrue(install.installed("claude"))
        install.uninstall(self.cfg, "claude")
        data = json.loads(settings.read_text())
        self.assertEqual({"SessionStart": [{"hooks": [other]}]}, data["hooks"])
        self.assertFalse(install.installed("claude"))

    def test_codex_hooks_have_status_messages(self) -> None:
        install.install(self.cfg, "codex", rules=False)
        data = json.loads((self.home / ".codex" / "hooks.json").read_text())
        self.assertIn("apply_patch", data["hooks"]["PreToolUse"][0]["matcher"])
        self.assertIn("statusMessage", data["hooks"]["Stop"][0]["hooks"][0])

    def test_rules_are_linked_and_removed(self) -> None:
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir()
        claude_md.write_text("# Mine\n")
        install.install(self.cfg, "claude", rules=True)
        install.install(self.cfg, "claude", rules=True)
        text = claude_md.read_text()
        self.assertTrue(text.startswith("# Mine\n"))
        self.assertEqual(1, text.count(install.MARK_START))
        self.assertIn(f"@{self.guard_home / 'rules.md'}", text)
        install.install(self.cfg, "codex", rules=True)
        agents = (self.home / ".codex" / "AGENTS.md").read_text()
        self.assertIn("Left behind:", agents)
        install.uninstall(self.cfg, "claude")
        self.assertEqual("# Mine\n", claude_md.read_text())

    def test_edited_rules_are_not_overwritten(self) -> None:
        self.guard_home.mkdir()
        (self.guard_home / "rules.md").write_text("my rules\n")
        install.install(self.cfg, "codex", rules=True)
        self.assertIn("my rules", (self.home / ".codex" / "AGENTS.md").read_text())

    def test_symlinked_rule_file_is_refused_before_any_change(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        target = self.root / "shared.md"
        target.write_text("shared\n")
        (codex / "AGENTS.md").symlink_to(target)
        with self.assertRaises(install.InstallError):
            install.install(self.cfg, "codex", rules=True)
        self.assertFalse((codex / "hooks.json").exists())
        self.assertEqual("shared\n", target.read_text())

    def test_invalid_json_is_left_alone(self) -> None:
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{oops")
        with self.assertRaises(install.InstallError):
            install.install(self.cfg, "claude", rules=False)
        self.assertEqual("{oops", settings.read_text())

    def test_opencode_plugin_gets_the_command(self) -> None:
        install.install(self.cfg, "opencode", rules=False)
        plugin = (self.home / ".config" / "opencode" / "plugins" / "tidyguard.ts").read_text()
        self.assertNotIn("__TIDYGUARD_ARGV__", plugin)
        self.assertIn(json.dumps(install.guard_argv()), plugin)
        install.uninstall(self.cfg, "opencode")
        self.assertFalse(install.installed("opencode"))

    def test_installed_guard_protects_its_own_hooks(self) -> None:
        install.install(self.cfg, "claude", rules=False)
        settings = self.home / ".claude" / "settings.json"
        self.assertIsNotNone(
            self.pre("Write", {"file_path": str(settings), "content": "{}"}, self.root)
        )


if __name__ == "__main__":
    unittest.main()
