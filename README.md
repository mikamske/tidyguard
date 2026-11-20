# tidyguard

**Your agent finishes the task and leaves no mess.**

tidyguard is a small, dependency-free hook for **Claude Code**, **Codex CLI** and **OpenCode**.
It stops coding agents from doing the things that make you clean up by hand after them:

- writing `PLAN.md`, `NOTES.md`, `HANDOFF.md`, `ANALYSIS.md` and friends into your repo,
- rewriting specs, ADRs, roadmaps and agent rule files (`AGENTS.md`, `CLAUDE.md`),
- quietly installing cron jobs, LaunchAgents, systemd timers, git hooks or CI workflows,
- leaving behind untracked files, branches, worktrees, stashes and scratch files.

When the agent wants to stop, tidyguard compares the machine with the session start and
hands it a list: every new artifact is either a deliverable named under `Left behind:`
in the final report, or the agent removes it now.

```text
$ claude -p "Create NOTES.md with hello, then debug.log with x"
...
The stop check flagged `debug.log`, which is a deliverable you asked for, so I'm keeping it.
Left behind: repo/debug.log (contains `x`)
`NOTES.md` was not created. tidyguard blocked it, and I did not work around the block.
To get it, run `tidyguard allow docs` in your own terminal and tell me.
```

*(real output from Claude Code 2.1.311 with tidyguard 0.1.0)*

## Why

Running several agents in parallel on one repo has a typical failure mode. One agent writes
its plan into `docs/plans/…md` and marks it "VERIFIED". The next agent reads that file as
the truth and builds on it, while a third one rewrites the spec to match its own reading.
Afterwards, you are left with dead branches, forgotten worktrees and a LaunchAgent nobody
asked for. Prompt rules alone don't hold, because skills and plugins tell agents the
opposite ("save the plan to docs/…"). tidyguard enforces the boring part deterministically,
in milliseconds, without an LLM and without network access.

## Install

```bash
uv tool install git+https://github.com/mikamske/tidyguard   # or: pipx install git+https://…
tidyguard install claude --rules     # ~/.claude/settings.json + ~/.claude/CLAUDE.md
tidyguard install codex --rules      # ~/.codex/hooks.json + ~/.codex/AGENTS.md, then review with /hooks
tidyguard install opencode --rules   # ~/.config/opencode/plugins/tidyguard.ts
tidyguard status
```

Requirements: Python 3.11+, git, macOS or Linux. Installing is idempotent. Each changed
file keeps one backup next to it (`*.tidyguard-bak`), and `tidyguard uninstall <client>`
removes exactly what was added.

`--rules` installs [the agent rules](src/tidyguard/rules.md) to `~/.tidyguard/rules.md` and
links them from the client's global instructions. That covers what a hook cannot check:
spec precedence, cleanup as part of the task, retention for anything that writes files,
evidence instead of claims, and safe behaviour next to parallel agents. Edit
`~/.tidyguard/rules.md` to make them yours; reinstalling never overwrites it.

## What it does

| Event | Check |
|---|---|
| Session start | Snapshot of untracked files, branches, worktrees, stashes, git hooks, scratch, LaunchAgents, systemd user units, Codex automations, crontab. Injects a short rule reminder with the scratch path. Prunes its own expired records. |
| Before a tool call | Blocks new doc/note files in repos, edits to spec/plan/handoff/ADR/roadmap files and rule files, new git hooks, husky hooks, new CI workflows and scheduled jobs. Covers file tools, `apply_patch`, and shell writes (`>`, `tee`, `cp`/`mv`/`touch`, `sed -i`, one-line Python/Node writes). Protects its own config, grants and rules, and refuses edits that would remove its hooks. |
| Stop | Lists everything new since the session start once and asks the agent to clean up or justify each item. A worker worktree is reviewed again after every resume. |

Code files, docs outside git repos, edits to existing ordinary docs (like `README.md`), edits to
existing workflows and `.changeset/*.md` stay allowed.

## When you do want the file

You grant exceptions from **your own terminal**. Agents are blocked from running these commands.

```bash
tidyguard allow docs              # 60 min: agents may write docs/specs in the current repo
tidyguard allow automation . 30   # 30 min: hooks, workflows, scheduled jobs
tidyguard allow all '*' 10        # everything, every repo, 10 min
tidyguard allow --list
tidyguard allow --revoke
```

`tidyguard check --cwd ~/code/app -- "echo x > PLAN.md"` dry-runs a shell command against the policy.

## Configuration

Everything is optional. Put this in `~/.tidyguard/config.toml` (or set `TIDYGUARD_HOME`):

