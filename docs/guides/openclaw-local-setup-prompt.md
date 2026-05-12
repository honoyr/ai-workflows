# Claude Code prompt — Set up OpenClaw locally on Mac Pro 2019 with Ollama

Copy everything between the `===` lines into Claude Code (in a fresh session, ideally with the working directory set to where you want the new `openclaw-local` repo to live, e.g., `~/repos/`).

The prompt is self-contained: it gives Claude the context, the goal, the constraints, the architecture decisions, and the phased plan so it can drive the setup end-to-end while asking you concrete questions only when it actually needs a decision.

---

============================================================
# Task: Set up OpenClaw locally on a Mac Pro 2019 with Ollama as the local-inference daemon, replacing my Azure Container Instance deployment

## Read these first (in order, all required)

1. `~/repos/ai-workflows/docs/guides/openclaw-local-mac-pro-feasibility.md` — the full design doc; **this is the source of truth for architecture decisions**. Pay attention to:
   - Section 1 (hardware reality — Intel Xeon + AMD W6800X-class, 128 GB RAM, **no MLX, no CUDA**)
   - Section 3 (the three architecture tiers — we're starting at **Tier 0 hybrid**)
   - Section 5 (oMLX alternatives — **decision: Ollama**)
   - Section 6 (performance optimizations — NVMe, tmpfs scope, `--mlock`)
   - Section 9 (phased setup plan)
   - Section 10 (`openclaw-local` is a **new repo**, not a fork of `openclaw-deploy`)
2. `~/repos/ai-workflows/docs/guides/openclaw-azure-setup-guide.md` — the current Azure setup; mine it for plugin install order, config schema, channel routing patterns we want to carry over.
3. `~/repos/openclaw-deploy/` — existing personal deploy repo for the container. Source of truth for current `openclaw.json`, agents, secret names, channel config. **Do not modify this repo** — read-only reference.
4. `https://docs.openclaw.ai/install` — official native install docs.
5. `https://docs.openclaw.ai/providers/openai` — we'll configure Ollama via OpenAI-compatible provider entry.

## Hard constraints (do not negotiate without asking me)

- **OS target:** Dedicated Linux on the Mac Pro (Ubuntu 24.04 LTS recommended; Fedora 41 acceptable if there's a strong reason). Not macOS. Not a VM.
- **Architecture tier:** **Tier 0 hybrid** to start — OpenClaw runs locally; cloud `gpt-5.5` via Codex OAuth stays as the primary LLM. Ollama runs locally for embeddings + small-model tasks (photo OCR, summarization first-pass, drafts).
- **Inference daemon:** **Ollama** (not MLC LLM, not raw llama.cpp, not LM Studio). Decision is final per Section 5 of the feasibility doc.
- **Repo:** Create a **new** repo `openclaw-local` (in `~/repos/`). Do not fork or modify `openclaw-deploy` or `openclaw-azure-template`. Borrow patterns, don't share state.
- **Secrets:** Never commit secrets. Use `.env` (gitignored) + a `.env.example` template. The Azure Key Vault references stay in the container repo; local uses plain env files (encrypted backups out of scope for now).
- **OpenClaw install:** Use the **official installer** (`curl -fsSL https://openclaw.ai/install.sh | bash`) + `openclaw onboard --install-daemon` for systemd user service. **Do not** try to replicate the container's plugin layout manually.
- **No code changes to OpenClaw itself** — config only.
- **Parallel running:** The Azure container stays up and primary during the entire bring-up. We only decommission Azure after the local box has passed a smoke harness in parallel for at least a week. **Do not** point Telegram or any production webhook at the local box until I explicitly approve.

## What "done" looks like for this task

Local Mac Pro is running OpenClaw as a systemd user service, reachable on `localhost:<port>`, with:

1. Linux base hardened (NVMe with XFS+noatime, `openclaw` user, ufw, Cloudflare Tunnel for inbound, automatic security updates).
2. Ollama running as a systemd service with `OLLAMA_KEEP_ALIVE=24h`, at least one model pulled (`llama3.2:3b` as smoke test; the bigger embeddings/vision models follow once basic LLM path works).
3. OpenClaw native install via the official installer; plugins installed: `@openclaw/codex`, `@openclaw/brave`, `@openclaw/memory-lancedb` (mirror the Dockerfile install line from `openclaw-deploy`).
4. `~/.openclaw/openclaw.json` configured with:
   - Codex OAuth carried over from the container box (we'll handle the actual token transfer manually — you set up the structure, I paste the token).
   - **Ollama provider entry** as a secondary OpenAI-compatible provider (`baseUrl: http://127.0.0.1:11434/v1`).
   - The Foundry AI provider entries we use today, also OpenAI-compatible.
   - A model routing config that keeps gpt-5.5 as primary for chat/agents and routes embeddings + small/fast paths to Ollama.
5. Channel config initially **disabled** for Telegram and any other inbound — local box is admin-only via local UI until I flip it.
6. A smoke harness script (`scripts/smoke-local.sh`) ported from `openclaw-azure-template` adjusted for local — exits non-zero on any provider/channel/agent failure.
7. A new `openclaw-local` repo committed and pushed to GitHub (private), with: README, `.env.example`, install scripts (idempotent), systemd unit templates, smoke script, restore-from-backup script, and the decommission-Azure checklist.

## Phased plan (execute in order; checkpoint with me at each "STOP")

### Phase 0 — Pre-flight (no machine changes yet)
- Read all files listed above.
- Inventory current container config: list providers, channels, agents, plugins, env vars from `~/repos/openclaw-deploy`. Produce a one-page summary in `openclaw-local/docs/current-container-inventory.md`.
- Produce a target config diff: what we keep, what we drop, what we add (Ollama provider). Save as `openclaw-local/docs/target-config-diff.md`.
- **STOP and show me the inventory + diff before doing anything else.**

### Phase 1 — Repo scaffolding (no Mac Pro touched yet)
- Create `~/repos/openclaw-local` (local-only; we'll push to GitHub at end of phase).
- Layout:
  ```
  openclaw-local/
    README.md
    .env.example
    .gitignore           # ignore .env, *.bak, /state/, /logs/
    docs/
    scripts/
      00-base-linux.sh           # idempotent, runs as root via sudo
      01-install-ollama.sh
      02-install-openclaw.sh
      03-write-config.sh         # renders openclaw.json from template + .env
      04-systemd-user-units.sh
      smoke-local.sh
      backup.sh                  # rsync ~/.openclaw -> external NVMe
      restore.sh
    config/
      openclaw.json.template     # placeholders ${OPENAI_API_KEY} etc.
      models.json.template       # mirrors ~/.openclaw/agents/main/agent/models.json
    systemd/
      openclaw.service           # user unit
      ollama.service             # if not provided by Ollama installer
  ```
- Push to GitHub as **private** repo `honoyr/openclaw-local`.
- **STOP. Show me the repo skeleton and `.env.example` before I provision the Mac Pro.**

### Phase 2 — Linux base on Mac Pro
- I'll have already installed Ubuntu 24.04 (or you tell me to). You run `scripts/00-base-linux.sh` over SSH:
  - Create `openclaw` user (with `loginctl enable-linger` for user systemd).
  - Mount NVMe at `/var/lib/openclaw-state` with XFS + `noatime`; bind-mount or symlink `~/.openclaw` → that path.
  - ufw default-deny inbound, allow SSH only from my LAN.
  - unattended-upgrades.
  - Install Cloudflare Tunnel client; placeholder config (we'll wire the actual hostname later).
  - AMD GPU: install Mesa + Vulkan drivers (`mesa-vulkan-drivers`, `vulkan-tools`); verify `vulkaninfo` sees the W6800.
- **STOP. Show `vulkaninfo --summary` output and confirm GPU is visible before proceeding.**

### Phase 3 — Ollama
- Install via official script: `curl -fsSL https://ollama.com/install.sh | sh`.
- Override systemd unit to set `OLLAMA_KEEP_ALIVE=24h` and `OLLAMA_HOST=127.0.0.1:11434` (no LAN exposure).
- Pull `llama3.2:3b` as a smoke test.
- Verify GPU offload via `ollama run llama3.2:3b "hello"` while watching `radeontop` or `intel_gpu_top` (or `nvtop` if it works on AMD).
- **STOP. Show me first-token latency and tokens/sec for the smoke test.**

### Phase 4 — OpenClaw native install
- `curl -fsSL https://openclaw.ai/install.sh | bash` as the `openclaw` user.
- `openclaw onboard --install-daemon`.
- Install plugins matching the container (read exact list from `openclaw-deploy`'s Dockerfile).
- Render `~/.openclaw/openclaw.json` from `config/openclaw.json.template` + `.env`. Include the Ollama provider entry as a secondary OpenAI-compatible provider.
- I will paste the Codex OAuth tokens into `.env` after you tell me the variable names.
- All channels **disabled** in this phase.
- **STOP. Show `openclaw doctor` output and `curl localhost:<port>/health`.**

### Phase 4.5 — Memory backend + hybrid retrieval (CORRECTED per Appendix A of feasibility doc)

Context: Earlier I thought we needed a Qdrant sidecar. **That was wrong.** OpenClaw's default `memory-core` backend natively supports hybrid (BM25 + vector via SQLite FTS5 + `sqlite-vec`), MMR, temporal decay, and multimodal — see [`/concepts/memory-search`](https://docs.openclaw.ai/concepts/memory-search) and [`/reference/memory-config`](https://docs.openclaw.ai/reference/memory-config). Our container is currently using `memory-lancedb` (vector-only) with `active-memory` disabled. We need to fix that on the local box.

Do this:

1. **Confirm the gap on the local install** — run the same probe (`openclaw plugins list --json` + grep the memory-lancedb bundle for `hybrid|rerank|bm25|fts`). Record in `openclaw-local/docs/retrieval-gap.md`.
2. **Set `plugins.slots.memory: "memory-core"`** in `~/.openclaw/openclaw.json` on the Mac Pro (this is the default; just make it explicit). Do **not** install `memory-lancedb` unless we need S3-backed memory or a portable Arrow store. Document the decision in `retrieval-gap.md`.
3. **Add the hybrid search config** under `agents.defaults.memorySearch`:
   ```json
   {
     "agents": {
       "defaults": {
         "memorySearch": {
           "provider": "openai",
           "model": "text-embedding-3-small",
           "query": {
             "hybrid": {
               "enabled": true,
               "vectorWeight": 0.7,
               "textWeight": 0.3,
               "candidateMultiplier": 4,
               "mmr": { "enabled": true, "lambda": 0.7 },
               "temporalDecay": { "enabled": true, "halfLifeDays": 30 }
             }
           },
           "store": { "vector": { "enabled": true }, "fts": { "tokenizer": "unicode61" } },
           "cache": { "enabled": true, "maxEntries": 50000 }
         }
       }
     }
   }
   ```
   (Provider can be `openai`, `ollama` with `mxbai-embed-large`, or `local` GGUF — pick based on what the user already has wired in `.env`.)
4. **Enable `active-memory`** so the agent auto-recalls before replies. Scope to `main` agent + direct chats, per the docs Quick Start:
   ```json
   {
     "plugins": {
       "entries": {
         "active-memory": {
           "enabled": true,
           "config": {
             "enabled": true,
             "agents": ["main"],
             "allowedChatTypes": ["direct"],
             "modelFallback": "google/gemini-3-flash",
             "queryMode": "recent",
             "promptStyle": "balanced",
             "timeoutMs": 15000,
             "maxSummaryChars": 220,
             "persistTranscripts": false,
             "logging": true
           }
         }
       }
     }
   }
   ```
5. **Restart, reindex, verify:**
   ```sh
   openclaw gateway restart
   openclaw memory index --force --agent main
   openclaw memory status --deep --agent main
   openclaw memory search "<a known phrase from MEMORY.md>"
   ```
6. **Side-by-side smoke:** run the same query before and after enabling hybrid (you can disable hybrid with `query.hybrid.enabled: false` and reindex to compare). Capture results in `retrieval-gap.md`.
7. **STOP. Show me the `openclaw memory status --deep` output and the side-by-side query results before moving on.**

Out of scope for now (defer to a later phase):
- Gemini Embedding 2 multimodal indexing — wait until we have a Gemini provider entry wired.
- `memory-wiki` plugin — evaluate after we have stable hybrid recall working.
- Sidecar vector stores (Qdrant / Weaviate) — **not needed**; `memory-core` already does hybrid.

### Phase 5 — Smoke harness in parallel mode
- Port `scripts/smoke-prod.sh` from `openclaw-azure-template` to `scripts/smoke-local.sh`, pointed at the local box.
- Run it. Fix anything red.
- Set up a cron / systemd timer that runs smoke every 15 min, logs to `/var/log/openclaw-smoke.log`, alerts me via Telegram (re-use the container's Telegram bot for alerts only — read-only path, no inbound).
- **STOP. Show me a green smoke run + the timer status.**

### Phase 6 — Cutover plan (do NOT execute without explicit go-ahead)
- Write `docs/cutover-checklist.md` covering: DNS swap (if any), Telegram webhook repoint, Azure container stop, state-rsync from Azure Files to local NVMe, rollback plan.
- This phase **plans only**. We execute later after a week of green smoke runs.

## Operating rules while you work

- Update `~/.copilot/session-state/<your-session>/plan.md` as you move between phases.
- Use the SQL todo table for granular tracking inside each phase.
- Run `openclaw doctor` and `scripts/smoke-local.sh` after every config change.
- If a step fails twice with the same error, **stop and ask me** — don't keep hammering.
- Never run destructive commands on `~/repos/openclaw-deploy` or `~/repos/openclaw-azure-template`. Read-only.
- Commit early, commit often, conventional commits, but **never commit `.env` or anything matching `*-token*`, `*secret*`, `*.pem`, `*.key`**.
- Ask before installing anything I haven't already named in this prompt.

## First action

Start with **Phase 0**. Read the four required docs, then produce the inventory + target-config diff and stop for my review.
============================================================
