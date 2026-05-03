# Tasks: OpenClaw Platform Roadmap

> Roadmap: [./roadmap.md](./roadmap.md)
> Status: planning
> Created: 2026-04-30

This is the umbrella task list. Each P0/P1 topic gets its own sub-folder + sub-tasks.md as it's picked up. Topics not yet expanded show only their high-level acceptance criteria here.

## Task 1: Bump container to v2026.4.27 + auto-pull-latest in scripts (P0)
- **Status:** pending
- **Depends on:** —
- **Docs:** [roadmap.md#1-bump-to-v202647--auto-pull-latest](./roadmap.md#1--bump-to-v2026427--auto-pull-latest)

### Subtasks
- [ ] 1.1 Add `~/openclaw/pull-latest.sh` that resolves the GitHub `latest` release tag, compares to `~/openclaw/.upstream-version`, and conditionally runs `az acr import` for `ghcr.io/openclaw/openclaw:<tag>-slim` → `acrdgonor2.azurecr.io/openclaw:<tag>`
- [ ] 1.2 Update `~/openclaw/build-image.sh` to invoke `pull-latest.sh` first and consume the resolved tag for `--build-arg BASE_IMAGE`
- [ ] 1.3 Switch tag scheme: drop `v3`/`v4`; use `openclaw:<upstream>` and `openclaw:custom-<upstream>-<rev>` everywhere (env.sh, Dockerfile, deploy.sh, guide doc)
- [ ] 1.4 Run end-to-end: `pull-latest.sh && build-image.sh && deploy.sh`; verify container reports `OpenClaw 2026.4.27`
- [ ] 1.5 Update `docs/guides/openclaw-azure-setup-guide.md` with the new tag scheme + auto-pull step

## Task 2: Versioning — extract `~/openclaw/` into `openclaw-deploy` git repo (P0)
- **Status:** pending
- **Depends on:** —
- **Docs:** [roadmap.md#8-versioning-strategy-for-our-docker-setup](./roadmap.md#8--versioning-strategy-for-our-docker-setup)

### Subtasks
- [ ] 2.1 Create new GitHub repo `openclaw-deploy` (private)
- [ ] 2.2 Move `~/openclaw/{docker/,build-image.sh,deploy.sh,config.json}` into the repo; keep `env.sh` gitignored with a `env.sh.example` committed
- [ ] 2.3 Add Bicep templates under `bicep/` for ACR, Storage Account + share, ACI container group; align with current resources (idempotent on existing infra)
- [ ] 2.4 Migrate secrets from plaintext `env.sh` to Azure Key Vault; rewrite `env.sh` to fetch via `az keyvault secret show`
- [ ] 2.5 Add GitHub Actions workflow `build-and-import.yml`: triggers on push, runs `pull-latest.sh`, runs `az acr build`, optional manual-approval `deploy.sh` job

## Task 3: Phone-based ACI start/stop via Azure Function + Telegram (P1)
- **Status:** pending
- **Depends on:** Task 2 (Bicep + Key Vault scaffolding)
- **Docs:** [roadmap.md#9-phone-based-aci-startstop](./roadmap.md#9--phone-based-aci-startstop)

### Subtasks
- [ ] 3.1 Add Bicep for an Azure Function (Consumption plan), system-assigned managed identity, role-assignment `Container Instance Contributor` on the resource group
- [ ] 3.2 Implement Function `start-stop` (Node.js or Python): handler checks shared-secret query param, parses Telegram `update`, dispatches `az containerinstance start|stop|show`
- [ ] 3.3 Wire Telegram bot webhook to the Function URL via `setWebhook` (reuse existing `dgonorOpenClawbot` token, since it already trusts that channel) — alternative: use a second bot if separation is desired
- [ ] 3.4 Add `/status` command returning `provisioningState` + `instanceView.state`
- [ ] 3.5 In-band stop: install `azure-cli` in the wrapper Dockerfile; grant the container's identity `Container Instance Contributor`; add a small openclaw skill `aci-control` with `stop`/`status` commands callable from chat
- [ ] 3.6 Document the iOS Shortcut → Telegram one-tap flow in the guide

## Task 4: LLM-Wiki via bundled `memory-wiki` plugin (P1)
- **Status:** pending
- **Depends on:** Task 1
- **Docs:** [roadmap.md#7-llm-wiki-on-existing-lancedb](./roadmap.md#7--llm-wiki-on-existing-lancedb)

### Subtasks
- [ ] 4.1 Add `memory-wiki` plugin to `config.json` with `mode: "bridge"`, `vault: "/mnt/openclaw-workspace/wiki"`
- [ ] 4.2 Seed vault: `entities/`, `concepts/`, `syntheses/`, `sources/`, `reports/` directories + a starter `AGENTS.md` describing ingest/lint/compile workflow
- [ ] 4.3 Verify `openclaw wiki ingest <path>` round-trips through `bridge import` from `memory-lancedb`
- [ ] 4.4 Document the day-to-day workflow (ingest → compile → search) in the setup guide

## Task 5: File ingestion (PDF, image, DOCX, XLSX) (P1)
- **Status:** pending
- **Depends on:** Task 4 (vault as ingestion target)
- **Docs:** [roadmap.md#3-file-ingestion--pdf-img-docx-xlsx-via-telegram--ui](./roadmap.md#3--file-ingestion--pdf-img-docx-xlsx-via-telegram--ui)

### Subtasks
- [ ] 5.1 Verify image attachments via Telegram already work end-to-end with `Kimi-K2.6-1` (smoke test: send a screenshot)
- [ ] 5.2 Author `pdf-extract` skill (Python: `pdfplumber` or `pypdf` → markdown); install via `openclaw skills install`
- [ ] 5.3 Author `office-extract` skill (Python: `python-docx`, `openpyxl`)
- [ ] 5.4 Wire skills to write extractions to `/mnt/openclaw-workspace/inbox/` and trigger `openclaw wiki ingest` automatically when source-quality is high enough (heuristic, gated)

## Task 6: Adaptive routing — `model-router-1` as default + per-skill overrides (P2)
- **Status:** pending
- **Depends on:** Task 0 (SMB lock fix — see "Pre-req" below)
- **Docs:** [roadmap.md#4-adaptive-routing-via-model-router-1](./roadmap.md#4--adaptive-routing-via-model-router-1)

### Subtasks
- [ ] 6.1 Update `agents.defaults.model.id` to `microsoft-foundry/model-router-1`; add `Kimi-K2.6-1` + a code-strong model to `fallbacks`
- [ ] 6.2 Define agent-level overrides for at least one specialised agent (e.g., `vision` → multimodal Kimi)
- [ ] 6.3 Document `/model <id>` manual-override pattern in the setup guide
- [ ] 6.4 After Topic-0 fix: re-test that `model-router-1` no longer silently falls back

## Task 7: Local docker dev parity (P2)
- **Status:** pending
- **Depends on:** Task 2
- **Docs:** [roadmap.md#5-local-docker-run-still-wired-to-azure-files--lancedb](./roadmap.md#5--local-docker-run-still-wired-to-azure-files--lancedb)

### Subtasks
- [ ] 7.1 Add `local-up.sh` that mirrors `deploy.sh`'s env wiring but runs `docker run -d` against the local Docker daemon
- [ ] 7.2 Decide workspace strategy: local-only folder vs. shared `openclaw-workspace-dev` Azure Files share — go with local-only by default
- [ ] 7.3 Verify LanceDB `az://` connection works identically from laptop
- [ ] 7.4 Document the local dev loop in `openclaw-deploy/README.md`

## Task 8: Voice — verify dictation + design telephony (P2)
- **Status:** pending
- **Depends on:** —
- **Docs:** [roadmap.md#2-voice--ui-dictation-telephony-foundry-stt](./roadmap.md#2--voice--ui-dictation-telephony-foundry-stt)

### Subtasks
- [ ] 8.1 Verify `talk-voice` browser dictation works in the current Control UI (no code; just smoke test)
- [ ] 8.2 If wanted later: design `voice-call` config with Twilio + OpenAI streaming STT (parking-lot until needed)

## Task 9: ACP harnesses — Gemini (YouTube), Codex / OpenAI (image gen), Claude Code, Copilot CLI (P1)
- **Status:** pending
- **Depends on:** Task 2 (Azure Files `_state/` pattern + Bicep)
- **Docs:** [roadmap.md#10-acp-harnesses--claude-code-copilot-cli-codex-gemini-cli-cursor-droid](./roadmap.md#10--acp-harnesses--claude-code-copilot-cli-codex-gemini-cli-cursor-droid)

### Subtasks — Phase A (Gemini for YouTube)
- [ ] 9.A.1 Add `gemini-cli` install to the wrapper Dockerfile
- [ ] 9.A.2 Extend `openclaw-init.sh` to symlink `~/.config/gemini` → Azure Files `_state/gemini/`
- [ ] 9.A.3 First-time auth: run `az container exec ... gemini auth` once; confirm token persists across redeploys
- [ ] 9.A.4 Smoke test: `/acp spawn gemini` then ask it to summarise a YouTube URL
- [ ] 9.A.5 Author a `youtube-summary` openclaw skill that detects a YT URL in Telegram and routes it to a Gemini ACP session

### Subtasks — Phase B (Image generation)
- [ ] 9.B.1 Register `openai/gpt-image-1` (and/or `microsoft-foundry/...` equivalent) as a model in `config.json` providers
- [ ] 9.B.2 Add an `image-gen` skill that calls the image model on user request; verify image returns through Telegram
- [ ] 9.B.3 Optional: install Codex CLI + enable the native `codex` plugin for multi-step image edit workflows; otherwise defer
- [ ] 9.B.4 Decide which path stays — direct model (preferred) vs Codex harness; document in the guide

### Subtasks — Phase C (Claude Code + Copilot CLI for tier-based coding)
- [ ] 9.C.1 Add `claude` and `copilot` CLI installs to the wrapper Dockerfile
- [ ] 9.C.2 Symlink `~/.config/claude` and `~/.config/github-copilot` → Azure Files `_state/`
- [ ] 9.C.3 OAuth dance once per harness; verify token persistence
- [ ] 9.C.4 Define a `coder` agent in `config.json` that prefers `/acp spawn claude` for large-context tasks
- [ ] 9.C.5 Document the harness-selection mental model in the setup guide

## Task 0b: Bump session-lock acquire timeout 10s → 60s
- **Status:** pending
- **Depends on:** —
- **Docs:** [roadmap.md#topic-0b--bump-session-lock-timeout](./roadmap.md#topic-0b--bump-session-lock-timeout)

### Subtasks
- [ ] 0b.1 Inspect the running container's effective config (`/acp doctor` or `openclaw config show`) to find the exact key for session-lock acquire timeout
- [ ] 0b.2 Add the override in `~/openclaw/config.json` setting it to 60000ms
- [ ] 0b.3 Redeploy and verify: under concurrent CLI + UI load, no more `SessionWriteLockTimeoutError` in logs

## Task 0 (parking lot): Azure Files NFS Premium migration
- **Status:** parked
- **Depends on:** —
- **Docs:** [roadmap.md#topic-0--underlying-constraint-to-remember-parking-lot](./roadmap.md#topic-0--underlying-constraint-to-remember-parking-lot)
- **Note:** Revisit only if Task 0b is insufficient or if multi-user scale becomes a concern. Keeping SMB for Mac connectivity is the explicit reason this is parked.

## Final verification
- **Status:** pending
- **Depends on:** Tasks 1, 2, 3, 4, 5, 9
- [ ] V.1 Run `testing-process` skill across the active workstream
- [ ] V.2 Run `documentation-process` skill to update `docs/guides/openclaw-azure-setup-guide.md` and `openclaw-deploy/README.md`
- [ ] V.3 Run `solid-code-review` skill on the cumulative diff (focus: shell scripts, Bicep, Function code)
