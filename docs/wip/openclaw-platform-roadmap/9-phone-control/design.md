# Topic 9 — Phone-based ACI start/stop

> Parent: [`../roadmap.md#9`](../roadmap.md#9--phone-based-aci-startstop)
> Companion: [`./implementation.md`](./implementation.md)

## Problem

To save on ACI compute, we want to stop the container when not in use and start it on demand. Today that means: open Azure portal on a laptop, navigate to the container group, click Start. Or `az` CLI from a laptop. Neither works from a phone in 2 seconds.

## Goal

From the iPhone, **start** or **stop** the ACI container in one tap, without:

- opening the Azure portal,
- having a laptop nearby,
- managing service principal secrets on the phone,
- paying for a 24/7 controller VM.

Status check ("is it running?") should also be one tap.

## Approach

**Azure Function (HTTP trigger) + Telegram bot webhook + system-assigned managed identity.**

```
iPhone Shortcut OR Telegram message
            │
            ▼
   Telegram Bot (existing @dgonorOpenClawbot)
            │ webhook
            ▼
   Azure Function /command
     ├─ verifies Telegram update signature
     ├─ verifies sender chat_id is allowlisted
     ├─ parses /start /stop /status
     │
     ├─ uses system-assigned managed identity
     │   with role: "Container Instance Contributor"
     │   scoped to the resource group
     │
     ├─ calls az container start|stop|show
     └─ replies via Telegram bot API
```

### Why a Function (not Logic App)

- **Cost:** Consumption plan is free up to 1M executions/month. We'll use ~30/month.
- **Auth:** managed identity → no SP secret rotation. Logic App can do MI too but is more expensive per execution.
- **Code-first:** small JS or Python file, easy to debug locally and version in `openclaw-deploy`.

### Why reuse the existing Telegram bot

We already trust `@dgonorOpenClawbot`. A second bot would mean a second token and a second chat to remember. Reusing the same bot for control commands keeps the user surface unified — but **only when the message is `/start_aci` / `/stop_aci` / `/status_aci`** (custom command names that don't clash with OpenClaw's own `/` commands when the container is running).

When the container is **down**, the OpenClaw gateway isn't there to receive Telegram updates. Telegram retries the webhook. By pointing the webhook at the Azure Function instead of (or in addition to) OpenClaw's gateway, we ensure control commands are always received.

### Webhook routing problem (and solution)

Telegram only allows one webhook per bot. So we have two options:

1. **Function-only webhook:** Telegram → Function. Function dispatches `/start_aci` etc., and forwards everything else to the OpenClaw gateway URL (when the container is running). This makes the Function a permanent middleware in the message path, but it's tiny code (~20 lines) and zero added latency on the Function-pass-through path.
2. **Toggle webhook based on container state:** when starting, switch webhook to OpenClaw; when stopping, switch webhook to Function. Brittle, race-prone, hard to debug.

**Decision: Option 1 — Function as permanent middleware.**

```
Telegram ──▶ Function ──┬─ container-control commands handled here
                        └─ everything else forwarded to https://openclaw.posthub.cc/api/v1/webhook/telegram
```

This also means the Function can synthesise a "container is starting up, please wait ~30s" reply when the user sends a regular message and the container is stopped — better UX than silent timeout.

### iOS Shortcut layer

For one-tap control without typing in Telegram, an iOS Shortcut posts directly to the Function URL with a shared-secret query param:

```
Shortcut "Start OpenClaw" ──▶ HTTP POST https://func.../command?secret=...&action=start
```

Same Function endpoint accepts both Telegram updates AND raw `?action=` calls. Two auth modes:

- **Telegram mode:** verify update via Telegram secret token (set on `setWebhook`).
- **Direct mode:** verify shared secret in query string + restrict to a specific source IP if Apple's Shortcuts service publishes a stable range (it doesn't — accept this risk and rotate the secret periodically, or use a Cloudflare Worker as a known source).

## Capacity and cost

- Consumption plan Function: free tier 1M execs + 400k GB-s. Our usage is far below.
- Telegram webhook traffic: free.
- Cloudflare Worker (optional, for Shortcut→Function authentication): free tier.

Net new monthly cost: **$0**.

## In-band stop via openclaw skill

When the container is **already up**, stopping it via Telegram doesn't actually require the Function — the running OpenClaw can call `az container stop` itself if:

1. `azure-cli` is installed in the wrapper image.
2. The container's identity has `Container Instance Contributor` on the RG.
3. A skill (`aci-control`) tells the agent to run `az container stop --no-wait` when the user says "shut down".

This gives unified UX with the bot for stop/status, while `/start` still has to go through the Function (chicken-and-egg: container can't start itself).

## Risks

- **Telegram update injection** if the secret token isn't verified — anyone could fake a Telegram update. Mitigation: set `secret_token` on `setWebhook` and verify it on every request.
- **Shared secret leakage** for the iOS Shortcut path. Mitigation: rotate periodically, document it in the runbook.
- **Race during stop:** user issues `/stop_aci` mid-conversation; the in-flight reply gets cut. Mitigation: stop is `--no-wait`, but reply confirms "stopping in 60s" so user sees graceful cutoff.
- **Function cold-start:** Consumption plan Functions have ~1-3 s cold start in Node.js. Acceptable for control plane; users will see "received, processing…" within 3 s.

## Out of scope

- Auto-stop on idle. Could be a follow-up: a Logic App on a 30-min timer that checks `instanceView.events` for last activity and stops if idle. Not needed for first cut — manual stop via phone is enough.
- Per-user authorization beyond a single allowlisted chat_id.
- A web UI for control. Telegram + Shortcuts cover all our use cases.
