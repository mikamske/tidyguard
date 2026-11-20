# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the
project uses Semantic Versioning.

## [0.1.0] - 2026-11-20

### Added

- Hooks for Claude Code, Codex CLI and OpenCode with one shared policy core.
- Pre-tool checks for notes, specs, rule files, automation and the guard's own files,
  including `apply_patch` envelopes and common shell write forms.
- Stop check that lists new untracked files, branches, worktrees, stashes, git hooks,
  scratch leftovers and scheduled jobs since the session start.
- Time-limited grants (`tidyguard allow`), dry-run (`tidyguard check`), `status`, `rules`.
- Idempotent `install` / `uninstall` with backups, and an optional agent rule pack.
