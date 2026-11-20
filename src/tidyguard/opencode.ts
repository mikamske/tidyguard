// tidyguard adapter for the OpenCode plugin API v2 (default export { id, setup }).
// Thin adapter: all policy lives in the tidyguard Python package, shared with the
// Claude Code and Codex hooks. `tidyguard install opencode` fills in GUARD_ARGV.
import type { Plugin } from "@opencode/plugin"
import { spawnSync } from "node:child_process"
import { homedir } from "node:os"
import { basename, join } from "node:path"

const GUARD_ARGV: string[] = __TIDYGUARD_ARGV__

function runGuard(event: string, payload: Record<string, unknown>) {
  const r = spawnSync(GUARD_ARGV[0], [...GUARD_ARGV.slice(1), "hook", event], {
    input: JSON.stringify({ ...payload, _client: "opencode" }),
    encoding: "utf8",
    timeout: 20_000,
  })
  if (r.error || r.status === null) {
    throw new Error(`tidyguard could not run: ${r.error?.message || r.signal}`)
  }
  return { code: r.status, stdout: (r.stdout ?? "").trim(), stderr: (r.stderr ?? "").trim() }
}

function scratchRoot() {
  return join(process.env.TIDYGUARD_HOME || join(homedir(), ".tidyguard"), "scratch")
}

const plugin: Plugin.Plugin = {
  id: "tidyguard",
  setup: async (ctx) => {
    const directory = ctx.location.directory
    const sessionDir = new Map<string, string>()

    const dirFor = (sessionID?: string) => (sessionID && sessionDir.get(sessionID)) || directory

    const start = (sessionID: string, cwd: string) => {
      if (sessionDir.has(sessionID)) return
      const result = runGuard("session-start", { session_id: sessionID, cwd })
      if (result.code !== 0) throw new Error(result.stderr || "tidyguard: session start failed")
      sessionDir.set(sessionID, cwd)
    }

    // Sessions resumed after a restart never emit session.created in this process.
    const ensure = async (sessionID: string) => {
      if (sessionDir.has(sessionID)) return
      const info = await ctx.session.get({ sessionID })
      start(sessionID, info?.location?.directory || directory)
    }

    const regs = await Promise.all([
      ctx.session.hook("prompt", async (input) => {
        await ensure(input.sessionID)
      }),

      // The standalone CLI can exit before an idle-event follow-up is admitted, so the
      // cleanup check runs synchronously before every model request, for subagents too.
      ctx.session.hook("context", async (input) => {
        await ensure(input.sessionID)
        const res = runGuard("stop", {
          session_id: input.sessionID,
          cwd: dirFor(input.sessionID),
          preview: true,
        })
        if (res.code !== 0) throw new Error(res.stderr || "tidyguard: cleanup check failed")
        if (res.stdout) input.system.push({ type: "text", text: res.stdout })
      }),

      ctx.tool.hook("execute.before", async (input) => {
        await ensure(input.sessionID)
        const res = runGuard("pre-tool", {
          session_id: input.sessionID,
          cwd: dirFor(input.sessionID),
          tool_name: input.tool,
          tool_input: input.input,
        })
        if (res.code !== 0) throw new Error(res.stderr || "tidyguard: check failed")
      }),

      ctx.shell.hook("create.before", (input) => {
        const cwd = input.cwd || directory
        const top = spawnSync("git", ["-C", cwd, "rev-parse", "--show-toplevel"], { encoding: "utf8" })
        const name = top.status === 0 ? basename(top.stdout.trim()) : "_global"
        input.env.TIDYGUARD_SCRATCH = join(scratchRoot(), name)
      }),
    ])

    return async () => {
      await Promise.all(regs.map((r) => r.dispose()))
    }
  },
}

export default plugin
