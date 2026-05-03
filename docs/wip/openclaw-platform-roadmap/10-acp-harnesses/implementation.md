# Implementation — ACP harnesses

> Design: [`./design.md`](./design.md)
> Repo home: `openclaw-deploy` (Topic 8 must land first or in parallel — modifications happen in `docker/Dockerfile`, `docker/openclaw-init.sh`, `config/config.json`, and a new `skills/` folder we add to the repo).

## Phase A — Gemini for YouTube

### A.1 Install Gemini CLI in the wrapper image

In `docker/Dockerfile`, after the existing `apt-get install` block:

```dockerfile
# Gemini CLI (npm-published)
RUN npm install -g @google/gemini-cli@latest
```

(If Gemini CLI ships as a different package name at runtime — check `npm view @google/gemini-cli` first; some Google CLIs ship via Homebrew or curl-bash. Adjust install command accordingly.)

Bump `WRAPPER_REV` in `env.sh` (e.g. `1` → `2`).

### A.2 Persist auth via `_state/`

In `docker/openclaw-init.sh`, after the existing `_state/` symlinks for `agents` etc., add:

```bash
# Gemini CLI auth state
mkdir -p /mnt/openclaw-workspace/_state/gemini
ln -sfn /mnt/openclaw-workspace/_state/gemini /home/node/.config/gemini
chown -h node:node /home/node/.config/gemini
```

(Adjust target path if Gemini CLI uses `~/.gemini` or `~/.config/google-gemini` — confirm with `gemini --help` once installed.)

### A.3 First-time auth (operator runbook)

After deploy, exec into the container and authenticate Gemini once:

```bash
source scripts/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "gosu node gemini auth"
```

Follow whatever flow Gemini prints (device code → visit URL → paste code on laptop → token written into `~/.config/gemini/`, which is the symlink to `_state/gemini/` → persisted on Azure Files).

Verify token persists across redeploy:

```bash
scripts/deploy.sh   # restart the container
az container exec ... --exec-command "gosu node gemini auth status"
# Expected: still authenticated.
```

### A.4 Smoke test ACP spawn

```bash
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "gosu node openclaw acp doctor"
# Expected: "gemini: ready" or similar healthy line.

# In the openclaw UI chat:
/acp spawn gemini
# then: "summarise https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

If the response references the actual video content, plumbing is correct.

### A.5 `youtube-summary` skill

OpenClaw skills live in `agents/<agentId>/skills/<skill-name>/` (per [docs.openclaw.ai/concepts/skills](https://docs.openclaw.ai/concepts/skills.md)). In the new `openclaw-deploy` repo, add `config/skills/youtube-summary/`:

`SKILL.md`:

```markdown
---
name: youtube-summary
description: When the user shares a YouTube URL, spawn a Gemini ACP session to summarise the video and stay loaded for follow-up questions.
trigger: regex
pattern: 'youtube\.com/watch\?v=|youtu\.be/'
---

When you detect a YouTube URL in the user's message, call `sessions_spawn` with
`runtime: "acp"`, `agentId: "gemini"`, and an initial prompt that asks Gemini to
summarise the video and keep state for follow-ups. Bind the spawned session to the
current chat so subsequent messages route through it.
```

Mount the skill into the container by adding it to `_state/agents/<agentId>/skills/` via the init script, or by configuring `agents.list[].skillsDir` in `config.json` to point at a workspace path that ships with the deploy.

### A.6 End-to-end test

From your phone, send a YouTube URL to `@dgonorOpenClawbot`. Expected: bot replies with a summary citing video content. Send "what was the third point?" — should answer using the same Gemini session.

---

## Phase B — Image generation

### B.1 Register `gpt-image-1` as a model

In `config/config.json`, under `providers`:

```jsonc
"providers": {
  "openai": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "${OPENAI_API_KEY}",
    "models": [
      { "id": "gpt-image-1", "kind": "image", "input": ["text"], "output": ["image"] }
    ]
  }
}
```

Add `OPENAI_API_KEY` to Key Vault (`openai-api-key` secret) and to `scripts/env.sh`'s hydration list.

### B.2 `image-gen` skill

`config/skills/image-gen/SKILL.md`:

```markdown
---
name: image-gen
description: Generate an image when the user asks to create/draw/imagine a picture.
trigger: regex
pattern: '\b(generate|draw|create|imagine|render)\b.*\b(image|picture|photo|illustration)\b'
---