```toml
fail_closed = true                 # internal error or broken config blocks writes (exit 2)

[docs]
extensions = [".md", ".mdx", ".txt", ".rst"]
rule_files = ["AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "GEMINI.md"]
spec_words = ["spec", "specs", "specification", "plan", "plans", "handoff", "handoffs",
              "roadmap", "prd", "adr", "adrs", "requirements"]
exempt = ['(^|/)\.changeset/']     # regexes on the repo-relative path
block_new = true                   # false: only specs and rule files are protected

[automation]
block = true

[stop]
review = true
report_cap = 30

[grants]
default_minutes = 60

[state]
max_age_days = 7

[protect]                          # hook configs whose tidyguard entries agents may not remove
hook_files = ["~/.claude/settings.json", "~/.claude/settings.local.json",
              "~/.codex/hooks.json", "~/.codex/config.toml",
              "~/.config/opencode/plugins/tidyguard.ts"]
```

Unknown keys and wrong types are errors. With `fail_closed = true`, a broken config blocks writes,
so a typo can't silently switch the guard off.

## Set it up with your agent

Copy one of these prompts into Claude Code, Codex or OpenCode.

**Install for me**

```text
Install tidyguard (https://github.com/mikamske/tidyguard) for this machine.
1. Read its README and src/tidyguard/rules.md first.
2. Install it with `uv tool install git+https://github.com/mikamske/tidyguard`
   (fall back to pipx if uv is missing).
3. Run `tidyguard install <client> --rules` for every agent CLI I have installed
   (check claude, codex, opencode on PATH).
4. Run `tidyguard status` and `tidyguard check --cwd . -- "echo x > PLAN.md"` and quote
   both outputs. The check must say deny.
Do not run `tidyguard allow` or `tidyguard uninstall`; those are mine.
```

**Fork and adapt it to my workflow**

```text
Fork https://github.com/mikamske/tidyguard to my GitHub account and clone the fork.
Read README.md, src/tidyguard/guard.py, src/tidyguard/rules.md and the tests.
Then interview me with at most five short questions about how I work: which files agents
may create, which paths are specs in my repos, which automation I allow, and which
language the messages should use. Adapt config defaults, rules.md and the messages to my
answers. Every behaviour change needs a test that fails before and passes after. Run
`python -m unittest discover -s tests`, ruff and mypy, quote the result lines, commit,
push to my fork, and install my fork with `uv tool install --force git+<my fork>`.
```

**Add a rule**

```text
In my tidyguard checkout, add this rule: <describe it, e.g. "block new files under
data/exports/ unless granted">. Follow the existing pattern in src/tidyguard/guard.py
(raise Blocked with a message that tells the agent what to do instead), add tests for
must-block and must-allow cases including tricky near-misses, run the full test suite,
ruff and mypy, and show me the diff before committing.
```

**Tune it for my team**

```text
Write a ~/.tidyguard/config.toml for our team. Our specs live in <path>, generated docs
in <path> are fine, and CI workflows are managed by <person/team>. Use only settings that
exist in tidyguard's README, then prove each choice with `tidyguard check` or a test
file write against a scratch repo, and delete the scratch repo afterwards.
```

## Limits

tidyguard prevents **accidents**. It is not a sandbox or a security boundary against a hostile
agent. A determined agent can write a file through a script, an unusual interpreter or a
variable-built command. The stop check is the safety net: whatever slipped through still
shows up as a new untracked file when the agent tries to finish. Other limits:

- Hooks only see what the client sends. A client that skips hooks (or an agent with the hook
  disabled) is not guarded.
- Codex runs new or changed hooks only after you review them with `/hooks`.
- The stop check reports, it never deletes. Ownership is the agent's job to establish, and
  age alone proves nothing.
- Windows is not supported yet.

## Lifecycle

What tidyguard writes, and what removes it:

| What | Where | Removed |
|---|---|---|
| Session snapshots | `~/.tidyguard/sessions/*.json` | automatically after `state.max_age_days` (default 7), at every session start |
| Grants | `~/.tidyguard/grants.json` | expired grants at every session start; `tidyguard allow --revoke` |
| Agent scratch | `~/.tidyguard/scratch/<repo>/` | by the agent at task end (the stop check reports leftovers); never by age, since it can hold live worktrees |
| Config and rules | `~/.tidyguard/config.toml`, `rules.md` | never automatically; they are yours |
| Hook entries | client config files | `tidyguard uninstall <client>` |
| Backups of changed client files | `<file>.tidyguard-bak` | one per file, replaced by the next change; delete by hand when you are happy |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
TIDYGUARD_PYTHON=.venv/bin/python node --test tests/opencode.test.mjs   # Node 22.18+
```

Related: [receiptgate](https://github.com/mikamske/receiptgate) gates deploys and merges on
command receipts bound to the exact code state. tidyguard keeps the workspace clean.

## License

Apache-2.0. Copyright 2026 Mika Mischke.
