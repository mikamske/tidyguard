# Security policy

tidyguard is a workflow aid that prevents accidental mess. It is not a sandbox, an
authentication system or a security boundary against a hostile agent.

Report vulnerabilities through GitHub's private vulnerability reporting on this repository.
Use a minimal toy repository and never attach real secrets or private data.

## Boundaries

- Shell checks are pattern-based heuristics. Scripts, unusual interpreters, `eval` and
  variable-built commands can write files the pre-tool check does not see. The stop check
  catches the result as a new untracked file, but only after the fact.
- The guard runs as the same user as the agent. It refuses agent writes to its own config,
  grants, rules and hook entries, but a process outside the hooked tools can still change them.
- Hooks run only when the client calls them. Clients that skip hooks are not covered.
- The stop check reports artifacts and never deletes anything.

Useful reports: a reproducible way for an agent to create a blocked file through a
*file tool* (not the shell), to disable or bypass the hooks through a guarded tool call, or
to make the stop check miss an artifact it should list.
