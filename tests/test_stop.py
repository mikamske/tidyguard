from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from support import GuardCase

from tidyguard import guard


class StopReviewTests(GuardCase):
    def test_clean_session_says_nothing(self) -> None:
        repo = self.repository()
        self.start(repo)
        self.assertEqual("", self.stop(repo))

    def test_new_artifacts_are_listed_once(self) -> None:
        repo = self.repository()
        self.start(repo)
        (repo / "debug.log").write_text("x")
        self.git(repo, "branch", "experiment")
        message = self.stop(repo)
        self.assertIn("new untracked file: debug.log", message)
        self.assertIn("new branch: experiment", message)
        self.assertIn("Left behind:", message)
        self.assertEqual(
            "", self.stop(repo), "a reported artifact is acknowledged, no endless loop"
        )
        (repo / "second.log").write_text("x")
        message = self.stop(repo)
        self.assertIn("second.log", message)
        self.assertNotIn("debug.log", message)

    def test_preexisting_files_are_not_blamed_on_the_session(self) -> None:
        repo = self.repository()
        (repo / "old.log").write_text("x")
        self.start(repo)
        self.assertEqual("", self.stop(repo))

    def test_stash_and_scratch_leftovers_are_reported(self) -> None:
        repo = self.repository()
        (repo / "tracked.txt").write_text("a")
        self.git(repo, "add", "tracked.txt")
        self.git(
            repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "t"
        )
        self.start(repo)
        (repo / "tracked.txt").write_text("b")
        self.git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "stash", "-q")
        scratch = self.guard_home / "scratch" / repo.name
        scratch.mkdir(parents=True)
        (scratch / "draft.md").write_text("x")
        message = self.stop(repo)
        self.assertIn("new stash entry", message)
        self.assertIn("scratch file left over: draft.md", message)

    def test_new_git_hook_and_launch_agent_are_reported(self) -> None:
        repo = self.repository()
        self.start(repo)
        (repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\n")
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.example.job.plist").write_text("x")
        message = self.stop(repo)
        self.assertIn("new git hook: pre-push", message)
        self.assertIn("new LaunchAgent", message)

    def test_crontab_change_is_reported(self) -> None:
        repo = self.repository()
        self.start(repo)
        with mock.patch.object(guard, "crontab_hash", return_value="changed"):
            self.assertIn("crontab was changed", self.stop(repo))

    def test_moving_to_another_repo_compares_machine_state_only(self) -> None:
        repo = self.repository()
        other = self.repository("other")
        (other / "existing.log").write_text("x")
        self.start(repo)
        self.assertEqual("", self.stop(other))

    def test_stop_hook_active_and_disabled_review(self) -> None:
        repo = self.repository()
        self.start(repo)
        (repo / "x.log").write_text("x")
        self.assertEqual("", self.stop(repo, stop_hook_active=True))
        self.guard_home.joinpath("config.toml").write_text("[stop]\nreview = false\n")
        self.assertEqual("", self.stop(repo))

    def test_missing_baseline_records_one_instead_of_blocking(self) -> None:
        repo = self.repository()
        self.assertEqual("", self.stop(repo, session="late"))
        (repo / "x.log").write_text("x")
        self.assertIn("x.log", self.stop(repo, session="late"))

    def test_claude_and_codex_get_a_block_decision(self) -> None:
        repo = self.repository()
        self.start(repo)
        (repo / "x.log").write_text("x")
        out = guard.on_stop(self.cfg, {"session_id": "s1", "cwd": str(repo)})
        data = json.loads(out)
        self.assertEqual("block", data["decision"])
        self.assertIn("x.log", data["reason"])

    def test_report_is_capped(self) -> None:
        repo = self.repository()
        self.guard_home.mkdir()
        (self.guard_home / "config.toml").write_text("[stop]\nreport_cap = 2\n")
        self.start(repo)
        for i in range(5):
            (repo / f"f{i}.log").write_text("x")
        self.assertIn("and 3 more", self.stop(repo))


class WorktreeTests(GuardCase):
    def worker(self) -> tuple[Path, Path]:
        repo = self.repository()
        worker = self.root / "worker"
        self.git(repo, "worktree", "add", "-q", "-b", "task", str(worker))
        return repo, worker

    def test_worker_checkout_created_before_session_is_reported(self) -> None:
        _, worker = self.worker()
        self.start(worker)
        message = self.stop(worker)
        self.assertIn(str(worker), message)
        self.assertIn("task", message)

    def test_primary_checkout_is_not_mistaken_for_a_worker(self) -> None:
        repo, _ = self.worker()
        self.start(repo)
        self.assertEqual("", self.stop(repo))

    def test_reporting_never_deletes_the_worker(self) -> None:
        _, worker = self.worker()
        self.start(worker)
        self.stop(worker)
        self.assertTrue(worker.is_dir())

    def test_resumed_worker_is_reviewed_again(self) -> None:
        _, worker = self.worker()
        self.start(worker)
        self.assertIn(str(worker), self.stop(worker))
        self.assertEqual("", self.stop(worker))
        self.start(worker)
        self.assertIn(str(worker), self.stop(worker))

    def test_detached_worker(self) -> None:
        repo = self.repository()
        worker = self.root / "detached"
        self.git(repo, "worktree", "add", "-q", "--detach", str(worker))
        self.start(worker)
        self.assertIn("detached HEAD", self.stop(worker))

    def test_preview_never_acknowledges(self) -> None:
        _, worker = self.worker()
        self.start(worker)
        before = guard.state_path(self.cfg, "s1").read_text()
        for _ in range(2):
            self.assertIn(str(worker), self.stop(worker, preview=True))
            self.assertEqual(before, guard.state_path(self.cfg, "s1").read_text())


class RetentionTests(GuardCase):
    def test_old_session_records_expire(self) -> None:
        state = self.cfg.state_dir
        state.mkdir(parents=True)
        stale, fresh = state / "old.json", state / "new.json"
        stale.write_text("{}")
        fresh.write_text("{}")
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        guard.prune(self.cfg)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_scratch_is_never_deleted_by_age(self) -> None:
        scratch = self.cfg.scratch_root / "repo"
        scratch.mkdir(parents=True)
        artifact = scratch / "review.patch"
        artifact.write_text("unmerged work\n")
        old = time.time() - 30 * 86400
        os.utime(artifact, (old, old))
        guard.prune(self.cfg)
        self.assertTrue(artifact.exists())

    def test_expired_grants_are_pruned_at_session_start(self) -> None:
        self.guard_home.mkdir()
        self.cfg.grants_file.write_text(json.dumps([{"kind": "docs", "repo": "*", "expires": 1}]))
        repo = self.repository()
        out = self.start(repo)
        self.assertEqual([], json.loads(self.cfg.grants_file.read_text()))
        self.assertIn(
            "tidyguard is active", json.loads(out)["hookSpecificOutput"]["additionalContext"]
        )


if __name__ == "__main__":
    unittest.main()
