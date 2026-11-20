# Contributing

Issues and pull requests are welcome.

- Keep the core dependency-free and fast: every hook call runs on each agent tool call.
- Each behaviour change comes with tests for both sides (must block, must allow), including
  near-misses such as `planet.md` next to `plan.md`.
- Run before pushing:

  ```bash
  .venv/bin/python -m unittest discover -s tests
  .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
  TIDYGUARD_PYTHON=.venv/bin/python node --test tests/opencode.test.mjs
  ```

- Payload formats differ between clients. If you add support for a client or a new tool name,
  add a test with a real payload shape from that client.
