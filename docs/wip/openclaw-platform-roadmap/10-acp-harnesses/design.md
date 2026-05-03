# Topic 10 — ACP harnesses (Gemini, Codex, Claude Code, Copilot)

> Parent: [`../roadmap.md#10`](../roadmap.md#10--acp-harnesses--claude-code-copilot-cli-codex-gemini-cli-cursor-droid)
> Companion: [`./implementation.md`](./implementation.md)

## Problem & opportunity

We pay for several AI subscriptions: Azure Foundry, ChatGPT Plus / Codex, GitHub Copilot Pro, and want to evaluate Claude Code Max. Without ACP harnesses, every model call goes through a single provider (Foundry today) and we can't use the *capabilities* unique to each ecosystem:

- **Gemini CLI** has native YouTube ingestion (paste a URL, it fetches transcript + frames).
- **OpenAI** has `gpt-image-1` for image generation.
- **Claude Code** has the strongest large-context refactoring loop and uses Anthropic auth.
- **Copilot CLI** rides the GitHub Copilot subscription we already pay for.

The bundled `acpx` plugin already lets OpenClaw spawn any of these as a sub-session via `/acp spawn <id>` — we saw `acpx` in our running container's plugin list. What's missing: the harness binaries themselves and persistent OAuth state inside the container.

## Goal

Three phased capabilities, each independently shippable:

- **Phase A** — paste a YouTube link in Telegram → Gemini summarises + answers follow-ups.
- **Phase B** — ask for "generate an image of …" → returns image via Telegram, billed to OpenAI subscription.
- **Phase C** — large-context coding tasks routed to Claude Code; default coding to Copilot CLI; both billed to subscription auth, not Foundry tokens.

Each phase reuses the same plumbing pattern — install CLI in the wrapper image, persist auth via Azure Files `_state/` symlink, do OAuth dance once.

## Architecture

```
                          OpenClaw container
                          ┌───────────────────────────────┐
Telegram / UI ──gateway──▶│ openclaw runtime              │
                          │   ├ acpx plugin (loaded)      │
                          │   │   ├ /acp spawn gemini ──▶ │ gemini-cli ─OAuth─▶ Google
                          │   │   ├ /acp spawn claude ──▶ │ claude-cli ─OAuth─▶ Anthropic
                          │   │   ├ /acp spawn copilot ──▶│ copilot-cli ─OAuth─▶ GitHub
                          │   │   └ /acp spawn codex ──▶  │ codex-cli ─OAuth──▶ OpenAI
                          │   └ skills:                   │
                          │      ├ youtube-summary ──▶ /acp spawn gemini
                          │      └ image-gen ──▶ openai/gpt-image-1 (direct model)
                          │                               │
                          │ Azure Files mount             │
                          │   _state/gemini/              │ ◀── persisted OAuth tokens
                          │   _state/claude/              │
                          │   _state/copilot/             │
                          │   _state/codex/               │
                          └───────────────────────────────┘
```

Each `_state/<harness>/` symlinks to the corresponding `~/.config/<tool>/` (or `~/.<tool>/`) inside the container. OAuth tokens written there persist across container restarts.

## Selection logic

Two layers:

1. **Skills** decide which harness to spawn for a specific intent. `youtube-summary` skill checks if the message contains a YT URL → spawn Gemini. `image-gen` skill detects "generate image" / "draw" / etc. → call `openai/gpt-image-1` model directly.
2. **Agents** can declare a preferred harness via `agents.list[].runtime.acp.agent`. E.g., a `coder` agent prefers `claude` for tasks > X tokens, `copilot` for the rest.

This keeps adaptive routing readable: each routing decision lives in one named place (a skill or an agent definition), not in a prompt classifier.

## Phase A — Gemini for YouTube

**Why first:** highest single payoff, smallest blast radius. Gemini handles the YouTube transcript fetch; we just route the URL.

Flow:
1. User sends `https://youtube.com/watch?v=…` in Telegram.
2. `youtube-summary` skill matches the URL.
3. Skill calls `sessions_spawn({ runtime: "acp", agentId: "gemini", initialPrompt: "Summarise this video and stay loaded for follow-ups: $URL" })`.
4. Gemini runs as a background ACP session bound to the current chat. Follow-ups go through the same session.

## Phase B — Image generation via OpenAI

Two routes considered:

| Route | How | Trade-offs |
|---|---|---|
| **Direct model** | Register `openai/gpt-image-1` in `config.json` providers. `image-gen` skill calls the model with the user's prompt. | Simpler, no harness, faster. Multi-step image edits clunky. |
| **Codex ACP** | `/acp spawn codex` then converse about the image. Native Codex app-server (`/codex bind`) is even tighter. | Multi-step edit flows feel natural. More moving parts. |

**Decision:** start with direct-model. Add Codex later only if multi-step image-editing becomes a real workflow.

## Phase C — Claude Code + Copilot CLI for tier-based coding

Once Phase A + B work, this is the same plumbing pattern × 2 tools. The `coder` agent gets:

```jsonc
"agents": {
  "list": [{
    "id": "coder",
    "runtime": { "acp": { "agent": "claude" } },
    "model": { "primary": "...", "fallbacks": [] }
  }]
}
```

User invokes via `/agent coder` or via skill routing. The skill picks `claude` vs `copilot` based on context size or task type.

## Don'ts

- **Don't** bake OAuth tokens into the docker image — short-lived + secrets in image.
- **Don't** expose openclaw's internal MCP tools to harnesses by default. Per docs, opt-in only via `acp-agents-setup`. Default-off keeps the security blast radius small.
- **Don't** run all four harnesses simultaneously in production until we've shaken out auth-state contention. Phase per phase.
- **Don't** build a prompt-classifier that picks harness — let skills/agents own it explicitly.

## Validation per phase

- **A:** send a YT URL to the bot, get back a relevant summary; ask "what did the speaker say about X" and get a coherent follow-up referencing the video.
- **B:** send "generate an image of an octopus on a unicycle" → image arrives in Telegram. Inspect token cost in OpenAI dashboard.
- **C:** ask the `coder` agent to refactor a 10-file Python project. Confirm the work happened via Claude Code (check Anthropic dashboard for Claude Code Max usage tick).

## Risks

- **OAuth refresh inside container:** if the OAuth flow tries to open a browser on the *container* rather than letting us do device-flow, we have to dig into the harness's auth options. Mitigation: prefer harnesses that support device-flow / API-key auth; document the per-harness procedure.
- **Token leakage to ACR images** if we accidentally `COPY ~/.config` into the build. Mitigation: `.dockerignore` lists every `_state/**` and `~/.config/**` pattern.
- **Concurrent ACP sessions vs SMB lock issue (Topic 0):** ACP sessions write their own session JSONLs. More writers → more lock contention. Mitigation: Topic 0b timeout bump.

## Out of scope (for first cut)

- Cursor / Droid / iFlow / Kilocode / Kiro / Kimi / OpenCode / Qwen harnesses. Easy to add later, not a P1 capability for now.
- MCP bridge config (`acp-agents-setup`). Default-off; revisit only if a harness genuinely needs back-call.
- Native `codex` plugin (separate from ACP). Only adopt if Phase B's direct-model route proves insufficient.