Use the `openai/gpt-image-1` model directly. Pass the user's request as the prompt.
Return the resulting image in the conversation. For follow-up edits, ask the user
to describe the desired change and re-call the model with a refined prompt.
```

### B.3 Smoke test

Send "generate an image of an octopus juggling hammers" via Telegram. Image should arrive within a few seconds. Cross-check usage in OpenAI's dashboard.

### B.4 Defer Codex ACP unless needed

If multi-step image edits become a real workflow, revisit `/acp spawn codex` or the native `codex` plugin. Until then, direct-model is the path.

---

## Phase C — Claude Code + Copilot CLI

### C.1 Install both CLIs

In `docker/Dockerfile`:

```dockerfile
# Claude Code
RUN npm install -g @anthropic-ai/claude-code@latest

# GitHub Copilot CLI
RUN curl -fsSL https://raw.githubusercontent.com/github/copilot-cli/main/install.sh | bash \
    && mv ~/.local/bin/copilot /usr/local/bin/copilot
```

(Confirm exact install commands against each tool's docs at implementation time.)

Bump `WRAPPER_REV`.

### C.2 Persist auth state

`docker/openclaw-init.sh`:

```bash
for tool in claude github-copilot; do
  mkdir -p /mnt/openclaw-workspace/_state/$tool
  ln -sfn /mnt/openclaw-workspace/_state/$tool /home/node/.config/$tool
  chown -h node:node /home/node/.config/$tool
done
```

### C.3 First-time OAuth per harness

```bash
# Claude
az container exec ... --exec-command "gosu node claude auth login"
# Copilot
az container exec ... --exec-command "gosu node copilot auth login"
```

Follow device-flow URLs printed.

### C.4 Verify ACP doctor

```bash
az container exec ... --exec-command "gosu node openclaw acp doctor"
# Expected: claude: ready, copilot: ready
```

### C.5 `coder` agent definition

In `config/config.json`:

```jsonc
"agents": {
  "list": [{
    "id": "coder",
    "name": "Coder",
    "description": "Coding tasks. Default to Copilot CLI; use Claude Code for large-context tasks.",
    "runtime": { "acp": { "agent": "copilot" } },
    "model": {
      "primary": { "provider": "microsoft-foundry", "id": "model-router-1" },
      "fallbacks": []
    },
    "skills": ["claude-handoff"]
  }]
}
```

`claude-handoff` skill (in `config/skills/claude-handoff/SKILL.md`):

```markdown
---
name: claude-handoff
description: When a coding task involves more than ~5 files or appears to need long-context reasoning, hand off to Claude Code via /acp spawn claude.
trigger: agent-only
agentId: coder
---

Heuristic: if the user references > 5 files, or asks for a project-wide refactor,
or the conversation already contains > 30 KB of code context, spawn a Claude Code
ACP session and route the task there. Otherwise stay on Copilot.
```

### C.6 End-to-end test

`/agent coder` then "explain the auth flow in this repo" (small) → should answer via Copilot. Then "refactor every util module to use async/await" (large) → should hand off to Claude.

Verify via Anthropic / GitHub usage dashboards that each subscription is being billed correctly.

---

## Cross-phase concerns

### `.dockerignore`

Add:

```
**/_state/**
**/.config/**
env.sh
.upstream-version
.last-build
```

So secrets and OAuth tokens never end up in build context.

### Image size

Each CLI install adds ~50-200 MB. Total wrapper image growth across all four harnesses: ~500 MB-1 GB. Acceptable for ACI; revisit if it becomes a deploy-time pain point (multi-stage builds, harness-on-demand install via init script, etc.).

### Auth rotation

OAuth tokens refresh automatically as long as the containers run regularly. If a container is stopped for > refresh window, log in again. Track expiry in the runbook.

### ACP doctor as health check

Add `openclaw acp doctor` to `deploy.sh`'s post-deploy verification block (before token-print). If any expected harness reports unhealthy, print a warning telling the user to re-auth.

## Phase order recap

A → B → C, each independently deployable. After A is stable in production for a few days, ship B. After B is stable, ship C.
