# OpenClaw on Azure Container Instances — Maintainable Runbook

A reproducible, version-controllable approach to running OpenClaw on Azure Container Instances (ACI), with HTTPS via Cloudflare Tunnel, token auth, Brave web search, durable workspace files, and shared LanceDB memory on Azure Blob Storage.

The container is treated as **disposable**. Configuration lives in local files; runtime state lives on durable Azure storage. After every redeploy the container reconstructs itself from these sources.

> **Auth model**: token over public HTTP. Acceptable for personal use over trusted networks. Avoid using on hostile networks (public wifi etc.) since the token rides plaintext. For stricter requirements, see the "Hardening" section.

---

## Architecture

```
Browser / Telegram
    ↓
https://openclaw.posthub.cc/          (HTTPS via Cloudflare Tunnel — use for Control UI)
    or
http://openclaw.eastus.azurecontainer.io:18789/   (direct HTTP — use for CLI/token fetch)
    ↓
ACI container "openclaw-container-dgonor"
  ├─ Custom entrypoint (openclaw-init.sh):
  │     1. decodes OPENCLAW_CONFIG_B64 → /home/node/.openclaw/openclaw.json
  │     2. symlinks ~/.openclaw/{agents,identity,tasks,canvas,telegram} → Azure Files _state/
  │     3. starts cloudflared tunnel in background (HTTPS)
  │     4. drops to node user, execs openclaw gateway
  ├─ cloudflared (background, pid 1-ish)
  │     → Cloudflare edge (4 redundant QUIC connections) → https://openclaw.posthub.cc/
  ├─ openclaw gateway (bind=lan, runs as node user)
  │     ├─ brave plugin → Brave Search API (web search for agents)
  │     ├─ memory-lancedb plugin → az://openclaw-memory/lancedb
  │     ├─ Telegram plugin (outbound polling)
  │     └─ workspace at /mnt/openclaw-workspace
  ├─ Volume mount: Azure Files share "openclaw-workspace"
  │     ├─ SOUL.md, USER.md, AGENTS.md, MEMORY.md, etc.
  │     └─ _state/{agents,identity,tasks,canvas,telegram}  ← symlinked from ~/.openclaw/
  └─ Models: Kimi-K2.6-1 (default), model-router-1 (speed comparison)
```

**Four data layers, all durable across container recreations:**

| Layer | Where | Survives `az container delete`? |
|---|---|---|
| Config (`openclaw.json`) | Baked into `OPENCLAW_CONFIG_B64` env var, written at boot by entrypoint | ✓ — comes from local `config.json` |
| Workspace files (`SOUL.md`, `USER.md`, etc.) | Azure Files share `openclaw-workspace` mounted at `/mnt/openclaw-workspace` | ✓ |
| Memory index (vectors) | Azure Blob `az://openclaw-memory/lancedb` via `memory-lancedb` plugin | ✓ |
| Session/runtime state (`agents/`, `identity/`, `tasks/`, `canvas/`, `telegram/`) | Azure Files share `openclaw-workspace/_state/` via symlinks set up at boot by entrypoint | ✓ — entrypoint symlinks `/home/node/.openclaw/<subdir>` → mount |

**Note on the gateway token**: the token is stored in the writable container filesystem and regenerates on every redeploy. It does **not** survive `az container delete`. Re-fetch it with the command in the "Getting the gateway token" section after each deploy. Device pairing records survive because they live in `identity/` which is symlinked to Azure Files.

**Three workflows:**

1. **Build image** (`build-image.sh`) — when the wrapper Dockerfile or entrypoint script changes, or you bump the upstream openclaw image tag (`BASE_TAG`).
2. **Deploy** (`deploy.sh`) — recreates the container with current `config.json` baked in. Used for plugin enablement changes, image updates, or secret rotation. Re-fetch the gateway token after each deploy.
3. **Hot-reload** (in-place edit) — for runtime-mutable config like the Telegram allowlist. Won't work for `gateway.*` changes (those require restart).

---

## Prerequisites

- **Azure CLI** installed and logged in (`az login`)
- **Azure Container Registry** with the upstream `openclaw:v3` image available (or whatever current upstream tag you want as the base)
- **Azure OpenAI / Foundry** deployment with both:
  - One or more chat models (e.g. `Kimi-K2.6-1`, `model-router-1`)
  - A text-embedding model deployment (e.g. `text-embedding-3-small-1`)
