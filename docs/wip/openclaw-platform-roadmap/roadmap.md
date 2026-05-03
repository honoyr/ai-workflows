# OpenClaw Platform Roadmap

> Status: **draft** — umbrella design doc.
> Companion file: [`tasks.md`](./tasks.md).
> Underlying deployment: [`docs/guides/openclaw-azure-setup-guide.md`](../../guides/openclaw-azure-setup-guide.md), runtime under `~/openclaw/`, container `openclaw-container-dgonor` on ACI `eastus`.

This roadmap consolidates nine cross-cutting platform questions into a single ranked plan. Each topic gets:

- **What we want** — the user-facing capability.
- **Native support** — what OpenClaw v2026.4.27 ships out of the box (per [docs.openclaw.ai](https://docs.openclaw.ai)).
- **Recommendation** — what we should actually build, and what to avoid.
- **Effort / Risk** — t-shirt sizing.

After sign-off, the top-priority items get their own `<topic>/design.md` + `<topic>/implementation.md` + `<topic>/tasks.md` under this folder. Deferred items stay in this roadmap as parking-lot entries.

---

## Topic 0 — Underlying constraint to remember (parking lot)

Before the topic list, one fact informs several decisions: **Azure Files (SMB/CIFS) doesn't honour `fcntl`/`flock` POSIX locks** the way OpenClaw's session-JSONL writer expects. Today our `agents/sessions/*.jsonl` writes go through the SMB mount; concurrent lanes (CLI probes, Telegram, the UI) collide on the `*.jsonl.lock` sidecar file and openclaw's model-fallback logic mis-attributes the lock timeout as a "model failed" event (live log capture: `decision=candidate_failed reason=unknown detail=session file locked (timeout 10000ms)`).

**Practical impact:** functional but slow. The 10 s lock timeout costs latency on contended writes; it never corrupts data. The user-visible symptom (silent model swap) is fully addressed by **Topic 4 strict-mode routing** — once `model-router-1` runs as agent-primary with `fallbacks: []`, the lock error surfaces as a real error rather than a model swap.

**Cheap mitigation (Topic 0b below):** raise the session-lock acquire timeout from 10 s to ~60 s. Sticky writers wait and succeed instead of timing out. One config-line change.

**Constraint that keeps us on SMB:** macOS supports SMB natively (Finder → Cmd-K → `smb://…`) and we want to mount the same share from the laptop. Azure Files **NFS Premium** would carry POSIX locks properly, but NFS Premium requires private-endpoint + vnet integration that is awkward to reach from a laptop. So NFS migration stays in the parking lot.

**Future option (parked, not in this roadmap):** if we ever scale to multi-user / high-concurrency workloads, migrate the whole `openclaw-workspace` share to Azure Files NFS Premium (~$50-100/mo floor) or split out a small NFS-Premium share just for `agents/sessions/`. Until then, the timeout bump (Topic 0b) plus strict-mode routing (Topic 4) is enough.

### Topic 0b — Bump session-lock timeout

One-shot config change: locate the session-writer lock-acquire timeout key in `~/openclaw/config.json` (likely under `gateway.session` or `agents.session`; need to confirm exact path against the running container's config schema) and raise from 10 000 ms → 60 000 ms. Redeploy. Done.

---

## Topic priority overview

| # | Topic | Priority | Effort | Risk | Native? |
|---|---|---|---|---|---|
| 1 | Bump to v2026.4.27 + auto-pull-latest in scripts | **P0** | S | Low | ✅ |
| 8 | Versioning strategy for the docker setup (separate repo + ACR) | **P0** | S | Low | n/a |
| 9 | Phone-based ACI start/stop | **P1** | M | Med | ❌ (build) |
| 7 | LLM-Wiki on existing LanceDB (`memory-wiki` plugin) | **P1** | M | Low | ✅ (bundled) |
| 3 | File ingestion (pdf/img/docx/xlsx) via Telegram + UI | **P1** | M | Med | Partial |
| 4 | Adaptive routing via `model-router-1` and per-skill overrides | **P2** | S | Low | Partial |
| 2 | Voice (UI dictation, Foundry STT vs bundled providers) | **P2** | M | Med | Partial |
| 5 | Local docker still wired to Azure Files + LanceDB | **P2** | M | Med | Partial |
| 6 | Cosmos DB | **P3 — reject** | L | High | ❌ |
| 10 | ACP harnesses (Claude / Copilot / Codex / Gemini / Cursor) for tier + capability routing | **P1** | M | Med | ✅ (`acpx` already loaded) |

**Top 4 to deep-dive first: #1, #8, #9, #10.** The rest become per-topic sub-folders as we get to them.

---

## 1. Bump to v2026.4.27 + auto-pull-latest

### What we want
- Run openclaw `2026.4.27` (latest stable) in ACI.
- `build-image.sh` and/or `deploy.sh` should automatically resolve "latest stable upstream" from GitHub Releases and `az acr import` it to ACR before building the wrapper, instead of us hand-bumping `BASE_TAG`.

### Native support
- Upstream releases follow `vYYYY.M.D` semver-date tags, dist-tags `latest` / `beta` / `dev` ([install/development-channels](https://docs.openclaw.ai/install/development-channels.md)).
- ghcr publishes `ghcr.io/openclaw/openclaw:<version>-slim` on each release.
- `openclaw update` exists but only operates *inside* an existing install (npm/git mode) — it doesn't help our ACR-import flow.

### Recommendation
Add a small "resolve + import" step at the top of `build-image.sh` (or extract to `pull-latest.sh`):

1. `curl https://api.github.com/repos/openclaw/openclaw/releases/latest | jq -r .tag_name` → `v2026.4.27`. Strip the `v` to get the ghcr tag suffix.
2. Compare against a cached value in a new `~/openclaw/.upstream-version` file. If the same and `ACR_NAME.azurecr.io/openclaw:<base-tag>` already has the right digest, skip import.
3. Otherwise `az acr import --source ghcr.io/openclaw/openclaw:<version>-slim --image openclaw:<base-tag>` (where `<base-tag>` is auto-bumped to `v<seq>` — see Topic 8 for the tag scheme).
4. Continue into the existing `az acr build`.

Bump now to `BASE_TAG=v4`, `CUSTOM_TAG=custom-v4-1`. Going forward, `<base-tag>` becomes a function of upstream date: e.g., `v-2026.4.27` literal, removing the `v3 → v4 → v5` mental tax.

### Effort / Risk
S, low. Pure script change in `~/openclaw/`; no openclaw config changes.

---

## 2. Voice — UI dictation, telephony, Foundry STT

### What we want
- Ability to talk to the agent in the browser instead of typing.
- Optionally inbound/outbound phone calls.
- Decide whether to use Azure AI Speech as the STT/TTS provider since we already pay Foundry.

### Native support
- **`talk-voice` plugin** is already loaded in our container (8-plugin list confirms it). Provides browser dictation + TTS.
- **`voice-call` plugin** ([plugins/voice-call](https://docs.openclaw.ai/plugins/voice-call)) handles real phone calls via Twilio / Telnyx / Plivo / mock. Bundled streaming-STT providers: Deepgram, ElevenLabs, Mistral, **OpenAI** (`gpt-4o-transcribe`), xAI. Bundled realtime voice: Google Gemini Live, OpenAI.
- **Azure / Foundry is *not* in the bundled provider list** for STT or realtime voice — but the OpenAI provider is OpenAI-API-compatible, and Azure OpenAI exposes an OpenAI-Responses API, so Foundry transcription deployments can be reached by configuring the OpenAI streaming provider with the Foundry baseUrl + key.

### Recommendation
Two-step:

- **2a (browser dictation):** zero code — `talk-voice` is already running. Confirm via Control UI; if needed, configure `messages.tts.provider` in `config.json`. Free.
- **2b (telephony):** defer until Topic 1/8/9 land. When picked up, use **Twilio + OpenAI streaming STT** as the bundled fast path. Foundry-as-STT is feasible (point `streaming.provider: "openai"` at the Foundry baseUrl with `model: "gpt-4o-transcribe-1"`) but should only be done after we confirm the Foundry deployment exists; it does *not* save money over native OpenAI usage and adds a debugging surface.

**Don't** build a custom Azure Speech plugin — not worth the effort for a personal-use deployment.

### Effort / Risk
2a: XS, low. 2b: M, med (requires Twilio account + public webhook via Cloudflare Tunnel — we already have one).

---

## 3. File ingestion — pdf, img, docx, xlsx via Telegram & UI

### What we want
Send a file to the bot (Telegram or via UI drag-drop) and have the agent read it.

### Native support
- Telegram channel forwards media + attachments to the gateway pipeline ([concepts/messages](https://docs.openclaw.ai/concepts/messages.md): "Debounce applies to text-only messages; media/attachments flush immediately").
- **Images:** flow directly into multimodal-capable models. Our default `Kimi-K2.6-1` advertises `input: ["text","image"]` in the model registry, so attached images Just Work.
- **PDFs / DOCX / XLSX:** OpenClaw does not ship a built-in office-doc extractor. The `oadank/openclaw-wiki-lancedb` reference shows a `wiki-quick-ingest.py` pattern that handles PDF/DOCX/XLSX → markdown via Python libs (`pypdf`, `python-docx`, `openpyxl`). The `openclaw skills install` mechanism ([cli/skills](https://docs.openclaw.ai/cli/skills.md)) is the supported way to ship that as a reusable skill.

### Recommendation
Three sub-deliverables:

1. **Images via Telegram/UI** — already works. Verify by sending a screenshot to the bot. No code.
2. **PDFs** — install / author a `pdf-extract` skill that, when the agent sees `attachment: *.pdf`, runs a sandboxed extraction and returns markdown. Use `pdftotext` or `pdfplumber` in a Python sandbox; output truncated + summarised.
3. **DOCX / XLSX** — similar skill (`office-extract`) using `python-docx` + `openpyxl`. Less critical; defer if not urgent.

For long PDFs, write the extraction to the workspace (`/mnt/openclaw-workspace/inbox/`) and let the agent grep + chunk it instead of pasting the full content into context.

### Effort / Risk
M, med. Skills are simple but we own three new code surfaces; risk is in the sandbox config (the `exec` tool needs to run python with the right deps available in the container).

---

## 4. Adaptive routing via `model-router-1`

### What we want
Right model for the right job, automatically — vision tasks → multimodal model, code tasks → strong reasoning model, casual chat → cheap fast model. And when the agent picks `model-router-1` we want the **real error** if Azure rejects, not a silent swap to Kimi.

### Native support — refined per [model-failover doc](https://docs.openclaw.ai/concepts/model-failover)

OpenClaw distinguishes **why** a model was selected, and applies fallback differently based on source:

| Selection source | Where it's set | Fallback behaviour |
|---|---|---|
| **Configured default** | `agents.defaults.model.primary` | Walks `agents.defaults.model.fallbacks` chain |
| **Agent primary** | `agents.list[].model` | **Strict by default**; only falls back if that agent's model object has its own `fallbacks` |
| **User session override** | `/model …`, picker, `sessions.patch` | **Strict** — failure surfaces, no silent swap |
| **Auto fallback** | Set by the runtime mid-retry | Walks the chain; cleared by `/new` / `/reset` |
| **Cron payload** | `payload.model` | Walks chain unless `payload.fallbacks: []` |

**This explains the bug we hit.** When `model-router-1` was configured-default, the SMB lock timeout was reported as `candidate_failed` and the runtime walked to Kimi. With `model-router-1` as either the **agent primary** (with `fallbacks: []`) or via **`/model microsoft-foundry/model-router-1`** from chat, the lock error surfaces directly instead of silently swapping models.

OpenClaw also provides:

- **Per-agent default model** + per-agent `fallbacks` opt-in (use `fallbacks: []` to make strict explicit).
- Skills + agent runtimes (orthogonal — see Topic 10).
- **No built-in classifier-based router** in openclaw itself — adaptivity must come from Azure-side `model-router-1`, manual `/model`, or per-skill/per-agent pinning.

### Recommendation
Three layers, in order:

1. **Default to `microsoft-foundry/model-router-1`** as the agent primary (`agents.list[].model`) with **`fallbacks: []`** — strict mode. Azure handles adaptive routing; we surface real errors.
2. **Per-agent overrides** for specialised lanes: `vision` → multimodal Kimi, `coder` → Claude/Anthropic, `image` → OpenAI `gpt-image-1` (Topic 10 captures the harness-style version).
3. **Manual `/model …` for one-shot** session-level overrides — also strict.

Optionally keep one configured-default fallback chain (e.g., `Kimi-K2.6-1` after `model-router-1`) **only** for the unconfigured-agent path. Per-agent strict still wins for the agents we actually use.

**Don't** disable fallback globally; it's still useful for the catch-all path. **Don't** chase a custom prompt-classifier — Azure already does that.

### Effort / Risk
S, low. Config-only edits to `~/openclaw/config.json`. The Topic-0 SMB-lock fix should land first or in parallel — strict mode will simply surface the SMB error louder until then.

---

## 5. Local docker run, still wired to Azure Files + LanceDB

### What we want
Run the same container locally on the laptop for fast iteration, while still reading/writing the Azure-hosted workspace + LanceDB index.

### Native support
- LanceDB needs no mount: it talks `az://` directly via `storageOptions` (account name + key). Same config works locally.
- Azure Files share — locally accessible via `mount -t cifs` (Linux), `blobfuse2`, or a small **rclone** sidecar that mirrors the share to a local folder. ACI uses the SMB mount transparently; we have to do that ourselves on macOS.
- Built-in openclaw docker flow ([install/docker](https://docs.openclaw.ai/install/docker.md)) uses `docker compose` with `.env` files. ClawDock helpers ([install/clawdock](https://docs.openclaw.ai/install/clawdock.md)) wrap that.

### Recommendation
Add a `~/openclaw/local-up.sh`:

1. Same env vars as `deploy.sh` (sourced from `env.sh`).
2. Pulls our wrapper image from ACR (`acrdgonor2.azurecr.io/openclaw:<custom-tag>`).
3. `docker run -d` with:
   - `-p 18789:18789`
   - `OPENCLAW_CONFIG_B64=...`
   - `AZURE_STORAGE_*` + LanceDB env vars (LanceDB talks blob directly).
   - For workspace: **option A** — bind-mount a local folder (laptop view, no SMB), agent works on a separate workspace; **option B** — `rclone mount` the Azure share locally, then bind-mount the rclone target. Pick (A) for simplicity; the agent's workspace is meant to diverge across machines anyway.

Critically, **don't** try to mount the *production* Azure Files share locally during dev — it'll collide with the ACI gateway's SMB locks and corrupt sessions. Use a separate share `openclaw-workspace-dev` if you want shared state, otherwise local-only workspace + shared LanceDB.

### Effort / Risk
M, med. Filesystem mount configuration is fiddly on macOS. Risk: corrupting prod sessions if we hit the same share concurrently.

---

## 6. Cosmos DB — should we use it?

### What we want
Understand the value and decide.

### Native support
**None.** OpenClaw memory backends ([concepts/memory](https://docs.openclaw.ai/concepts/memory.md)): `memory-core` (built-in), QMD, Honcho, `memory-wiki`, and our `memory-lancedb`. There is no Cosmos plugin. Sessions are JSONL files on disk.

### Recommendation
**Reject.** Cosmos would buy us:

- Managed document store with global replication.
- Transactional writes — could fix the Topic-0 SMB-lock issue *if* sessions moved to Cosmos.

It would cost us:

- Custom plugin development (no existing port).
- $25+/mo minimum for the smallest provisioned throughput; pay-per-request is unbounded.
- Schema lock-in to a backend openclaw doesn't natively support, so every upgrade to openclaw becomes a porting risk.

The actual problem (Azure Files SMB locks) has a much cheaper solution: move the `agents/sessions/` symlink target to container-local disk + nightly export to blob, or upgrade the share to Azure Files NFS (Premium). Track that as part of the Topic-0 fix-up, not a Cosmos migration.

### Effort / Risk
n/a — recommendation is to not pursue.

---

## 7. LLM-Wiki on existing LanceDB

### What we want
The Karpathy "LLM Wiki" pattern (persistent compounding knowledge base from ingested sources, not stateless RAG) on top of our LanceDB memory.

### Native support
**Excellent.** OpenClaw ships a bundled `memory-wiki` plugin ([plugins/memory-wiki](https://docs.openclaw.ai/plugins/memory-wiki)) that is *literally* the Karpathy pattern, with the `bridge` mode designed to import public memory artifacts from the active memory plugin (i.e., our `memory-lancedb`). Provides:

- vault layout (`entities/`, `concepts/`, `syntheses/`, `sources/`, `reports/`).
- `openclaw wiki ingest|compile|search|get|apply|lint|bridge import` ([cli/wiki](https://docs.openclaw.ai/cli/wiki.md)).
- structured claims with confidence + evidence.
- contradiction reports + dashboards.

The third-party `oadank/openclaw-wiki-lancedb` repo adds a hybrid-search layer (BGE embeddings + grep + BM25 + reranking) that goes *beyond* the bundled plugin. Useful, but the bundled plugin alone covers the Karpathy idea.

### Recommendation
1. **Phase A (native):** enable `memory-wiki` in `bridge` mode, point its vault at `/mnt/openclaw-workspace/wiki/`. Drop a starter `AGENTS.md` describing ingest/lint/compile workflow. Use `openclaw wiki ingest` for new sources. This is mostly config + workflow.
2. **Phase B (optional):** evaluate `oadank/openclaw-wiki-lancedb`'s scripts after Phase A is bedded in. The hybrid search is genuinely better than `wiki search` alone, but it's a separate code surface to maintain.

Skip Phase B until we feel friction with native search.

### Effort / Risk
Phase A: S, low (config + workflow). Phase B: M, med (third-party scripts, Python deps).

---

## 8. Versioning strategy for our docker setup

### What we want
Where do `~/openclaw/`, the wrapper Dockerfile, scripts, configs, and image tags live? Right now they live unversioned on one laptop with secrets next to them.

### Recommendation
Three-layer split:

1. **Code → new git repo `openclaw-deploy`** (separate from `ai-workflows`). Contains `docker/Dockerfile`, `docker/openclaw-init.sh`, `build-image.sh`, `deploy.sh`, `local-up.sh`, `config.json`, `bicep/` (or `terraform/` if preferred) for declarative ACI/ACR/Storage. Public or private GitHub repo.
2. **Images → Azure Container Registry (`acrdgonor2`)**. Tag scheme: `openclaw:<upstream>` for raw imports (e.g. `openclaw:2026.4.27`) and `openclaw:custom-<upstream>-<wrapper-rev>` for wrapper builds (e.g. `openclaw:custom-2026.4.27-1`). Drop the opaque `v3`/`v4` numbering.
3. **Secrets → Azure Key Vault**, referenced from the deploy script via `az keyvault secret show`. `env.sh` becomes a thin wrapper that pulls from Key Vault into env vars at deploy time. Plaintext `env.sh` deletes once Key Vault is wired.

**Don't use Docker Hub.** Cross-cloud image pulls add latency and we already pay for ACR. **Don't put this in `ai-workflows`** — different lifecycle, would muddle the docs repo.

GitHub Actions in `openclaw-deploy`: on push to `main`, run `pull-latest.sh` (Topic 1) → `build-image.sh` → optionally `deploy.sh` (gated behind manual approval).

### Effort / Risk
S–M, low. Mostly mechanical: `git init`, move files, write Bicep, wire Key Vault, add a CI workflow.

---

## 9. Phone-based ACI start/stop

### What we want
From iPhone, start or stop the ACI container without the Azure portal or laptop.

### Native support
None — this is purely an Azure/iOS problem. Openclaw's CLI can't be reached when the container isn't running, so an external trigger is mandatory for "start".

### Options
| Option | Pros | Cons |
|---|---|---|
| (a) Azure Function (HTTP trigger) + Telegram bot webhook → managed-identity → `az container start/stop` | No always-on VM, free tier covers it, secret-free via managed identity | Some setup; Telegram bot needs a separate token if we don't reuse the openclaw bot |
| (b) Logic App with Telegram trigger + ACI start/stop action | Visual designer, no code | Pricier per execution; auth fiddly |
| (c) iOS Shortcuts → SSH to a controller VM → `az container start/stop` | Simplest mental model | Needs always-on VM ($) |
| (d) Openclaw skill that runs `az container ...` via `exec` tool | Unified UX with the bot | Only works while the container is up — useless for "start" |

### Recommendation
**(a) Azure Function + reuse the existing Telegram bot** as the trigger. Function:

- HTTP-triggered, behind a shared-secret query param.
- Runs in Consumption plan ($0 baseline).
- Has a managed identity with `Container Instance Contributor` scoped to the resource group.
- Two routes: `/start` and `/stop`. Optional `/status`.
- Cloudflare Worker or Telegram bot's `setWebhook` posts to the Function on `/start`/`/stop` commands.

Pair with **(d)** for in-band stop/status: once the container is up, the openclaw agent can already run `az` commands via `exec` if we install azure-cli and grant a federated workload identity. Saves a round-trip for stop, while `/start` always hits the Function.

This belongs in the same `openclaw-deploy` repo as Topic 8 (Bicep adds the Function + identity assignment).

### Effort / Risk
M, med. Function is small; the work is in Bicep/identity/Telegram webhook. Risk: Telegram webhook needs a public URL — we already have Cloudflare Tunnel, so the Function can sit on Azure Functions' default `*.azurewebsites.net` and Telegram reaches it directly without extra plumbing.

---

## 10. ACP harnesses — Claude Code, Copilot CLI, Codex, Gemini CLI, Cursor, Droid

### What we want
- Use external coding harnesses through OpenClaw to **leverage subscription-tier auth** (Copilot CLI free tier, Claude Code Max, ChatGPT Plus / Codex) instead of paying per-token via Azure.
- Use **capability-specific** harnesses for things only one ecosystem does well: Gemini CLI for native YouTube ingestion, OpenAI Codex/Images for `gpt-image-1` image generation, Claude Code for big-context refactoring runs.
- All routed through one openclaw conversation, so the user never leaves Telegram/UI.

### Native support — already wired

Per [tools/acp-agents](https://docs.openclaw.ai/tools/acp-agents):

- The bundled **`acpx` runtime plugin is loaded by default**, and we already see `acpx` in the 8-plugin list of our running container. So `/acp spawn <id>` is *available right now*; the only thing missing is per-harness binaries + auth inside the container.
- Supported harness ids out of the box: `claude`, `codex`, `copilot`, `cursor`, `droid`, `gemini`, `iflow`, `kilocode`, `kimi`, `kiro`, `opencode`, `openclaw`, `pi`, `qwen`.
- Spawn from chat: `/acp spawn gemini` or `sessions_spawn({ runtime: "acp", agentId: "gemini" })`. Each session is a tracked background task.
- **Native Codex app-server** (separate `codex` plugin) gives tighter conversation binding via `/codex bind` — preferred over ACP for Codex specifically. Optional; not loaded by default in our container.
- Health check: `/acp doctor` reports whether each harness is reachable.

### Capability map for our use cases

| Need | Harness | Subscription used | Notes |
|---|---|---|---|
| YouTube link → summary + Q&A | `gemini` | Google AI Studio key or Gemini CLI auth | Gemini CLI has native YouTube tooling — paste a URL, it fetches transcript + frames |
| Image generation (`gpt-image-1`) | `codex` (or native Codex app-server) | ChatGPT Plus / Codex sub | Codex CLI exposes image gen; alternative: register `gpt-image-1` directly as a model in `providers.openai` and call from any agent |
| Big-context coding refactor | `claude` | Claude Code Max | Use Claude Code's own auth; tokens come from your subscription |
| Free general coding | `copilot` | GitHub Copilot Pro | Copilot CLI ACP adapter |
| Cursor-style file ops | `cursor` | Cursor Pro | Optional |

### Container-side requirements

For each harness we want enabled, the container must have:

1. The CLI binary on `PATH` (`gemini`, `claude`, `codex`, `copilot`, etc.).
2. Auth state — usually `~/.config/<tool>/...` or `~/.<tool>/...`. For OAuth-based harnesses (Claude Code Max, Copilot, Gemini), we have to either:
   - bake auth files into the image (bad — short-lived tokens, secrets in image),
   - mount them from Azure Files (better — pre-do the OAuth dance once, persist `~/.config/<tool>` to the share's `_state/<tool>/`),
   - or do the OAuth interactive flow once after container start (works but manual on every redeploy).

The Azure-Files-mount approach pairs naturally with the existing `_state/` symlink scheme in `openclaw-init.sh` — extend it to symlink `~/.config/claude`, `~/.config/gemini`, `~/.config/github-copilot`, `~/.codex` from the share.

### Recommendation
Three-phase rollout:

- **Phase A (Gemini for YouTube — highest single payoff):**
  1. Add `gemini-cli` install to the wrapper Dockerfile.
  2. Mount `~/.config/gemini` from Azure Files `_state/gemini/`.
  3. First-time auth: `az container exec ... gemini auth` once.
  4. Verify `/acp spawn gemini` works; ask it to summarise a YouTube link.
  5. Add a tiny openclaw skill `youtube-summary` that, when the user pastes a YT URL in Telegram, spawns a Gemini ACP session and routes the URL through it.

- **Phase B (Image gen):** Two routes in parallel — both cheap to try:
  1. **Direct model** route — register `gpt-image-1` as a model under our existing `providers.openai` (or via Foundry deployment). Then the agent generates images via tool-call without spawning a harness. *Preferred* for one-shot image asks.
  2. **Codex ACP** route — install `codex` CLI + auth, use `/acp spawn codex` or enable the native `codex` plugin (`/codex bind`). Better for multi-step image-edit workflows.

- **Phase C (Claude Code + Copilot CLI):**
  1. Install `claude` CLI + Copilot CLI in the image.
  2. Mount `~/.config/claude` and `~/.config/github-copilot` from `_state/`.
  3. First-time OAuth via `az container exec`; persist tokens to the share.
  4. Configure `agents.list[]` so a `coder` agent prefers spawning `claude` ACP for tasks > some token budget; otherwise stay on the cheap default.

### Don'ts
- Don't bake any OAuth tokens into the docker image.
- Don't try to expose openclaw's internal tools to the harnesses by default (per docs, MCP bridges are explicit opt-in via `acp-agents-setup`). Start without bridging; only add it if a harness genuinely needs back-call into openclaw.
- Don't re-implement adaptive routing in openclaw config to dispatch between ACP harnesses — instead, add small purpose-specific skills (`youtube-summary`, `image-gen`) that explicitly choose a harness. Keeps the routing logic readable.

### Effort / Risk
M, med. Mostly install + auth plumbing; risk is in the OAuth-persistence pattern across redeploys. Phase A (Gemini + YouTube) alone is small and high-value.

---

## Cross-topic dependency map

```
Topic 0 (SMB lock fix-up)
    ↓ (informs)
    Topics 4, 5, 6, 7

Topic 8 (versioning / repo / Bicep)
    ↓ (foundation for)
    Topics 1, 9, 10 (CI + Bicep + auth-state persistence in Azure Files)

Topic 1 (auto-pull-latest) ─── Topic 9 (phone start/stop)
    ↓
    Topic 5 (local docker)

Topic 7 (memory-wiki) ──→ Topic 3 (file ingestion lands in the wiki)

Topic 10 (ACP harnesses) ─┬─→ Topic 4 (per-skill harness selection complements per-agent model selection)
                          └─→ Topic 3 (Gemini handles YouTube; Codex handles images — both are file/media ingestion)
```

## Out of scope (parking lot)

- Multi-agent split (work / personal / coder agents) — interesting later, not now.
- Cosmos DB — explicitly rejected, see Topic 6.
- Custom Azure Speech provider plugin — overkill for current usage.
- macOS Tailscale-based remote access in place of Cloudflare Tunnel — current setup works.
