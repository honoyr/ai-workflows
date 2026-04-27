# OpenClaw on Azure Container Instances — Maintainable Runbook

A reproducible, version-controllable approach to running OpenClaw on Azure Container Instances (ACI), tailnet-only, with durable workspace files and shared LanceDB memory.

The container is treated as **disposable**. Configuration lives in local files; runtime state lives on durable Azure storage. After every redeploy the container reconstructs itself from these sources.

---

## Architecture

```
Laptop on tailnet ─┐                                  ┌─ Azure Blob: az://openclaw-memory/lancedb
                   │                                  │   (memory-lancedb plugin index, shared with laptop)
                   ▼                                  │
        Tailscale Serve (HTTPS, auto-TLS)             ▼
                   │                  ┌── ACI container "openclaw-container-dgonor"
       https://openclaw-aci           │
        .<tailnet>.ts.net/            │   ┌─ tailscaled (userspace networking, root)
                   │                  │   ├─ Tailscale Serve → http://localhost:18789
                   │                  │   ├─ openclaw gateway (bind=loopback, runs as node user)
                   │                  │   │   ├─ memory-lancedb → az://openclaw-memory/lancedb
                   │                  │   │   ├─ Telegram (outbound polling)
                   │                  │   │   └─ workspace at /mnt/openclaw-workspace
                   │                  │   └─ Custom entrypoint:
                   │                  │       1. decodes OPENCLAW_CONFIG_B64 → openclaw.json
                   │                  │       2. starts tailscaled
                   │                  │       3. tailscale up (using TS_AUTHKEY)
                   │                  │       4. tailscale serve --https=443 → openclaw
                   │                  │       5. drops to node user, execs openclaw
                   │                  └─ Volume mount /mnt/openclaw-workspace
                   │                      ↓ Azure Files share "openclaw-workspace"
                   │                          (SOUL.md, USER.md, AGENTS.md, MEMORY.md, etc.)
                   │
                   └── No public IP, no public port. Container is invisible from the internet.
```

**Three data layers, all durable across container recreations:**

| Layer | Where | Survives `az container delete`? |
|---|---|---|
| Config (`openclaw.json`) | Baked into `OPENCLAW_CONFIG_B64` env var, written at boot by entrypoint | ✓ — comes from local `config.json` |
| Workspace files (`SOUL.md`, `USER.md`, etc.) | Azure Files share `openclaw-workspace` mounted at `/mnt/openclaw-workspace` | ✓ |
| Memory index (vectors) | Azure Blob `az://openclaw-memory/lancedb` via `memory-lancedb` plugin | ✓ |

**Three workflows:**

1. **Build image** (`build-image.sh`) — only when the wrapper Dockerfile or entrypoint script changes, or you bump the upstream openclaw image tag.
2. **Deploy** (`deploy.sh`) — recreates the container with current `config.json` baked in. Used for plugin enablement changes, image updates, or secret rotation.
3. **Hot-reload** (`apply-config.sh`, optional) — for runtime-mutable config like the default model or Telegram allowlist. Won't work for `gateway.*` changes (those require restart).

---

## Prerequisites

- **Azure CLI** installed and logged in (`az login`)
- **Azure Container Registry** with the upstream `openclaw:v2` image available
- **Azure OpenAI / Foundry** deployment with both:
  - A chat model (e.g. `Kimi-K2.6-1`)
  - A text-embedding model deployment (e.g. `text-embedding-3-small-1`)
