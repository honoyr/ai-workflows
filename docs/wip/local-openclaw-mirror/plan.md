# Local OpenClaw: Mirror Container Setup + One-Shot Data Copy

## Problem
The macOS-native OpenClaw install at `~/.openclaw/` is running gateway+Telegram, but is missing the rich plugin/skill/memory wiring that the Azure container (`openclaw-deploy`) has been accumulating: lancedb vector recall, `memory-wiki` Obsidian bridge, `active-memory` auto-summarizer, brave web search, custom slash-commands, plus existing session/chat history. Goal: bring the local install up to feature-parity with the container, copy the existing data once, then let the two installs diverge — no sync after the cut-over.

## Container inventory (source of truth: `~/repos/openclaw-deploy`)

### Config (`config/config.json`)
- **plugins.entries**: `codex`, `openai`, `active-memory`, `brave`, `memory-lancedb`, `memory-wiki`
- **plugins.slots.memory**: `memory-lancedb`
- **agents.defaults.model**: `openai/gpt-5.5` (primary), `azure-images/gpt-image-2` (image gen)
- **tools.web.search**: brave, max 5 results
- **messages.tts**: Google TTS via `${GEMINI_API_KEY}`
- **acp.allowedAgents**: `gemini`
- **active-memory config**: agents=[main], chatTypes=[direct], fallback=`google/gemini-3-flash-preview`, autoCapture+autoRecall on

### Skills (`docker/skills/`)
`obscite`, `obsingest`, `obspull`, `obspush`, `insurance-appeal` — baked in via `COPY skills/ /home/node/.openclaw/skills/`

### Plugins added in Dockerfile (not in stock base)
- `clawhub:@openclaw/brave-plugin`
- `clawhub:@openclaw/memory-lancedb`
- `@openclaw/codex`

(All three already present locally per `openclaw plugins list` — they ship in the npm distribution too.)

## Data inventory (Azure storage `openclawstoragedgonor`)

| Container | Size | Use | Migrate? |
|---|---|---|---|
| `openclaw-memory/lancedb/` | 0.1 MB (47 blobs, ~empty) | Vector recall DB | YES (trivial) |
| `openclaw-state-snapshots/latest/_state/` | 205 MB (877 blobs) | Container state snapshot (agents, sessions, telegram, cron, flows, canvas, identity, credentials) | SELECTIVE — see below |
| `openclaw-attachments/` | 0.5 MB | Telegram file attachments via CF Worker | NO — stays on Azure |
| `openclaw-inbox-photos`, `openclaw-photo-state` | small | Photo cron pipeline | NO — Mac-local doesn't use photo cron |
| GitHub `honoyr/openclaw-vault` | 338 KB | memory-wiki vault (Obsidian) | YES via git clone |

State snapshot selective extract:
- ✅ `_state/agents/main/sessions/` — chat history
- ✅ `_state/cron/` — scheduled jobs
- ✅ `_state/flows/`, `_state/canvas/` — agent flow + canvas state
- ✅ `_state/telegram/` — group/topic cache (optional; local bot has own)
- ❌ `_state/credentials/`, `_state/auth-profile-secrets/` — AES-GCM blobs encrypted with container's key, useless locally
- ❌ `_state/findagent*.sh`, `fix-*.sh`, `install-*.sh` — ad-hoc shell scripts at root, noise
- ❌ `_state/wiki/` — same content as GitHub repo, use git instead

## Approach

### Phase A — Install skills (zero-risk)
- `cp -r ~/repos/openclaw-deploy/docker/skills/* ~/.openclaw/skills/`

### Phase B — Wiki vault
- `git clone git@github.com:honoyr/openclaw-vault.git <USER_VAULT_PATH>` (ask user)
- Patch `~/.openclaw/openclaw.json` → `plugins.entries.memory-wiki` with full container config block but `vault.path = <USER_VAULT_PATH>`
- Enable `memory-wiki` plugin

### Phase C — Lancedb (vector memory)
- Download `openclaw-memory/lancedb/` blob tree → `~/.openclaw/memory/lancedb/` via `az storage blob download-batch`
- Patch `plugins.entries.memory-lancedb` with config block but `dbPath=~/.openclaw/memory/lancedb` (LOCAL path, no `storageOptions`)
- Set `plugins.slots.memory = memory-lancedb`
- Reuse embedding endpoint: `https://dgonor-llm-api-resource.openai.azure.com/openai/v1` + `text-embedding-3-small-1`, key from KV `azure-openai-api-key`

### Phase D — Active-memory + brave
- Enable `active-memory` plugin with container's config
- Configure `brave` plugin with API key from KV `brave-search-api`
- Set `tools.web.search.provider = brave`

### Phase E — State snapshot selective restore (optional, only if user wants history)
- `az storage blob download-batch` with `--pattern "latest/_state/agents/*" --pattern "latest/_state/cron/*" --pattern "latest/_state/flows/*" --pattern "latest/_state/canvas/*"`
- Merge into local: agents/main/sessions/ → `~/.openclaw/agents/main/sessions/`, cron/ → `~/.openclaw/cron/`, flows/ → `~/.openclaw/flows/`, canvas/ → `~/.openclaw/canvas/`
- Skip: any `*auth-profiles*`, `credentials/`, `auth-profile-secrets/`

### Phase F — Verify
- `openclaw gateway restart`
- Tail `/tmp/openclaw/openclaw-<date>.log` for plugin-load errors
- Test in Telegram: ask agent a memory question, verify recall pulls from lancedb; ingest a sample URL into wiki, verify page created in vault
- Confirm all 3 Telegram topics respond (carry-over from prior task)

### Phase G — Lock divergence
- Drop `~/.openclaw/README.local.md` noting: no sync to container after <DATE>; container's `openclaw-state-snapshots` no longer authoritative for local

## Open questions for user (block on these before Phases B/C/E)
1. Obsidian vault location? (`~/Documents/OpenClawVault` recommended)
2. Keep current default model (`microsoft-foundry/Kimi-K2.6-1`) or switch to container's (`openai/gpt-5.5` via codex)?
3. Phase E (pull session history) — yes or skip (start clean)?

## Files that will change
- `~/.openclaw/openclaw.json` (with `.pre-clone-mirror` backup)
- `~/.openclaw/skills/` (new, populated)
- `~/.openclaw/memory/lancedb/` (new)
- `<USER_VAULT_PATH>` (new git clone)
- `~/.openclaw/README.local.md` (new)
- Optionally: `~/.openclaw/{agents,cron,flows,canvas}/` populated from snapshot

## Non-goals
- No sync agent/cron between local + container after the copy
- No photo-inbox cron (Mac-local doesn't need it)
- No cloudflared tunnel (Mac-local is LAN-only)
- No state snapshot loop (Mac-local trusts disk)
