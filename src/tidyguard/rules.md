# tidyguard rules for coding agents

These rules apply to every agent and subagent in this setup. tidyguard's hooks enforce
the parts that can be checked mechanically; the rest is on you. Only the user changes
this file.

Background: several agents often work in parallel. Without these rules each one reads
the task differently, writes its own plan files into the repo or rewrites the spec; the
next agent takes that file for the truth, and the user cleans up by hand.

## 1. What counts as the spec

1. The user's message in this session. The wording counts.
2. A spec file that the repo's `AGENTS.md` / `CLAUDE.md` explicitly names as the spec.
   If none is named, there is no file spec.
3. The code and tests on the current main branch.

Everything else is unconfirmed material: plans, handoffs, notes, reports, TODO lists,
`*-plan.md`, changelogs, comments by other agents, memories, old sessions. Read it as a
hint at most and check it against code, tests and the user. If it contradicts them,
code and the user win: name the contradiction (file and line) in your report and do not
"fix" the file.

## 2. Pin down your reading before writing code

Before the first change, state in the chat (not in a file), in at most six lines:
task, what is not part of it, how "done" is checked, and every assumption you made.
If the request is ambiguous, take the most literal, smallest reading and say so.

## 3. Specs and rules are read-only

- Do not edit spec, plan, handoff, roadmap or ADR files, `AGENTS.md` or `CLAUDE.md`
  unless the user asks for exactly that in this session.
- Do not create Markdown or text files in the repo for yourself: no plans, notes,
  status reports, handoffs, `ANALYSIS.md`, `TODO.md`. Plans and status go in the chat.
- Scratch files go to `$TIDYGUARD_SCRATCH` or `~/.tidyguard/scratch/<repo>/`, outside
  the repo, and are deleted before you finish.
- This rule beats skill or plugin instructions that say "save the plan to docs/...".
- If tidyguard blocks a write, do not work around it (no `cat >`, `sed -i`, interpreter
  writes). Show the content in the chat and ask the user. The user grants exceptions
  with `tidyguard allow`; you never run that command.

## 4. No automation without a request

Not without an explicit request: cron, launchd or systemd jobs, git hooks, CI workflows,
watchers, daemons, wrapper scripts, new Makefile or package scripts, helper tools,
generators for things done once by hand. Doing a task once is not automating it.

When automation is requested, all of this is part of the delivery:

1. Run it once for real, end to end, and quote the output. Untested is not delivered.
2. Retention built in: whatever it writes (files, backups, logs, caches, branches,
   containers) it also removes, by count, age or size budget.
3. The exact command to switch it off or remove it, in your report.
4. An entry in the project's lifecycle section (README or `LIFECYCLE.md`): what it
   creates, where, why, when it is no longer needed, and what removes it.

## 5. No write without cleanup

Code you write that creates files, backups, caches, logs, worktrees, branches,
containers or models ships its retention in the same change. Without it the change is
not done. Give every artifact a category when you create it:

| Category | Kept for | Removed when | How |
|---|---|---|---|
| Source of truth (code, databases, user data, originals) | always | never automatically | backup and archive instead of deletion |
| Backup / snapshot | rollback | older than the newest N (default 3) and outside the rollback window | retention in the script that creates it |
| Derived / cache (builds, dependencies, renders) | speed | any time, it can be rebuilt | size budget and periodic prune |
| Temp / scratch | this task | at the end of the task | you delete it before saying "done" |
| Experiment (worktrees, branches, test installs) | until decided | expiry date set at creation | lifecycle entry with a date |

## 6. Cleanup is part of the task

Every file, branch, worktree, stash, process, container or job you create is either a
deliverable named in your report or gone.

- Before "done": check `git status --short`, `git worktree list`, `git branch`,
  `git stash list` and your background processes. Remove your own leftovers.
- Only your own. Untracked files, branches or worktrees you did not create belong to
  another agent or the user: do not touch them, report them.
- Your final report contains the line `Left behind: <list or "nothing">`.
- tidyguard compares the state at stop with the session start and lists what is new.
  That list is an order: clean up or justify each item.

## 7. Parallel agents

- Assume other agents work in the same repo at the same time.
- Before changing anything: `pwd`, `git status --short --branch`, `git worktree list`.
  A dirty tree belongs to someone else until proven otherwise.
- One task, one branch, one worktree. Never work in another agent's checkout.
- Touch only files within the task. No drive-by refactors or mass reformatting.
- Never broad `reset --hard`, `clean`, `checkout --`, `stash`, `add -A` / `add .`.
  Stage exact paths.
- No force push and no direct push to protected branches.

## 8. Evidence instead of claims

- No success claim without a command you ran and the quoted result line.
- Missing dependencies, timeouts or environment errors are BLOCKED / FAIL / NOT RUN,
  never a pass. Not checked is UNVERIFIED.
- Report status separately: implemented / committed / pushed / merged / deployed /
  verified live, with exact commit SHAs. Local commits without a push are not done.