- **Telegram bot token** from [@BotFather](https://t.me/BotFather), and your Telegram user ID from [@userinfobot](https://t.me/userinfobot)
- **Tailscale account** with **HTTPS feature enabled** (https://login.tailscale.com → Settings → Features → Enable HTTPS)
- A device on the tailnet (your laptop) for accessing the Control UI

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

### 3. Generate a Tailscale auth key

Go to https://login.tailscale.com/admin/settings/keys → **Generate auth key** with these settings:

- ✅ **Reusable** — needed because every redeploy creates a new device
- ✅ **Ephemeral** — **critical** — without this, dead containers leave zombie devices that take over the hostname slot (you'll get `openclaw-aci-1`, `-2`, `-3` etc.). With ephemeral, dead devices auto-cleanup and the next deploy reclaims the clean `openclaw-aci` hostname.
- Optionally tag it (e.g. `tag:openclaw`)

Copy the `tskey-auth-...` value — it's used in `env.sh`.

---

## Local working directory

Everything below lives at `~/openclaw/` on your machine. This directory is the source of truth.

```
~/openclaw/
├── env.sh              # Secrets + Azure/Tailscale variables (NOT committed)
├── config.json         # OpenClaw runtime config (safe to commit — no secrets)
├── build-image.sh      # Builds the custom wrapper image via az acr build
├── deploy.sh           # Recreates the ACI container with current config baked in
└── docker/
    ├── Dockerfile      # FROM openclaw:v2 + tailscale + gosu + entrypoint
    └── openclaw-init.sh # Entrypoint: writes config, starts tailscaled, drops to node, exec openclaw
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
export BASE_TAG=v2
export CUSTOM_TAG=custom-v2-4
export IMAGE_TAG="$CUSTOM_TAG"
export IMAGE="${ACR_NAME}.azurecr.io/openclaw:${IMAGE_TAG}"

# Workspace storage (Azure Files share for SOUL.md, USER.md, MEMORY.md, etc.)
export WORKSPACE_SHARE=openclaw-workspace
export WORKSPACE_MOUNT=/mnt/openclaw-workspace

# Tailscale — see https://login.tailscale.com/admin/settings/keys
# Recommended: reusable=true, ephemeral=true (avoids zombie devices that block the hostname)
export TS_AUTHKEY="<paste-tailscale-auth-key>"
export TS_HOSTNAME="openclaw-aci"

# Container
export CONTAINER=openclaw-container-dgonor
export DNS_LABEL=openclaw

# Resources
export CPU=2
export MEMORY=4

# Secrets
export AZURE_OPENAI_API_KEY="<paste-azure-openai-key>"
export TELEGRAM_BOT_TOKEN="<paste-telegram-bot-token>"
export AZURE_STORAGE_ACCOUNT="openclawstoragedgonor"
export AZURE_STORAGE_KEY="<paste-storage-account-key>"
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

Source of truth for non-secret OpenClaw config. Safe to commit.

```bash
cat > ~/openclaw/config.json << 'EOF'
{
  "gateway": {
    "bind": "loopback",
    "trustedProxies": ["127.0.0.1", "::1"],
    "auth": {
      "mode": "token",
      "allowTailscale": true
    },
    "controlUi": {
      "allowInsecureAuth": false,
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
        "models": [{"id": "Kimi-K2.6-1", "name": "Kimi K2.6"}]
      }
    }
  },
  "plugins": {
    "slots": {
      "memory": "memory-lancedb"
    },
    "entries": {
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
| `gateway.bind: "loopback"` | OpenClaw never listens on a public interface. All ingress comes through Tailscale Serve via 127.0.0.1. |
| `gateway.trustedProxies` | Tells openclaw to trust `X-Forwarded-*` headers from Tailscale Serve (loopback). Without this, openclaw refuses to identify the real Tailscale user → falls back to "pairing required". |
| `gateway.auth.allowTailscale: true` | Accepts Tailscale identity (verified by `tailscaled`) as authentication. |
| `gateway.controlUi.allowInsecureAuth: false` | No plain-HTTP token auth — we have HTTPS via Tailscale Serve. Passes `openclaw security audit`. |
| `gateway.controlUi.allowedOrigins: ["*"]` | Wildcard origin is acceptable here because Tailscale identity already gates who can even reach the URL. CORS is defense-in-depth, not the primary control. |
| `agents.defaults.workspace: "/mnt/openclaw-workspace"` | Workspace files (SOUL.md, USER.md, etc.) land on the durable Azure Files mount. |
| `plugins.slots.memory: "memory-lancedb"` | Memory slot owner — required to actually activate the lancedb plugin. Enabling the entry alone isn't enough. |
| `plugins.entries.memory-lancedb.config.dbPath: "az://..."` | LanceDB index lives on Azure Blob Storage, shared with your laptop's local OpenClaw if you point both to the same URI. |
| `text-embedding-3-small-1 + dimensions: 1536` | Required because Azure deployment names don't match openclaw's hardcoded model→dim lookup. See gotchas below. |

### `~/openclaw/docker/Dockerfile`

```bash
mkdir -p ~/openclaw/docker
cat > ~/openclaw/docker/Dockerfile << 'EOF'
ARG BASE_IMAGE=acrdgonor2.azurecr.io/openclaw:v2
FROM ${BASE_IMAGE}

USER root

# Install Tailscale (userspace networking — no TUN device or NET_ADMIN cap needed,
# which makes it work cleanly inside Azure Container Instances) and gosu so we
# can drop privileges back to the node user before exec'ing openclaw.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg gosu \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg \
      | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.tailscale-keyring.list \
      | tee /etc/apt/sources.list.d/tailscale.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends tailscale \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY openclaw-init.sh /usr/local/bin/openclaw-init.sh
RUN chmod +x /usr/local/bin/openclaw-init.sh

# Stay as root for the entrypoint — it starts tailscaled (root-only) and then
# drops to the node user via gosu before exec'ing openclaw.
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
chown -R node:node /home/node/.openclaw

# 2. Note workspace mount
if [ -d /mnt/openclaw-workspace ]; then
  log "workspace mount detected at /mnt/openclaw-workspace (azure files)"
fi

# 3. Start Tailscale (userspace networking, runs as root)
if [ -n "$TS_AUTHKEY" ]; then
  log "starting tailscaled in userspace networking mode"
  mkdir -p /var/run/tailscale /var/lib/tailscale

  # Redirect tailscaled output to a separate file to keep main stdout clean
  tailscaled \
    --tun=userspace-networking \
    --state=/var/lib/tailscale/tailscaled.state \
    --socket=/var/run/tailscale/tailscaled.sock \
    --port=41641 \
    >/var/log/tailscaled.log 2>&1 &

  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ -S /var/run/tailscale/tailscaled.sock ] && break
    sleep 1
  done

  HOSTNAME="${TS_HOSTNAME:-openclaw-aci}"
  log "tailscale up (hostname=$HOSTNAME)"
  tailscale up \
    --authkey="$TS_AUTHKEY" \
    --hostname="$HOSTNAME" \
    --accept-routes=false \
    --accept-dns=false 2>&1 | sed 's/^/[tailscale-up] /'

  if [ "${TS_SERVE_ENABLED:-true}" = "true" ]; then
    log "configuring tailscale serve --bg --https=443 -> http://localhost:18789"
    tailscale serve --bg --https=443 http://localhost:18789 2>&1 | sed 's/^/[tailscale-serve] /' || \
      log "tailscale serve setup failed (HTTPS may need to be enabled in admin console)"
  fi

  log "tailscale ip:"
  tailscale ip -4 2>&1 | sed 's/^/[tailscale-ip] /' || true
else
  log "TS_AUTHKEY not set; Tailscale disabled"
fi

# 4. Run openclaw via the original entrypoint, dropping privileges to node.
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
: "${BASE_TAG:?BASE_TAG not set in env.sh (e.g. v2)}"
: "${CUSTOM_TAG:?CUSTOM_TAG not set in env.sh (e.g. custom-v2-4)}"

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
echo "==> Built. Update env.sh: IMAGE_TAG=$CUSTOM_TAG, then run deploy.sh"
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
[ -n "${TS_AUTHKEY:-}" ] || { echo "TS_AUTHKEY not set (generate at https://login.tailscale.com/admin/settings/keys)"; exit 1; }
[ "$TS_AUTHKEY" != "<paste-tailscale-auth-key>" ] || { echo "TS_AUTHKEY is still the placeholder — paste a real key into env.sh"; exit 1; }
: "${WORKSPACE_SHARE:?WORKSPACE_SHARE not set in env.sh}"
: "${WORKSPACE_MOUNT:?WORKSPACE_MOUNT not set in env.sh}"
: "${TS_HOSTNAME:?TS_HOSTNAME not set in env.sh}"

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
    TS_AUTHKEY="$TS_AUTHKEY" \
    TS_HOSTNAME="$TS_HOSTNAME" \
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

echo "==> Done — container is on the tailnet only (no public IP)"
echo
echo "Access the UI from any device on the tailnet:"
echo "  https://${TS_HOSTNAME}.<your-tailnet>.ts.net/"
SCRIPT

chmod +x ~/openclaw/deploy.sh
```

---

## First-time deploy

```bash
~/openclaw/build-image.sh    # ~1.5 min — builds custom image via ACR Tasks (no local Docker daemon needed)
~/openclaw/deploy.sh         # ~2-3 min — recreates container with all config baked in
```

The deploy waits for the gateway to log `ready (7 plugins: ...)` before exiting.

---

## First-time device pairing

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

# 3. Memory backend confirmed
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "gosu node openclaw ltm stats"
# Expected: "Total memories: N" (note: command is `ltm`, not `memory`, when lancedb owns the slot)

# 4. Workspace files on Azure Files (durable across recreations)
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "ls /mnt/openclaw-workspace"
# Expected: AGENTS.md, BOOTSTRAP.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md (all on the share)

# 5. Tailscale device alive on tailnet
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "tailscale status --self=true --peers=false"
# Expected: <ip>  openclaw-aci  <user>  linux  -

# 6. UI reachable (open in browser from any tailnet device)
#    https://openclaw-aci.<your-tailnet>.ts.net/
```

---

## Daily operations

### Update config (most changes — runtime-mutable)

For changes to model selection, Telegram allowlist, etc.:

1. Edit `~/openclaw/config.json`
2. Run `~/openclaw/deploy.sh`

OpenClaw also supports hot-reload of *some* keys without full container recreation, but `gateway.*` settings (bind, trustedProxies, allowedOrigins) and plugin enablement always require a process restart, which means a redeploy. Keep it simple: redeploy.

### Update upstream OpenClaw image

```bash
# 1. Bump BASE_TAG in env.sh (e.g. v2 → v3)
sed -i '' 's/BASE_TAG=v2/BASE_TAG=v3/' ~/openclaw/env.sh

# 2. Bump CUSTOM_TAG too (so we don't conflict with old custom images)
sed -i '' 's/CUSTOM_TAG=custom-v2-4/CUSTOM_TAG=custom-v3-1/' ~/openclaw/env.sh

# 3. Rebuild the wrapper image
~/openclaw/build-image.sh

# 4. Redeploy
~/openclaw/deploy.sh
```

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
# Tailscale device auto-cleans up because the auth key is ephemeral
```

---

## Critical gotchas

These all bit during the original setup. Knowing them up front saves hours.

### Don't use `az container restart`

ACI's `restart` does a full **teardown** of the writable filesystem. The auth token regenerates and any in-container config edits vanish. The boot-time injection from `OPENCLAW_CONFIG_B64` env var rewrites `openclaw.json` from your `config.json`, so the long-lived state is fine — but the auth token resets each time.

### Don't mount Azure Files at `/home/node/.openclaw`

A fresh share overlays the directory and hides workspace files OpenClaw needs (`canvas/`, `agents/`, `logs/`). Container exits with `ExitCode 1` and **empty logs** (dies before stdout opens). Mount at `/mnt/openclaw-workspace` and set `agents.defaults.workspace` instead.

### `az container exec` quoting

ACI splits `--exec-command` on whitespace and passes quotes literally. To avoid this in shell-injection-style commands, base64-encode payloads and use `Buffer.from(b64,'base64')` inside `node -e`. To avoid quote literals when reading files via node, use `String.fromCharCode(102,115)` for `'fs'`.

```bash
# WRONG — sed receives literal "'s/x/y/'" and errors
--exec-command "sed -i 's/x/y/' /file"

# RIGHT — no inner quotes
--exec-command "sed -i s/x/y/ /file"
```

### Setting `ENTRYPOINT` in a child Dockerfile **resets** the parent's `CMD`

If you `FROM openclaw:v2` and add `ENTRYPOINT [...]`, Docker discards the parent image's `CMD`. Result: your entrypoint receives no args, `docker-entrypoint.sh` exits with rc=0, and the container loops with no openclaw process — **all without any error log**.

Always re-declare `CMD` after `ENTRYPOINT`:
```dockerfile
ENTRYPOINT ["/usr/local/bin/openclaw-init.sh"]
CMD ["node", "openclaw.mjs", "gateway", "--allow-unconfigured"]
```

### Tailscale auth key MUST be ephemeral

Without `Ephemeral: Yes` on the auth key, dead containers leave zombie devices in your tailnet that hold the hostname slot. Each redeploy claims `openclaw-aci-1`, `-2`, `-3`, etc. The URL keeps changing and `allowedOrigins` becomes a moving target. With ephemeral keys, dead devices auto-cleanup within minutes and the next deploy reclaims the clean `openclaw-aci` hostname.

### Schema mistakes that get rejected

| Wrong | Right |
|---|---|
| `models.default = "..."` | `agents.defaults.model = "..."` |
| `gateway.bind = "0.0.0.0:18789"` | `gateway.bind = "loopback"` (string mode names only) |
| `models.providers.<p>.endpoint` | `models.providers.<p>.baseUrl` |
| `baseUrl: "https://<r>.services.ai.azure.com/api/projects/..."` | `baseUrl: "https://<r>.openai.azure.com/openai/v1"` |
| `api: "chat-completions"` | `api: "azure-openai-responses"` |
| `models: ["Kimi-K2.6-1"]` | `models: [{"id":"Kimi-K2.6-1","name":"Kimi K2.6"}]` |
| `"apiKey": "${AZURE_OPENAI_API_KEY}"` in chat provider config | Set env var, omit `apiKey` from JSON |
| `plugins.entries.memory-lancedb.enabled: true` alone | Also set `plugins.slots.memory: "memory-lancedb"` |
| `embedding.model: "text-embedding-3-small"` (no `dimensions`) when deployment name differs | Add `"dimensions": 1536` |
| `dbPath: "az://openclaw-memory"` | `dbPath: "az://openclaw-memory/lancedb"` (need a sub-path) |
| `openclaw memory status` after lancedb is active | `openclaw ltm stats` (lancedb replaces the `memory` CLI) |

### "pairing required" error in Control UI

If the WebSocket connection closes immediately with `code=1008 reason=pairing required`, two distinct causes:

1. **Missing `gateway.trustedProxies`**: openclaw can't see the real Tailscale user behind Tailscale Serve, so it falls back to "must pair this device". Fix: `"trustedProxies": ["127.0.0.1", "::1"]` in `gateway`.

2. **Genuinely new browser**: even with everything else right, the first connection from a new browser/device needs explicit approval. Fix: `openclaw devices list` then `openclaw devices approve <requestId>`. See "First-time device pairing" above.

### Cross-region ACR/ACI

If ACR and ACI are in different regions, image pulls cross regions — adds 20–60s to first boot. Match regions for cleaner ops.

---

## Why this architecture

| Concern | Solution |
|---|---|
| ACI auto-restarts wipe writable filesystem | Config baked into env var, decoded at boot by entrypoint |
| Plugin enablement requires process restart | Boot-time config injection — plugins are present on first start, no second restart needed |
| Public IP exposes openclaw to the internet | No public IP at all. Tailscale handles ingress. |
| Token over plain HTTP can be intercepted | Tailscale Serve provides automatic HTTPS via auto-issued TLS |
| Workspace markdown files lost on container recreation | Azure Files volume mount at `/mnt/openclaw-workspace` |
| Memory lost on container recreation | `memory-lancedb` plugin with `dbPath: "az://..."` on Azure Blob |
| Tailscale devices accumulate zombies | Ephemeral auth key — Tailscale auto-cleans dead devices |
| `az container restart` re-creates writable layer | Document that it's destructive; use `deploy.sh` instead |

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
| Tailnet hostname | `openclaw-aci.<your-tailnet>.ts.net` |
| Required env vars | `AZURE_OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, `TS_AUTHKEY`, `TS_HOSTNAME` |
| Default chat model | `microsoft-foundry/Kimi-K2.6-1` |
| Embedding model | `text-embedding-3-small-1` (deployment name) → 1536 dims |
| Memory CLI subcommand | `openclaw ltm` (when lancedb owns the slot) |
| OpenClaw docs | https://docs.openclaw.ai |