- **Telegram bot token** from [@BotFather](https://t.me/BotFather), and your Telegram user ID from [@userinfobot](https://t.me/userinfobot)
- **Docker daemon running locally** *only* if you need to push a base image to ACR (most users won't — they pull a tagged image directly into ACR via `az acr import` or have it pushed by upstream)

---

## One-time Azure setup

### 1. Create the Azure Files share for workspace persistence

```bash
az storage share-rm create \
  --name openclaw-workspace \
  --storage-account openclawstoragedgonor \
  --resource-group myCentralRG \
  --quota 5
```

### 2. Confirm the Azure Blob container for memory exists

The `memory-lancedb` plugin uses `az://openclaw-memory/lancedb`. Create the container if not already present:

```bash
az storage container create \
  --name openclaw-memory \
  --account-name openclawstoragedgonor \
  --auth-mode key
```

### 3. Push the upstream openclaw image to ACR (if not already there)

The fastest path is `az acr import`, which streams the image registry-to-registry without needing a local Docker daemon:

```bash
az acr import \
  --name acrdgonor2 \
  --source ghcr.io/openclaw/openclaw:2026.4.26-slim \
  --image openclaw:v3
```

If you already have the openclaw base image locally tagged for your ACR:

```bash
az acr login --name acrdgonor2
docker push acrdgonor2.azurecr.io/openclaw:v3
```

If you only have a SHA reference, tag it first:

```bash
docker tag <sha256:...> acrdgonor2.azurecr.io/openclaw:v3
docker push acrdgonor2.azurecr.io/openclaw:v3
```

> **On choosing `BASE_TAG`**: this guide uses `v3` (openclaw 2026.4.26, `bookworm-slim` runtime) which is known to work end-to-end with `azure-openai-responses` API, `storageOptions` for memory-lancedb, and the standard plugin layout. When upstream publishes a newer tag (`v4`, etc.), bump `BASE_TAG` in `env.sh` and run `build-image.sh` + `deploy.sh`. Avoid trying to `npm install -g openclaw@latest` on top of an older base image — the npm package puts plugins at a different path and the plugin loader can't find them, which breaks Control UI device pairing among other things.

---

## Local working directory

Everything below lives at `~/openclaw/` on your machine. This directory is the source of truth.

```
~/openclaw/
├── env.sh              # Secrets + Azure variables (NOT committed)
├── config.json         # OpenClaw runtime config (safe to commit — no secrets)
├── build-image.sh      # Builds the custom wrapper image via az acr build
├── deploy.sh           # Recreates the ACI container with current config baked in
└── docker/
    ├── Dockerfile      # FROM openclaw:v3 + gosu + entrypoint script
    └── openclaw-init.sh # Entrypoint: decodes config, drops to node, exec openclaw
```

### `~/openclaw/env.sh`

> Replace placeholder secrets with your real values. Add this path to your global gitignore.

```bash
mkdir -p ~/openclaw
cat > ~/openclaw/env.sh << 'EOF'
# Azure
export SUBSCRIPTION=d7e7222e-bf82-4b07-b21c-f8c7d06b3607
export RG=myCentralRG
export LOCATION=eastus

# ACR + images
# BASE_TAG  = upstream openclaw image tag (used as FROM in our Dockerfile)
# CUSTOM_TAG = our wrapper image tag (built by build-image.sh, deployed by deploy.sh)
# IMAGE_TAG = which tag deploy.sh uses (set to CUSTOM_TAG once the wrapper is built)
export ACR_NAME=acrdgonor2
export BASE_TAG=v3
export CUSTOM_TAG=custom-v3-1
export IMAGE_TAG="$CUSTOM_TAG"
export IMAGE="${ACR_NAME}.azurecr.io/openclaw:${IMAGE_TAG}"

# Workspace storage (Azure Files share for SOUL.md, USER.md, MEMORY.md, etc.)
export WORKSPACE_SHARE=openclaw-workspace
export WORKSPACE_MOUNT=/mnt/openclaw-workspace

# Container
export CONTAINER=openclaw-container-dgonor
export DNS_LABEL=openclaw

# Resources
export CPU=2
export MEMORY=4

# Secrets — REPLACE WITH YOUR REAL VALUES
export AZURE_OPENAI_API_KEY="<paste-azure-openai-key>"
export TELEGRAM_BOT_TOKEN="<paste-telegram-bot-token>"
export AZURE_STORAGE_ACCOUNT="openclawstoragedgonor"
export AZURE_STORAGE_KEY="<paste-storage-account-key>"
export BRAVE_SEARCH_API="<paste-brave-search-api-key>"    # from api.search.brave.com
export CF_TUNNEL_TOKEN="<paste-cloudflare-tunnel-token>"  # from one.dash.cloudflare.com → Networks → Tunnels
EOF

chmod 600 ~/openclaw/env.sh
```

Get the storage account key:
```bash
az storage account keys list \
  --account-name openclawstoragedgonor \
  --query "[0].value" -o tsv
```

### `~/openclaw/config.json`

Source of truth for non-secret OpenClaw config. Safe to commit. Note `models` lists multiple model deployments — the default is set by `agents.defaults.model` and individual models can be invoked by `openclaw infer model run --model <provider/id>`.

```bash
cat > ~/openclaw/config.json << 'EOF'
{
  "gateway": {
    "bind": "lan",
    "auth": {
      "mode": "token"
    },
    "trustedProxies": ["127.0.0.1", "::1"],
    "controlUi": {
      "allowInsecureAuth": true,
      "allowedOrigins": ["*"]
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "allowlist",
      "allowFrom": ["116798769"]
    }
  },
  "agents": {
    "defaults": {
      "model": "microsoft-foundry/Kimi-K2.6-1",
      "workspace": "/mnt/openclaw-workspace"
    }
  },
  "models": {
    "providers": {
      "microsoft-foundry": {
        "baseUrl": "https://dgonor-llm-api-resource.openai.azure.com/openai/v1",
        "api": "azure-openai-responses",
        "models": [
          {"id": "Kimi-K2.6-1", "name": "Kimi K2.6"},
          {"id": "model-router-1", "name": "Model Router"}
        ]
      }
    }
  },
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "maxResults": 5,
        "timeoutSeconds": 30
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memory-lancedb"
    },
    "entries": {
      "brave": {
        "enabled": true,
        "config": {
          "webSearch": {
            "apiKey": "${BRAVE_SEARCH_API}"
          }
        }
      },
      "memory-lancedb": {
        "enabled": true,
        "config": {
          "embedding": {
            "apiKey": "${AZURE_OPENAI_API_KEY}",
            "baseUrl": "https://dgonor-llm-api-resource.openai.azure.com/openai/v1",
            "model": "text-embedding-3-small-1",
            "dimensions": 1536
          },
          "dbPath": "az://openclaw-memory/lancedb",
          "storageOptions": {
            "account_name": "${AZURE_STORAGE_ACCOUNT}",
            "account_key": "${AZURE_STORAGE_KEY}"
          },
          "autoCapture": true,
          "autoRecall": true
        }
      }
    }
  }
}
EOF
```

**What every block does:**

| Block | Purpose |
|---|---|
| `gateway.bind: "lan"` | Listen on all network interfaces (so the ACI public IP is reachable). |
| `gateway.auth.mode: "token"` | Bearer-token authentication. Token is auto-generated on every fresh boot, fetched via the helper command below. |
| `gateway.controlUi.allowInsecureAuth: true` | Required for token auth over plain HTTP. Flagged as insecure by `openclaw security audit`; acceptable on trusted networks only. |
| `gateway.controlUi.allowedOrigins: ["*"]` | Wildcard CORS — fine since auth is the primary control. |
| `agents.defaults.model` | Default chat model. Set to `Kimi-K2.6-1` here; switch via UI or override per-conversation. |
| `agents.defaults.workspace: "/mnt/openclaw-workspace"` | Workspace files (SOUL.md, USER.md, etc.) land on the durable Azure Files mount. |
| `models.providers.microsoft-foundry.models[]` | List of available model deployments. Add more as objects with `{id, name}` to make them switchable. |
| `plugins.slots.memory: "memory-lancedb"` | Memory slot owner — required to actually activate the lancedb plugin. Enabling the entry alone isn't enough. |
| `plugins.entries.memory-lancedb.config.dbPath: "az://..."` | LanceDB index lives on Azure Blob Storage. Both your laptop and ACI can point to the same URI to share memory. |
| `plugins.entries.memory-lancedb.config.storageOptions` | Azure Blob credentials (account + key). Supports `${ENV_VAR}` substitution at OpenClaw startup. |
| `text-embedding-3-small-1 + dimensions: 1536` | Required because Azure deployment names don't match openclaw's hardcoded model→dim lookup. See gotchas below. |

### `~/openclaw/docker/Dockerfile`

```bash
mkdir -p ~/openclaw/docker
cat > ~/openclaw/docker/Dockerfile << 'EOF'
ARG BASE_IMAGE=acrdgonor2.azurecr.io/openclaw:v3
FROM ${BASE_IMAGE}

USER root

# Install gosu so the entrypoint can drop privileges back to the node user
# before exec'ing openclaw.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY openclaw-init.sh /usr/local/bin/openclaw-init.sh
RUN chmod +x /usr/local/bin/openclaw-init.sh

ENTRYPOINT ["/usr/local/bin/openclaw-init.sh"]

# Re-declare CMD: setting ENTRYPOINT in a child image RESETS the parent's CMD
# to empty. Without this, openclaw-init.sh gets no args and docker-entrypoint.sh
# exits cleanly (rc=0) → container restart loop with no openclaw process.
CMD ["node", "openclaw.mjs", "gateway", "--allow-unconfigured"]
EOF
```

### `~/openclaw/docker/openclaw-init.sh`

```bash
cat > ~/openclaw/docker/openclaw-init.sh << 'SCRIPT'
#!/bin/sh
set -e

log() { echo "[openclaw-init] $*"; }

# 1. Write OpenClaw config from env var
mkdir -p /home/node/.openclaw
if [ -n "$OPENCLAW_CONFIG_B64" ]; then
  echo "$OPENCLAW_CONFIG_B64" | base64 -d > /home/node/.openclaw/openclaw.json
  log "config written from OPENCLAW_CONFIG_B64 ($(wc -c < /home/node/.openclaw/openclaw.json) bytes)"
else
  log "OPENCLAW_CONFIG_B64 not set; OpenClaw will use auto-generated default config"
fi

# 2. Persist mutable state subdirs to the Azure Files mount via symlinks.
# These directories hold runtime data that we don't want to lose on container
# recreation: chat sessions, device identity (browser pairing), task state,
# canvas state, telegram channel state. Without these symlinks, every redeploy
# loses sessions and re-pairs every browser.
#
# Strategy: create _state/<subdir> on the durable mount, then symlink
# /home/node/.openclaw/<subdir> -> the mount. OpenClaw will discover existing
# data on first directory read, and any new writes flow to the mount.
if [ -d /mnt/openclaw-workspace ]; then
  STATE_DIR=/mnt/openclaw-workspace/_state
  mkdir -p "$STATE_DIR"
  for subdir in agents identity tasks canvas telegram; do
    SRC="$STATE_DIR/$subdir"
    DST="/home/node/.openclaw/$subdir"
    mkdir -p "$SRC"
    # Remove anything pre-existing at the symlink site (fresh container = nothing,
    # but in case of weirdness, clear it). Don't follow symlinks on rm.
    [ -e "$DST" ] || [ -L "$DST" ] && rm -rf "$DST"
    ln -s "$SRC" "$DST"
  done
  log "state subdirs symlinked → /mnt/openclaw-workspace/_state/{agents,identity,tasks,canvas,telegram}"
else
  log "WARNING: /mnt/openclaw-workspace not mounted — sessions will NOT persist across container recreation"
fi

chown -R node:node /home/node/.openclaw
log "workspace mount detected at /mnt/openclaw-workspace (azure files)"

# 3. Run openclaw via the original entrypoint, dropping privileges to node.
log "launching openclaw as node user: docker-entrypoint.sh $*"
gosu node docker-entrypoint.sh "$@"
RC=$?
log "openclaw exited with code $RC — sleeping 30s before container restart so logs flush"
sleep 30
exit "$RC"
SCRIPT
```

### `~/openclaw/build-image.sh`

```bash
cat > ~/openclaw/build-image.sh << 'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

: "${ACR_NAME:?ACR_NAME not set}"
: "${BASE_TAG:?BASE_TAG not set in env.sh (e.g. v3)}"
: "${CUSTOM_TAG:?CUSTOM_TAG not set in env.sh (e.g. custom-v3-1)}"

DOCKER_DIR="$(dirname "$0")/docker"

echo "==> Building $ACR_NAME.azurecr.io/openclaw:$CUSTOM_TAG (FROM openclaw:$BASE_TAG) via ACR Tasks"
az acr build \
  --registry "$ACR_NAME" \
  --subscription "$SUBSCRIPTION" \
  --image "openclaw:$CUSTOM_TAG" \
  --build-arg "BASE_IMAGE=$ACR_NAME.azurecr.io/openclaw:$BASE_TAG" \
  --file "$DOCKER_DIR/Dockerfile" \
  "$DOCKER_DIR"

echo
echo "==> Built. Run: ~/openclaw/deploy.sh"
SCRIPT

chmod +x ~/openclaw/build-image.sh
```

### `~/openclaw/deploy.sh`

```bash
cat > ~/openclaw/deploy.sh << 'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

[ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "AZURE_OPENAI_API_KEY not set"; exit 1; }
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || { echo "TELEGRAM_BOT_TOKEN not set"; exit 1; }
[ -n "${AZURE_STORAGE_ACCOUNT:-}" ] || { echo "AZURE_STORAGE_ACCOUNT not set"; exit 1; }
[ -n "${AZURE_STORAGE_KEY:-}" ] || { echo "AZURE_STORAGE_KEY not set"; exit 1; }
: "${WORKSPACE_SHARE:?WORKSPACE_SHARE not set in env.sh}"
: "${WORKSPACE_MOUNT:?WORKSPACE_MOUNT not set in env.sh}"

CONFIG_FILE="$(dirname "$0")/config.json"
[ -f "$CONFIG_FILE" ] || { echo "Missing $CONFIG_FILE"; exit 1; }
node -e "JSON.parse(require('fs').readFileSync('$CONFIG_FILE'))" || { echo "Invalid JSON in $CONFIG_FILE"; exit 1; }

# Pass config.json as a base64-encoded env var. The custom image's entrypoint
# (openclaw-init.sh) decodes it and writes /home/node/.openclaw/openclaw.json
# before openclaw starts.
CONFIG_B64=$(base64 -i "$CONFIG_FILE" | tr -d '\n')

echo "==> Ensuring ACR admin user is enabled"
az acr update --name "$ACR_NAME" --admin-enabled true >/dev/null

ACR_USER=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASS=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)

echo "==> Deleting existing container (if any)"
az container delete --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --yes 2>/dev/null || true

echo "==> Creating container ($IMAGE)"
az container create \
  --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --location "$LOCATION" \
  --image "$IMAGE" \
  --registry-login-server "${ACR_NAME}.azurecr.io" \
  --registry-username "$ACR_USER" \
  --registry-password "$ACR_PASS" \
  --os-type Linux \
  --cpu "$CPU" \
  --memory "$MEMORY" \
  --ip-address Public \
  --ports 18789 \
  --dns-name-label "$DNS_LABEL" \
  --azure-file-volume-account-name "$AZURE_STORAGE_ACCOUNT" \
  --azure-file-volume-account-key "$AZURE_STORAGE_KEY" \
  --azure-file-volume-share-name "$WORKSPACE_SHARE" \
  --azure-file-volume-mount-path "$WORKSPACE_MOUNT" \
  --secure-environment-variables \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    AZURE_STORAGE_ACCOUNT="$AZURE_STORAGE_ACCOUNT" \
    AZURE_STORAGE_KEY="$AZURE_STORAGE_KEY" \
    OPENCLAW_CONFIG_B64="$CONFIG_B64" \
  >/dev/null

echo "==> Waiting for container to reach Running state"
until [ "$(az container show --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --query "containers[0].instanceView.currentState.state" -o tsv 2>/dev/null)" = "Running" ]; do
  sleep 10
  echo -n "."
done
echo

echo "==> Waiting for OpenClaw gateway to be ready (~60s on first boot)"
until az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep -q "gateway.*ready"; do
  sleep 5
  echo -n "."
done
echo

echo "==> Done"
az container show --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --query "{ip:ipAddress.ip, fqdn:ipAddress.fqdn}" -o table
echo
echo "Access the UI:"
echo "  http://openclaw.${LOCATION}.azurecontainer.io:18789/"
echo
echo "==> Fetching Gateway Token"
TOKEN=$(az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node node -e console.log(JSON.parse(require(String.fromCharCode(102,115)).readFileSync('/home/node/.openclaw/openclaw.json')).gateway.auth.token)" 2>&1 \
  | tr -d '\r' | grep -E '^[a-f0-9]{32,}$' | head -n1)
if [ -n "$TOKEN" ]; then
  echo "Gateway Token: $TOKEN"
else
  echo "WARNING: could not extract Gateway Token. Run manually:"
  echo "  az container exec --resource-group \"\$RG\" --subscription \"\$SUBSCRIPTION\" --name \"\$CONTAINER\" \\"
  echo "    --exec-command \"gosu node node -e console.log(JSON.parse(require(String.fromCharCode(102,115)).readFileSync('/home/node/.openclaw/openclaw.json')).gateway.auth.token)\""
fi

cat <<EOF

==> Next steps: pair this browser

1. Open the UI in your browser, paste the Gateway Token above, and click Connect.
   The first connect from a new browser will be queued for approval.

2. List pending pairing requests (note the requestId):
     source ~/openclaw/env.sh
     az container exec --resource-group "\$RG" --subscription "\$SUBSCRIPTION" --name "\$CONTAINER" \\
       --exec-command "gosu node openclaw devices list"

3. Approve it:
     az container exec --resource-group "\$RG" --subscription "\$SUBSCRIPTION" --name "\$CONTAINER" \\
       --exec-command "gosu node openclaw devices approve <requestId>"

The browser reconnects automatically once approved. Pairings persist across redeploys
(stored under /mnt/openclaw-workspace/_state/identity/).
EOF
SCRIPT

chmod +x ~/openclaw/deploy.sh
```

---

## First-time deploy

```bash
~/openclaw/build-image.sh    # ~1-2 min — builds custom image via ACR Tasks (no local Docker daemon needed)
~/openclaw/deploy.sh         # ~2-3 min — recreates container with all config baked in
```

The deploy waits for the gateway to log `ready (7 plugins: ...)` before exiting. The output ends with the public IP and FQDN.

---

## First-time device pairing (Control UI)

OpenClaw's Control UI requires explicit device approval on first connection from a new browser. After your first visit and Connect attempt, approve from the CLI:

```bash
source ~/openclaw/env.sh

# 1. List pending pairing requests
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw devices list"
# Look for an entry under "Pending" with a requestId

# 2. Approve the requestId
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw devices approve <requestId>"
```

After approval, the same browser will reconnect without prompting. Each new browser/device requires a new approval. To revoke a paired device, use `openclaw devices remove`.

> **Why `gosu node`?** OpenClaw stores its config and device records in `/home/node/.openclaw/`. ACI exec sessions run as root, so we use `gosu node` to switch to the user that owns the data.

---

## Session and state persistence

ACI's writable filesystem is wiped on every container recreation (`az container delete` + recreate). The entrypoint solves this by symlinking OpenClaw's five mutable state directories to the durable Azure Files mount at boot, before openclaw starts.

**What gets persisted:**

| Directory | Contents |
|---|---|
| `agents/` | Chat sessions, conversation history, agent task logs |
| `identity/` | Browser device pairing records — paired browsers stay paired across redeploys |
| `tasks/` | Scheduled / background task state |
| `canvas/` | Canvas (whiteboard) state |
| `telegram/` | Telegram channel state and pending messages |

**On-disk layout (Azure Files share):**

```
openclaw-workspace/          ← root of the Azure Files share
├── SOUL.md                  ← workspace identity files (already durable)
├── USER.md
├── ...
└── _state/                  ← created by entrypoint on first boot
    ├── agents/
    ├── identity/
    ├── tasks/
    ├── canvas/
    └── telegram/
```

**How the symlinks work:**

At container start, `openclaw-init.sh` runs (as root) before openclaw:
1. Creates `_state/<subdir>` on the mount for any subdir that doesn't exist yet.
2. Removes any pre-existing path at `/home/node/.openclaw/<subdir>` (always empty on a fresh container).
3. Creates `ln -s /mnt/openclaw-workspace/_state/<subdir> /home/node/.openclaw/<subdir>`.
4. Runs `chown -R node:node /home/node/.openclaw` so openclaw (running as `node`) owns the symlinks.

OpenClaw sees normal directories and reads/writes flow transparently to Azure Files.

**Verifying symlinks after deploy:**

```bash
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "ls -la /home/node/.openclaw"
# Expected: agents → /mnt/openclaw-workspace/_state/agents, etc.
```

**Migrating existing sessions before a redeploy (optional):**

If you have valuable sessions in a running container and want to carry them over, copy them to the mount before redeploying:

```bash
source ~/openclaw/env.sh
for subdir in agents identity tasks canvas telegram; do
  az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
    --exec-command "cp -rn /home/node/.openclaw/${subdir}/. /mnt/openclaw-workspace/_state/${subdir}/"
done
```

Then redeploy. The entrypoint will find existing data in `_state/` and the symlinks will point right to it.

---

## Getting the gateway token

The token regenerates on every redeploy (it's stored in the writable filesystem, which is reset on container recreation). Fetch the current value:

```bash
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node node -e console.log(JSON.parse(require(String.fromCharCode(102,115)).readFileSync('/home/node/.openclaw/openclaw.json')).gateway.auth.token)"
```

Paste the value into the **Gateway Token** field in the Control UI.

> The `String.fromCharCode(102,115)` is `'fs'` — needed because `az container exec` mangles arguments containing quotes.

---

## Verification checklist

```bash
source ~/openclaw/env.sh

# 1. Container running, no restart loop
az container show --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --query "{state:containers[0].instanceView.currentState.state, restarts:containers[0].instanceView.restartCount}" -o json
# Expected: state=Running, restarts=0

# 2. All 7 plugins loaded (incl. memory-lancedb)
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep "gateway.*ready"
# Expected: "ready (7 plugins: acpx, browser, device-pair, memory-lancedb, phone-control, talk-voice, telegram; ...)"

# 3. Memory backend confirmed (lancedb on Azure Blob)
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw ltm stats"
# Expected: "Total memories: N" (note: command is `ltm`, not `memory`, when lancedb owns the slot)

# 4. Workspace files on Azure Files (durable across recreations)
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "ls /mnt/openclaw-workspace"
# Expected: AGENTS.md, BOOTSTRAP.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md (all on the share)

# 5. Probe both models
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw infer model run --model microsoft-foundry/Kimi-K2.6-1 --prompt hi"
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw infer model run --model microsoft-foundry/model-router-1 --prompt hi"
# Expected: a real reply from each model

# 6. UI reachable
#    http://openclaw.eastus.azurecontainer.io:18789/  (paste token, complete pairing)
```

> **Speed comparison via `az container exec` is misleading** — the exec adds ~30s of websocket overhead. For accurate latency comparison between models, test through the Control UI or Telegram, where you measure actual model time.

---

## Daily operations

### Update config

For changes to model selection, Telegram allowlist, etc.:

1. Edit `~/openclaw/config.json`
2. Run `~/openclaw/deploy.sh`

OpenClaw also supports hot-reload of *some* keys without full container recreation, but `gateway.*` settings (bind, allowedOrigins, etc.) and plugin enablement always require a process restart, which means a redeploy. Keep it simple: redeploy.

### Update upstream OpenClaw image

When upstream publishes a newer tag (e.g. `v4`):

```bash
# 1. Push the new upstream tag to ACR (or use az acr import)
az acr import --name acrdgonor2 \
  --source ghcr.io/openclaw/openclaw:<new-version>-slim \
  --image openclaw:v4

# 2. Bump tags in env.sh
sed -i '' 's/BASE_TAG=v3/BASE_TAG=v4/' ~/openclaw/env.sh
sed -i '' 's/CUSTOM_TAG=custom-v3-1/CUSTOM_TAG=custom-v4-1/' ~/openclaw/env.sh

# 3. Rebuild + redeploy
~/openclaw/build-image.sh
~/openclaw/deploy.sh
```

> **Don't try to `npm install -g openclaw@latest` on top of an older base image.** The npm package puts plugins at a different path (`/usr/local/lib/node_modules/openclaw/`) than the base image's plugin loader expects (`/app/extensions/`). Result: only `memory-lancedb` and `telegram` load — `device-pair` is missing, so the Control UI can't pair browsers and the WebSocket closes with `pairing required` regardless of approval. Bump the base image tag instead.

### Rotate a secret

```bash
# Edit the secret in ~/openclaw/env.sh, then:
~/openclaw/deploy.sh
```

### View logs

```bash
source ~/openclaw/env.sh
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER"

# Filter to just openclaw-side events
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | \
  grep -E "openclaw-init|gateway|memory-lancedb|telegram|FAIL|ERROR"
```

### Open a shell (debugging only)

```bash
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --exec-command /bin/bash
```

> ACI kills idle exec sessions with `exit code 137` (SIGKILL on bash). That's the session ending, **not your command failing**. Verify edits with one-shot exec calls.

### Decommission

```bash
source ~/openclaw/env.sh
az container delete --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --yes
```

---

## Hardening (if you need stricter security)

Token-over-HTTP is fine for personal use over trusted networks, but the token is recoverable by anyone able to MITM the connection. If you need stronger security (public wifi, multi-tenant ACI, internet-facing demo, etc.), choose one:

1. **Tailscale**. Add `tailscaled` + `tailscale serve --https=443` to the entrypoint. Drop `--ip-address Public` from `deploy.sh`. UI becomes `https://openclaw-aci.<tailnet>.ts.net/` over auto-issued TLS, gated by Tailscale identity. Documented in earlier git history — search this file's git log for `tailnet`.
2. **Reverse proxy with TLS** (Cloudflare Tunnel, Caddy + auth). Bind openclaw to loopback, terminate TLS at the proxy.
3. **Trusted-proxy auth + reverse proxy with SSO** (e.g. Cloudflare Access). Set `gateway.auth.mode: "trusted-proxy"`, `gateway.trustedProxies: [...]` and let the proxy do identity verification.

The OpenClaw docs explicitly recommend (1) for any internet-facing deployment.

---

## Critical gotchas

These all bit during the original setup. Knowing them up front saves hours.

### Don't use `az container restart`

ACI's `restart` does a full **teardown** of the writable filesystem. The auth token regenerates and any in-container config edits vanish. The boot-time injection from `OPENCLAW_CONFIG_B64` env var rewrites `openclaw.json` from your `config.json`, so the long-lived state is fine — but the auth token resets each time.

### Don't mount Azure Files at `/home/node/.openclaw`

A fresh share overlays the directory and hides workspace files OpenClaw needs (`canvas/`, `agents/`, `logs/`). Container exits with `ExitCode 1` and **empty logs** (dies before stdout opens). Mount at `/mnt/openclaw-workspace` and set `agents.defaults.workspace` instead.

### `az container exec` quoting

ACI splits `--exec-command` on whitespace and passes quotes literally. To avoid this in shell-injection-style commands, base64-encode payloads and use `Buffer.from(b64,'base64')` inside `node -e`. To avoid quote literals when reading files via node, use `String.fromCharCode(102,115)` for `'fs'`.

### Setting `ENTRYPOINT` in a child Dockerfile **resets** the parent's `CMD`

If you `FROM openclaw:v3` and add `ENTRYPOINT [...]`, Docker discards the parent image's `CMD`. Result: your entrypoint receives no args, `docker-entrypoint.sh` exits with rc=0, and the container loops with no openclaw process — **all without any error log**.

Always re-declare `CMD` after `ENTRYPOINT`:
```dockerfile
ENTRYPOINT ["/usr/local/bin/openclaw-init.sh"]
CMD ["node", "openclaw.mjs", "gateway", "--allow-unconfigured"]
```

### Don't `npm install -g openclaw@latest` to update inside the image

Multiple subtle failures:
1. The base image's `/usr/local/bin/openclaw` is a **symlink** to `/app/openclaw.mjs` — npm refuses to overwrite (EEXIST). `--force` works around this.
2. Even with `--force`, npm puts the new openclaw at `/usr/local/lib/node_modules/openclaw/` while the base image's plugin loader looks at `/app/extensions/`. Plugins like `device-pair` aren't found, so the Control UI fails to pair browsers.
3. Newer openclaw versions may change the config schema (e.g. `storageOptions` removed, new `api` enum values added) which breaks your `config.json` against the old base.

Bump `BASE_TAG` to a newer upstream tag instead.

### Schema mistakes that get rejected

| Wrong | Right |
|---|---|
| `models.default = "..."` | `agents.defaults.model = "..."` |
| `gateway.bind = "0.0.0.0:18789"` | `gateway.bind = "lan"` (string mode names only) |
| `models.providers.<p>.endpoint` | `models.providers.<p>.baseUrl` |
| `baseUrl: "https://<r>.services.ai.azure.com/api/projects/..."` | `baseUrl: "https://<r>.openai.azure.com/openai/v1"` |
| `api: "chat-completions"` | `api: "azure-openai-responses"` |
| `models: ["Kimi-K2.6-1"]` | `models: [{"id":"Kimi-K2.6-1","name":"Kimi K2.6"}]` |
| `"apiKey": "${AZURE_OPENAI_API_KEY}"` in chat provider config | Set env var, omit `apiKey` from JSON |
| `plugins.entries.memory-lancedb.enabled: true` alone | Also set `plugins.slots.memory: "memory-lancedb"` |
| `embedding.model: "text-embedding-3-small"` (no `dimensions`) when deployment name differs | Add `"dimensions": 1536` |
| `dbPath: "az://openclaw-memory"` | `dbPath: "az://openclaw-memory/lancedb"` (need a sub-path) |
| `openclaw memory status` after lancedb is active | `openclaw ltm stats` (lancedb replaces the `memory` CLI) |

### Cross-region ACR/ACI

If ACR and ACI are in different regions, image pulls cross regions — adds 20–60s to first boot. Match regions for cleaner ops.

---

## Reference

| Resource | Default in this guide |
|---|---|
| Working directory | `~/openclaw/` |
| Config source of truth | `~/openclaw/config.json` |
| Secrets file (gitignored) | `~/openclaw/env.sh` |
| Build script | `~/openclaw/build-image.sh` |
| Deploy script | `~/openclaw/deploy.sh` |
| Wrapper Dockerfile | `~/openclaw/docker/Dockerfile` |
| Entrypoint script | `~/openclaw/docker/openclaw-init.sh` |
| Container name | `openclaw-container-dgonor` |
| Custom image | `${ACR_NAME}.azurecr.io/openclaw:${CUSTOM_TAG}` |
| Workspace blob storage | Azure Files share `openclaw-workspace` in `openclawstoragedgonor` |
| Memory blob storage | `az://openclaw-memory/lancedb` in `openclawstoragedgonor` |
| Workspace mount path inside container | `/mnt/openclaw-workspace` |
| Config path inside container | `/home/node/.openclaw/openclaw.json` |
| Public URL | `http://openclaw.<region>.azurecontainer.io:18789/` |
| Required env vars | `AZURE_OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY` |
| Default chat model | `microsoft-foundry/Kimi-K2.6-1` |
| Available models | `Kimi-K2.6-1`, `model-router-1` |
| Embedding model | `text-embedding-3-small-1` (deployment name) → 1536 dims |
| Memory CLI subcommand | `openclaw ltm` (when lancedb owns the slot) |
| OpenClaw docs | https://docs.openclaw.ai |
