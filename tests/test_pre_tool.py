from __future__ import annotations

import json
import unittest

from support import GuardCase

from tidyguard import grants


class DocWriteTests(GuardCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.repository()

    def test_new_notes_in_repo_are_blocked(self) -> None:
        for name in ("NOTES.md", "docs/analysis.md", "status.txt", "x.rst", "a.mdx"):
            with self.subTest(name=name):
                msg = self.pre(
                    "Write", {"file_path": str(self.repo / name), "content": "x"}, self.repo
                )
                self.assertIsNotNone(msg)
                self.assertIn("new note or doc file", str(msg))

    def test_relative_paths_resolve_against_cwd(self) -> None:
        self.assertIsNotNone(self.pre("Write", {"file_path": "PLAN.md"}, self.repo))

    def test_editing_an_existing_ordinary_doc_is_allowed(self) -> None:
        (self.repo / "README.md").write_text("hi\n")
        self.assertIsNone(self.pre("Edit", {"file_path": str(self.repo / "README.md")}, self.repo))

    def test_spec_plan_handoff_files_are_read_only(self) -> None:
        for name in (
            "docs/spec.md",
            "plans/q3.md",
            "HANDOFF.md",
            "adr/0001-db.md",
            "my-roadmap.md",
        ):
            with self.subTest(name=name):
                path = self.repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
                msg = self.pre("Edit", {"file_path": str(path)}, self.repo)
                self.assertIn("spec/plan/handoff", str(msg))

    def test_spec_word_must_be_a_whole_segment(self) -> None:
        for name in ("planet.md", "inspection.md", "explanation.md"):
            with self.subTest(name=name):
                path = self.repo / name
                path.write_text("x\n")
                self.assertIsNone(self.pre("Edit", {"file_path": str(path)}, self.repo))

    def test_rule_files_are_read_only(self) -> None:
        for name in ("AGENTS.md", "sub/CLAUDE.md", "GEMINI.md"):
            with self.subTest(name=name):
                path = self.repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
                self.assertIn(
                    "agent rule file", str(self.pre("Edit", {"file_path": str(path)}, self.repo))
                )

    def test_code_and_files_outside_repos_are_allowed(self) -> None:
        self.assertIsNone(self.pre("Write", {"file_path": str(self.repo / "app.py")}, self.repo))
        outside = self.root / "loose" / "NOTES.md"
        self.assertIsNone(self.pre("Write", {"file_path": str(outside)}, self.root))

    def test_changesets_are_exempt(self) -> None:
        path = self.repo / ".changeset" / "brave-fox.md"
        self.assertIsNone(self.pre("Write", {"file_path": str(path)}, self.repo))

    def test_docs_grant_opens_the_current_repo_only(self) -> None:
        grants.add(self.cfg, "docs", str(self.repo), 5)
        self.assertIsNone(self.pre("Write", {"file_path": str(self.repo / "NOTES.md")}, self.repo))
        other = self.repository("other")
        self.assertIsNotNone(self.pre("Write", {"file_path": str(other / "NOTES.md")}, other))

    def test_global_grant_and_expiry(self) -> None:
        grants.add(self.cfg, "all", "*", 5)
        self.assertIsNone(self.pre("Write", {"file_path": str(self.repo / "NOTES.md")}, self.repo))
        data = json.loads(self.cfg.grants_file.read_text())
        data[0]["expires"] = 0
        self.cfg.grants_file.write_text(json.dumps(data))
        self.assertIsNotNone(
            self.pre("Write", {"file_path": str(self.repo / "NOTES.md")}, self.repo)
        )

    def test_corrupt_grants_file_grants_nothing(self) -> None:
        self.guard_home.mkdir(parents=True, exist_ok=True)
        self.cfg.grants_file.write_text('[{"kind": "all", "repo": "*", "expires": "soon"}, 3]')
        self.assertIsNotNone(
            self.pre("Write", {"file_path": str(self.repo / "NOTES.md")}, self.repo)
        )


class PatchTests(GuardCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.repository()

    def patch(self, body: str) -> str | None:
        return self.pre(
            "apply_patch", {"command": f"*** Begin Patch\n{body}\n*** End Patch"}, self.repo
        )

    def test_added_note_is_blocked(self) -> None:
        self.assertIsNotNone(self.patch("*** Add File: TODO.md\n+todo"))

    def test_update_of_spec_and_delete_of_spec_are_blocked(self) -> None:
        (self.repo / "spec.md").write_text("x\n")
        self.assertIsNotNone(self.patch("*** Update File: spec.md\n-x\n+y"))
        self.assertIsNotNone(self.patch("*** Delete File: spec.md"))

    def test_move_to_doc_name_is_blocked(self) -> None:
        (self.repo / "a.py").write_text("x\n")
        self.assertIsNotNone(self.patch("*** Update File: a.py\n*** Move to: PLAN.md"))

    def test_code_patch_is_allowed(self) -> None:
        self.assertIsNone(self.patch("*** Add File: src/app.py\n+print(1)"))

    def test_opencode_patch_text_field(self) -> None:
        msg = self.pre(
            "patch",
            {"patchText": "*** Begin Patch\n*** Add File: NOTES.md\n+x\n*** End Patch"},
            self.repo,
        )
        self.assertIsNotNone(msg)


class ShellTests(GuardCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.repository()

    def test_shell_doc_writes_are_blocked(self) -> None:
        for cmd in (
            "echo hi > NOTES.md",
            "echo hi >> 'docs/plan.md'",
            "printf x | tee SUMMARY.md",
            "printf x | tee -a SUMMARY.md",
            "touch a.py NOTES.md",
            "cp /tmp/x.md HANDOFF.md",
            "mv draft.txt docs/STATUS.md",
            "python3 -c \"open('PLAN.md', 'w').write('x')\"",
            "python3 -c \"from pathlib import Path; Path('PLAN.md').write_text('x')\"",
            "node -e \"require('fs').writeFileSync('PLAN.md', 'x')\"",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.bash(cmd, self.repo), cmd)

    def test_in_place_edit_of_spec_is_blocked(self) -> None:
        (self.repo / "spec.md").write_text("x\n")
        self.assertIsNotNone(self.bash("sed -i '' 's/x/y/' spec.md", self.repo))
        self.assertIsNotNone(self.bash("perl -pi -e 's/x/y/' spec.md", self.repo))

    def test_harmless_shell_commands_pass(self) -> None:
        (self.repo / "README.md").write_text("x\n")
        for cmd in (
            "cat README.md",
            "grep -r foo . > /dev/null",
            "ls 2>&1 | head",
            "echo '|| true' > out.log",
            "cp README.md /tmp/readme-copy.txt",
            "sed -i '' 's/x/y/' README.md",
            "git status --short",
            "pytest -q",
            "crontab -l",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.bash(cmd, self.repo), cmd)

    def test_shell_command_as_argv_list(self) -> None:
        msg = self.pre("shell", {"command": ["bash", "-lc", "echo x > NOTES.md"]}, self.repo)
        self.assertIsNotNone(msg)

    def test_automation_commands_are_blocked(self) -> None:
        for cmd in (
            "crontab /tmp/jobs",
            "launchctl load ~/Library/LaunchAgents/x.plist",
            "launchctl bootstrap gui/501 x.plist",
            "systemctl --user enable backup.timer",
            "cp hook .git/hooks/pre-commit",
            "git config core.hooksPath .githooks",
            "npx husky init",
        ):
            with self.subTest(cmd=cmd):
                self.assertIn("automation", str(self.bash(cmd, self.repo)))

    def test_automation_grant(self) -> None:
        grants.add(self.cfg, "automation", str(self.repo), 5)
        self.assertIsNone(self.bash("git config core.hooksPath .githooks", self.repo))

    def test_user_commands_are_reserved(self) -> None:
        for cmd in (
            "tidyguard allow docs",
            "cd x && tidyguard allow all '*' 600",
            "python3 -m tidyguard allow docs",
            "/usr/local/bin/tidyguard uninstall claude",
        ):
            with self.subTest(cmd=cmd):
                self.assertIn("user's commands", str(self.bash(cmd, self.repo)))
        self.assertIsNone(self.bash("tidyguard status", self.repo))


class AutomationFileTests(GuardCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.repository()

    def test_git_hooks_husky_and_new_workflows(self) -> None:
        for name in (".git/hooks/pre-push", ".husky/pre-commit", ".github/workflows/nightly.yml"):
            with self.subTest(name=name):
                self.assertRegex(
                    str(self.pre("Write", {"file_path": str(self.repo / name)}, self.repo)),
                    "automation|git hook",
                )

    def test_existing_workflow_may_be_edited(self) -> None:
        wf = self.repo / ".github" / "workflows" / "ci.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("on: push\n")
        self.assertIsNone(self.pre("Edit", {"file_path": str(wf)}, self.repo))

    def test_machine_job_dirs(self) -> None:
        for rel in (
            "Library/LaunchAgents/x.plist",
            ".config/systemd/user/x.timer",
            ".codex/automations/x",
        ):
            with self.subTest(rel=rel):
                self.assertIn(
                    "automation",
                    str(self.pre("Write", {"file_path": str(self.home / rel)}, self.root)),
                )

    def test_automation_switch_off(self) -> None:
        self.guard_home.mkdir()
        (self.guard_home / "config.toml").write_text("[automation]\nblock = false\n")
        self.assertIsNone(
            self.pre("Write", {"file_path": str(self.repo / ".husky/pre-commit")}, self.repo)
        )
        self.assertIsNone(self.bash("crontab /tmp/jobs", self.repo))


class SelfProtectionTests(GuardCase):
    def setUp(self) -> None:
        super().setUp()
        self.guard_home.mkdir()
        self.claude = self.home / ".claude" / "settings.json"
        self.claude.parent.mkdir()
        self.claude.write_text(
            json.dumps(
                {"model": "x", "hooks": {"Stop": [{"hooks": [{"command": "tidyguard hook stop"}]}]}}
            )
        )

    def test_guard_files_cannot_be_written(self) -> None:
        for name in ("grants.json", "config.toml", "rules.md", "sessions/s1.json"):
            with self.subTest(name=name):
                self.assertIn(
                    "belongs to tidyguard",
                    str(self.pre("Write", {"file_path": str(self.guard_home / name)}, self.root)),
                )

    def test_scratch_inside_guard_home_is_writable(self) -> None:
        path = self.guard_home / "scratch" / "repo" / "notes.md"
        self.assertIsNone(self.pre("Write", {"file_path": str(path)}, self.root))
        self.assertIsNone(self.bash(f"echo x > {path}", self.root))

    def test_shell_writes_to_guard_files_are_blocked(self) -> None:
        for cmd in (
            "echo '[]' > ~/.tidyguard/grants.json",
            'python3 -c "..." $HOME/.tidyguard/grants.json',
            f"rm {self.guard_home}/config.toml",
            "sed -i '' 's/tidyguard//' ~/.claude/settings.json",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.bash(cmd, self.root), cmd)
        self.assertIsNone(self.bash("cat ~/.tidyguard/grants.json", self.root))

    def test_hook_config_edits_that_keep_the_guard_are_allowed(self) -> None:
        msg = self.pre(
            "Edit",
            {
                "file_path": str(self.claude),
                "old_string": '"model": "x"',
                "new_string": '"model": "y"',
            },
            self.root,
        )
        self.assertIsNone(msg)

    def test_removing_the_guard_from_hook_config_is_blocked(self) -> None:
        cases = [
            (
                "Edit",
                {
                    "file_path": str(self.claude),
                    "old_string": "tidyguard hook stop",
                    "new_string": "true",
                },
            ),
            ("Write", {"file_path": str(self.claude), "content": "{}"}),
            (
                "MultiEdit",
                {
                    "file_path": str(self.claude),
                    "edits": [{"old_string": "tidyguard hook stop", "new_string": ""}],
                },
            ),
        ]
        for tool, ti in cases:
            with self.subTest(tool=tool):
                self.assertIn("remove tidyguard", str(self.pre(tool, ti, self.root)))

    def test_patch_removing_the_guard_is_blocked(self) -> None:
        patch = (
            f"*** Begin Patch\n*** Update File: {self.claude}\n"
            '-      "command": "tidyguard hook stop"\n+      "command": "true"\n*** End Patch'
        )
        self.assertIn(
            "remove tidyguard", str(self.pre("apply_patch", {"command": patch}, self.root))
        )
        delete = f"*** Begin Patch\n*** Delete File: {self.claude}\n*** End Patch"
        self.assertIn(
            "remove tidyguard", str(self.pre("apply_patch", {"command": delete}, self.root))
        )

    def test_hook_files_without_the_guard_are_not_special(self) -> None:
        self.claude.write_text("{}")
        self.assertIsNone(
            self.pre("Write", {"file_path": str(self.claude), "content": "{}"}, self.root)
        )
        self.assertIsNone(self.bash("sed -i '' 's/a/b/' ~/.claude/settings.json", self.root))


if __name__ == "__main__":
    unittest.main()
