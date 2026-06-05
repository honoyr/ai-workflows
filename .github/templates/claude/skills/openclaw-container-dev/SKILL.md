---
name: openclaw-container-dev
description: Use when working in honoyr/openclaw-deploy (Azure Container Instance running OpenClaw). Covers schema validation, az exec quirks, rebuild discipline, plugin pinning, self-healing patterns, and the smoke-test contract. Apply for any change to docker/, scripts/, or config/.
---

# OpenClaw Container Development

The OpenClaw deploy ships as an ACI container fronted by Cloudflare Tunnel. State lives on the container's ephemeral disk and snapshots to Azure Blob every 30 min. This skill captures the patterns that have to be followed to avoid the failure modes we've already hit in production.

## Rule #0 — Check the official docs FIRST

Before implementing any feature, fix, or workaround, **read [docs.openclaw.ai](https://docs.openclaw.ai/)** for the relevant area. OpenClaw ships a lot of native capabilities that are easy to miss and almost always better than rolling our own:

- **CLI surface:** `docs.openclaw.ai/cli/<cmd>` (e.g. `/cli/cron`, `/cli/mcp`, `/cli/devices`, `/cli/configure`, `/cli/doctor`). The CLI itself prints its docs URL in `--help` output — follow that link before scripting around the command.
- **Config schema:** `docs.openclaw.ai/config/*` — authoritative field reference. Cross-check against the in-container Zod schema (see §2 below) when a doc field doesn't match the deployed version.
- **Plugins:** `docs.openclaw.ai/plugins/*` — each plugin's setup, required config keys, env vars, and limitations. Check the version compatibility note before pinning.
- **Channels / Gateway / Skills:** `docs.openclaw.ai/channels/*`, `/gateway/*`, `/skills/*` — covers webhook patterns, channel routing, skill format, and the agent harness.
- **Release notes / changelog:** check before bumping `UPSTREAM_PIN` — breaking schema changes and plugin-API floor bumps are called out there.

Workflow on every new request:
1. **Search the docs** for the exact feature (use `web_fetch` against `https://docs.openclaw.ai/...`). Note any CLI command, config key, or plugin that already solves the problem.
2. **Verify against the deployed version** — `openclaw <subcmd> --help` inside the container shows the actual CLI surface; the docs may describe a newer or older API.
3. **Only build custom glue** when the native path genuinely doesn't exist or is documented as not applicable. Prefer `openclaw mcp set` over hand-editing `openclaw.json`. Prefer `openclaw cron add` over writing a cron file directly. Prefer `openclaw configure --section …` for guided changes over jq surgery.
4. **Cite the doc URL** in commit messages so the next session can verify the source.

If the docs and the deployed version disagree, the deployed version wins (we ran into 2026.5.x → 2026.6.x format changes mid-session). Note the divergence in the commit message and consider opening an upstream issue.

## Always-true facts

- Container runs as user `node` (HOME=`/home/node`). NOT root.
- Workspace is `/var/openclaw-state` (was `/mnt/openclaw-workspace` pre-May-2026; compat symlinks make legacy paths still resolve).
- `~/.openclaw/openclaw.json` is **rewritten unconditionally from `OPENCLAW_CONFIG_B64`** on every boot. Anything mutated at runtime via `openclaw mcp set`, `openclaw cron add`, etc. is lost on restart unless re-applied by `docker/openclaw-init.sh` or `scripts/deploy.sh`.
- State subdirs (`agents/`, `tasks/`, `cron/`, `credentials/`, etc.) symlink into `/var/openclaw-state/_state/`. Identity and openclaw.json must be local files (FsSafeError rejects symlinks for those).
- Secrets live in `scripts/env.sh` (gitignored). Never commit them.

## The recurring failure modes (read these BEFORE editing)

### 1. `az container exec` mangles shell metacharacters

`az container exec --exec-command "…"` re-splits the command client-side. These reliably break:

| Bad | What happens | Workaround |
|---|---|---|
| `bash -c 'cmd1; cmd2'` | unbalanced quote | split into separate `az exec` calls |
| `cmd1 && cmd2` | second command dropped | separate calls |
| `node -e "code with (parens)"` | "unexpected EOF" | base64-encode |
| `i > 0` inside any string | `>` stripped → syntax err | use `i !== -1` |
| `<REDACTED>` inside any string | `<` and `>` stripped | use `REDACTED` |
| `=>` arrow functions in inline JS | mangled | use `function(){}` |
| any payload >5000 chars | `InvalidCommandLength` | upload to blob and bootstrap |

**Standard pattern for non-trivial container probes:**

```bash
SCRIPT='var sp=require("child_process").spawnSync; …'
B64=$(echo -n "$SCRIPT" | base64)
az container exec ... --exec-command "node -e eval(Buffer.from('$B64','base64').toString())"
```

For payloads >5KB, write the script to `openclaw-attachments/dryrun/<name>.sh` via the SAS URL and bootstrap with a small `node -e` that curl-fetches + `bash`es it.

### 2. Config edits MUST be validated against the live Zod schema BEFORE deploy

A single unknown field in `OPENCLAW_CONFIG_B64` (the schema is `.strict()`) crashes the container on boot with `ExitCode 1` and **no logs at all** → CrashLoopBackOff.

Before changing `config/config.json` or any `deploy.sh` jq-injection, validate:

```bash
# Build candidate (just the subtree being changed)
PARTIAL=$(jq -n --arg k "$GEMINI_API_KEY" '{ realtime: { provider: "google", … } }')
B64=$(printf '%s' "$PARTIAL" | base64 | tr -d '\n')

# Merge into the live config inside the container and validate against the
# live Zod schema. OpenClawSchema is exported as `t` from this module.
VALIDATE='var fs=require("fs");
var sub=JSON.parse(Buffer.from(process.argv[1],"base64").toString());
var cfg=JSON.parse(fs.readFileSync("/home/node/.openclaw/openclaw.json","utf8"));
cfg.talk=sub;  // or whatever subtree
var mod=require("/app/dist/zod-schema-CNsDPNyw.js");
try { mod.t.parse(cfg); console.log("VALID"); }
catch(e){ console.log("INVALID"); console.log(JSON.stringify(e.issues||e.errors,null,2)); process.exit(1); }'
V_B64=$(printf '%s' "$VALIDATE" | base64 | tr -d '\n')
az container exec ... --exec-command "node -e eval(Buffer.from('$V_B64','base64').toString()) $B64"
```

Only redeploy after seeing `VALID`. The schema filename hash (`CNsDPNyw`) changes per upstream version — re-grep for it with `grep -l "OpenClawSchema" /app/dist/*.js` when needed.

### 3. OpenClaw version bumps silently break stored state

The 2026.5.x → 2026.6.x upgrade rewrote the cron job format. `openclaw cron list` returned 0 jobs even with `_state/cron/jobs.json` populated. Worse: the CLI's own `doctor --fix` hint renames `jobs.json` → `jobs.json.migrated` **without repopulating** the new store.

**Mitigation already in place:** `scripts/deploy.sh` re-runs `scripts/install-photo-cron.sh` after every deploy. If you change `UPSTREAM_PIN`, audit any other persistent state (MCP servers, devices, identity) for similar silent format migrations and ensure they have an idempotent re-applier in `openclaw-init.sh`.

### 4. Plugin install pins matter

- `brave-plugin` requires plugin API ≥ host version. Bumping `UPSTREAM_PIN` may require updating other plugins in lockstep.
- `@openclaw/memory-lancedb` 2026.6.x ships broken peer deps (apache-arrow@21 vs lancedb@0.30 needs ≤18.1.0). Currently pinned to `2026.5.7` in `docker/Dockerfile`.
- `ENV NPM_CONFIG_LEGACY_PEER_DEPS=true` is set in the Dockerfile as a safety net for similar peer-dep issues.

Before bumping `UPSTREAM_PIN`, check `https://registry.npmjs.org/@openclaw/<plugin>/<version>` for each plugin's `.dependencies` and `.openclaw.compat.pluginApi`.

### 5. Snapshot freshness needs a stamp file

`rclone sync` only re-uploads changed files. Static files (`AGENTS.md`, `IDENTITY.md`) are useless freshness signals. `docker/state-snapshot.sh` writes `latest/_snapshot-stamp` (current ISO timestamp) on every successful sync; smoke probe #25 reads its `Last-Modified` header.

### 6. Stale-path errors surface in Telegram

After the SMB→Blob migration, agent skills and user vault docs still reference `/mnt/openclaw-workspace` and `/root/.openclaw`. Mitigation:
- Boot-time compat symlinks in `openclaw-init.sh` (`/mnt/openclaw-workspace → /var/openclaw-state`, `/root/.openclaw → /home/node/.openclaw`).
- Smoke probe #22 verifies symlinks resolve to canonical paths.

When changing any infrastructure path again, audit ALL refs in `docker/skills/`, `docker/`, the user's vault repo, and the agent's cached memory — then add a new compat symlink as a safety net.

## Rebuild discipline

```
docker/* changes               → bump WRAPPER_REV in scripts/env.sh, ./scripts/build-image.sh, then deploy
scripts/* (deploy only)        → no rebuild, just ./scripts/deploy.sh
config/config.json changes     → no rebuild, just ./scripts/deploy.sh (but validate first!)
upstream pin change only       → no WRAPPER_REV bump (it tracks docker/ changes), but rebuild
```

`WRAPPER_REV` is the suffix in the image tag `custom-<UPSTREAM_PIN>-<WRAPPER_REV>`. Bumping it forces ACR to produce a new tag, which forces ACI to repull.

## Secret-wiring pattern

Three layers, all required:

1. `scripts/env.sh`: `export FOO="…"` (gitignored).
2. `scripts/deploy.sh`: add `FOO="${FOO:-}" \` to the `--secure-environment-variables` block.
3. Consumer:
   - For files written by openclaw at runtime (cron, MCP): re-apply idempotently in `docker/openclaw-init.sh` or post-deploy in `deploy.sh`.
   - For config keys (`talk.realtime`, etc.): jq-inject into `EFFECTIVE_CONFIG` in `deploy.sh` before base64-encoding.

Never bake secrets into `OPENCLAW_CONFIG_B64` from `config/config.json` (it's committed source).

## Smoke-test contract

`./scripts/smoke-prod.sh` runs 27 probes (Tier 3). When you add a new piece of infrastructure that can silently break:
- Add a probe — see existing probes #17, #22-#27 as templates.
- Hard-fail (`log_fail`) for class-A regressions (silent loss, data corruption). Warn-only (`log_warn`) for transient/cosmetic.
- Each probe must have a header comment citing the historical incident it prevents.
- Re-run smoke after any deploy; commit only when all probes pass (probe count varies — match the current TAP plan).

## Telegram errors are canaries

These exact messages indicate underlying infrastructure issues — investigate, do not dismiss:

| Telegram message | Likely cause | Smoke probe |
|---|---|---|
| `check git status (in /var/openclaw-state) failed` | sentinel `.git` missing | #24 |
| `Realtime WebRTC setup failed (500)` | `.talk` config missing/invalid | (config) |
| `print lines N-M from /mnt/openclaw-workspace/... failed` | compat symlinks gone | #22 |
| `search ... in memory ... failed` | lancedb plugin not registered or cwd wrong | #7 + investigate |
| `Something went wrong while processing your request.` | agent dispatch failure, often EACCES or harness-missing | #19, #20, #21 |

## Investigation order for a "Telegram is broken" report

1. `az container show … --query "containers[0].instanceView.currentState.state"` — is it Running?
2. `az container logs …` — anything post-boot?
3. `./scripts/smoke-prod.sh` — what's red?
4. Read the failing probe's source for the exact diagnostic command.
5. Fix the root cause, NOT the symptom (e.g., re-register cron, don't disable the probe).
6. Add or strengthen a smoke probe so the next instance is caught earlier.

## Session hygiene (keep context fresh)

- Read this skill at the start of any new openclaw-deploy session BEFORE running commands.
- For investigations: use `task` with `agent_type: explore` rather than grepping the dist yourself; it keeps your main context lean.
- After validating any config change, document the validated JSON in the commit message so the next session has the working shape to copy.
- `/compact` only delays the problem. Long sessions reward re-reading this skill and the `CLAUDE.md` in openclaw-deploy over reasoning from scratch.
- Prefer one focused commit per fix over a sprawling session. Push and start a fresh session for the next problem.
