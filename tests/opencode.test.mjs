// Runs the OpenCode adapter against a fake plugin context (Node >= 22.18 strips TS types).
// Usage: TIDYGUARD_PYTHON=.venv/bin/python node --test tests/opencode.test.mjs
import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import { pathToFileURL } from "node:url"

const python = process.env.TIDYGUARD_PYTHON || "python3"

async function loadPlugin(dir) {
  const source = readFileSync(new URL("../src/tidyguard/opencode.ts", import.meta.url), "utf8")
  const file = join(dir, "tidyguard.ts")
  writeFileSync(file, source.replace("__TIDYGUARD_ARGV__", JSON.stringify([python, "-m", "tidyguard"])))
  return (await import(pathToFileURL(file).href)).default
}

async function setup(plugin, directory) {
  const hooks = new Map()
  const registration = { dispose: async () => {} }
  const reg = (prefix) => async (name, handler) => {
    hooks.set(`${prefix}.${name}`, handler)
    return registration
  }
  const cleanup = await plugin.setup({
    location: { directory },
    session: { get: async () => ({ location: { directory } }), hook: reg("session") },
    tool: { hook: reg("tool") },
    shell: { hook: reg("shell") },
  })
  return { hooks, cleanup }
}

test("blocks writes, reports leftovers before each model call, sets scratch", async (t) => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "tidyguard-opencode-")))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  process.env.TIDYGUARD_HOME = join(root, "guard-home")
  const repo = join(root, "repo")
  const worker = join(root, "worker")
  mkdirSync(repo)
  const git = (...args) => execFileSync("git", ["-C", repo, ...args], { stdio: "pipe" })
  git("init", "-q", "-b", "main")
  git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "init")
  git("worktree", "add", "-q", "-b", "task", worker)

  const { hooks, cleanup } = await setup(await loadPlugin(root), worker)
  const sessionID = "opencode-test"

  await assert.rejects(
    hooks.get("tool.execute.before")({ sessionID, tool: "write", input: { filePath: join(worker, "NOTES.md") } }),
    /new note or doc file/,
  )
  await hooks.get("tool.execute.before")({ sessionID, tool: "write", input: { filePath: join(worker, "app.py") } })

  for (let call = 0; call < 2; call++) {
    const input = { sessionID, system: [] }
    await hooks.get("session.context")(input)
    assert.ok(
      input.system.some((p) => p.type === "text" && p.text.includes(worker) && p.text.includes("Left behind:")),
      "an unfinished checkout stays visible in every model call",
    )
  }

  const shell = { cwd: worker, env: {} }
  hooks.get("shell.create.before")(shell)
  assert.equal(shell.env.TIDYGUARD_SCRATCH, join(root, "guard-home", "scratch", "worker"))
  await cleanup()
})
