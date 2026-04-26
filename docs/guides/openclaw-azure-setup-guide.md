# OpenClaw on Azure Container Instances — Maintainable Runbook

A reproducible, version-controllable approach to running OpenClaw on ACI. The container is treated as **disposable**; configuration lives in local files and is reapplied after every deploy.

---

## How it works

```
Local machine                          Azure
─────────────────────────────────      ───────────────────────────────────
~/openclaw/                            ACR <acr>.azurecr.io/openclaw:<tag>
  ├─ env.sh         (shell vars,                      │ pull
  │                  not committed)                   ▼
  ├─ config.json    (committable)      ACI container "openclaw-container-dgonor"
  ├─ deploy.sh ────────base64──────▶     ├─ env: AZURE_OPENAI_API_KEY, TELEGRAM_BOT_TOKEN,
  │                                      │        AZURE_STORAGE_*, OPENCLAW_BOOT_CONFIG_B64
  └─ apply-config.sh ─hot reload──▶     ├─ override CMD: writes config from env var
                                          │              before launching openclaw
                                          └─ /home/node/.openclaw/openclaw.json
                                             (regenerated on EVERY boot from env var,
                                              survives Azure-initiated auto-restarts)
```

**Three workflows**:
1. **Deploy** — bakes `config.json` into the container as a base64 env var. Used on first install, image updates, secret rotation, **or any change to plugin enablement** (e.g. memory-lancedb).
2. **Update config (hot)** — edit `config.json`, run `apply-config.sh`. OpenClaw hot-reloads non-plugin keys (model, allowlist, etc.). No restart needed.
3. **Update image** — push new tag to ACR, run `deploy.sh` (recreates container, bakes config in fresh).

**Why this approach**: ACI's `az container restart` wipes the writable filesystem, and Azure also auto-restarts containers periodically (node moves, image health). Without the boot-time injection, the writable layer would be reset to defaults. By passing config as a base64 env var and overriding the container CMD to write it before OpenClaw starts, the config is **always present at boot** — including after Azure-initiated restarts.

**Why hot-reload alone isn't enough**: plugin enablement (e.g. enabling `memory-lancedb`) requires a process restart per the plugin schema. `apply-config.sh` only works for runtime-mutable config like the default model or Telegram allowlist.

---

## Prerequisites

- Azure CLI installed and logged in (`az login`)
- An Azure Container Registry with the OpenClaw image pushed
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (message [@userinfobot](https://t.me/userinfobot))
- An Azure OpenAI / Microsoft Foundry deployment with API key + endpoint

---

## One-time setup

### 1. Create the working directory

```bash
mkdir -p ~/openclaw
cd ~/openclaw
```

### 2. Create `env.sh` — secrets and config (do not commit)

```bash
cat > ~/openclaw/env.sh << 'EOF'
# Azure
export SUBSCRIPTION=d7e7222e-bf82-4b07-b21c-f8c7d06b3607
export RG=myCentralRG
export LOCATION=centralus

# ACR + image
export ACR_NAME=acrdgonor2
export IMAGE_TAG=v2
export IMAGE="${ACR_NAME}.azurecr.io/openclaw:${IMAGE_TAG}"

# Container
export CONTAINER=openclaw-container-dgonor
export DNS_LABEL=openclaw

# Resources
export CPU=2
export MEMORY=4

# Secrets — REPLACE WITH YOUR REAL VALUES
export AZURE_OPENAI_API_KEY="<paste-your-key>"
export TELEGRAM_BOT_TOKEN="<paste-your-bot-token>"
export AZURE_STORAGE_ACCOUNT="openclawstoragedgonor"
export AZURE_STORAGE_KEY="<paste-your-storage-key>"
EOF

chmod 600 ~/openclaw/env.sh
echo "Now edit ~/openclaw/env.sh and replace the placeholder secrets."
```

> Add `~/openclaw/env.sh` to your global gitignore. Never commit it.

### 3. Create `config.json` — canonical OpenClaw config

This is the source of truth for non-secret OpenClaw config. Edit values to match your setup. **Use top-level keys only** — `apply-config.sh` does a shallow merge, so each top-level block is replaced wholesale.

```bash
cat > ~/openclaw/config.json << 'EOF'
{
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "allowlist",
      "allowFrom": ["116798769"]
    }
  },
  "agents": {
    "defaults": {
      "model": "microsoft-foundry/Kimi-K2.6-1"
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

This file is safe to commit (no secrets — credentials come from env vars).

**Model role split**

Two models are deployed, each doing a completely different job:

| Role | Deployment | When it runs |
|---|---|---|
| **Chat** | `Kimi-K2.6-1` | Every Telegram reply, all reasoning — set via `agents.defaults.model` |
| **Embeddings** | `text-embedding-3-small-1` | Only when storing or recalling a memory — converts text → 1536 numbers for LanceDB similarity search, never generates a word of response |

`text-embedding-3-small-1` is invisible during normal chat. It activates only when `autoCapture` saves something to memory or `autoRecall` queries the index before a reply.

---

**Why `text-embedding-3-small-1` requires `"dimensions": 1536`**

The `memory-lancedb` plugin has a hardcoded lookup table mapping model names to vector dimensions:

```
"text-embedding-3-small" → 1536
"text-embedding-3-large" → 3072
```

Azure OpenAI deployment names are user-defined (e.g. `text-embedding-3-small-1`) and don't match the lookup key exactly. Without `dimensions`, the plugin throws "Unsupported embedding model". Specifying `dimensions: 1536` bypasses the lookup and tells LanceDB the correct vector size directly.

**Why you must not change embedding models later**

LanceDB stores vectors with a fixed column width baked into the table schema. If you switch to a different model with different dimensions (or even same dimensions but different vector space), existing vectors become meaningless — cosine similarity breaks silently. The only recovery is deleting the index and reindexing from scratch. Pick once.

**Schema rules** (saved you the validation errors):
- Default model goes at `agents.defaults.model`, **not** `models.default`.
- Provider config uses `baseUrl`, **not** `endpoint`.
- For Azure OpenAI / Foundry, the `baseUrl` ends in `/openai/v1` (e.g. `https://<resource>.openai.azure.com/openai/v1`). The Foundry `*.services.ai.azure.com/api/projects/...` URL does **not** work — wrong API surface.
- `api` must be one of: `openai-completions`, `openai-responses`, `openai-codex-responses`, `anthropic-messages`, `google-generative-ai`, `github-copilot`, `bedrock-converse-stream`, `ollama`, `azure-openai-responses`.
- `models` is an array of **objects**, not strings: `[{"id": "<deployment-name>", "name": "<display>"}]`.
- Don't put `apiKey` in JSON — set the env var, OpenClaw auto-detects.

### 4. Create `apply-config.sh` — pushes `config.json` into the container

```bash
cat > ~/openclaw/apply-config.sh << 'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

CONFIG_FILE="$(dirname "$0")/config.json"
[ -f "$CONFIG_FILE" ] || { echo "Missing $CONFIG_FILE"; exit 1; }

# Validate JSON locally first
node -e "JSON.parse(require('fs').readFileSync('$CONFIG_FILE'))" || { echo "Invalid JSON in $CONFIG_FILE"; exit 1; }

# Encode to base64 (single line, no whitespace) and ship to container
B64=$(base64 -i "$CONFIG_FILE" | tr -d '\n')

az container exec \
  --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "node -e d=JSON.parse(Buffer.from('$B64','base64').toString());fs=require('fs');p='/home/node/.openclaw/openclaw.json';c=JSON.parse(fs.readFileSync(p));Object.assign(c,d);fs.writeFileSync(p,JSON.stringify(c,null,2));console.log('applied');"

echo
echo "Verifying hot-reload picked it up..."
sleep 3
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep -E "reload|hot reload" | tail -3
SCRIPT

chmod +x ~/openclaw/apply-config.sh
```

**How it works**: encodes `config.json` to base64 (one line, no whitespace — survives `az container exec`'s naive word-splitting), then runs a single-line `node -e` inside the container that decodes it, shallow-merges with the existing config, and writes it back. OpenClaw watches the file and hot-reloads automatically.

### 5. Create `deploy.sh` — creates or recreates the container

```bash
cat > ~/openclaw/deploy.sh << 'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

[ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "AZURE_OPENAI_API_KEY not set"; exit 1; }
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || { echo "TELEGRAM_BOT_TOKEN not set"; exit 1; }
[ -n "${AZURE_STORAGE_ACCOUNT:-}" ] || { echo "AZURE_STORAGE_ACCOUNT not set"; exit 1; }
[ -n "${AZURE_STORAGE_KEY:-}" ] || { echo "AZURE_STORAGE_KEY not set"; exit 1; }

CONFIG_FILE="$(dirname "$0")/config.json"
[ -f "$CONFIG_FILE" ] || { echo "Missing $CONFIG_FILE"; exit 1; }
node -e "JSON.parse(require('fs').readFileSync('$CONFIG_FILE'))" || { echo "Invalid JSON in $CONFIG_FILE"; exit 1; }

# Bake config into the container at boot time. The base64-encoded config is
# passed as a secure env var; the override command writes it to disk BEFORE
# OpenClaw starts so plugin enablement (memory-lancedb, etc.) takes effect on
# first boot — and survives Azure-initiated container auto-restarts.
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
  --command-line "sh -c 'mkdir -p /home/node/.openclaw && printf %s \"\$OPENCLAW_BOOT_CONFIG_B64\" | base64 -d > /home/node/.openclaw/openclaw.json && exec docker-entrypoint.sh node openclaw.mjs gateway --allow-unconfigured'" \
  --secure-environment-variables \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    AZURE_STORAGE_ACCOUNT="$AZURE_STORAGE_ACCOUNT" \
    AZURE_STORAGE_KEY="$AZURE_STORAGE_KEY" \
    OPENCLAW_BOOT_CONFIG_B64="$CONFIG_B64" \
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

echo "==> Done — config was baked in at boot via OPENCLAW_BOOT_CONFIG_B64"
az container show --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --query "{ip:ipAddress.ip, fqdn:ipAddress.fqdn}" -o table
SCRIPT

chmod +x ~/openclaw/deploy.sh
```

---

## Usage

### First deploy

```bash
~/openclaw/deploy.sh
```

Takes ~2–3 minutes (image pull + workspace init + config apply). Output ends with the container's public IP and FQDN.

Test by messaging your bot on Telegram. If allowlisted correctly, no pairing prompt — it just replies.

### Update config (no container restart)

```bash
# Edit the file
$EDITOR ~/openclaw/config.json

# Push to container — hot-reloads automatically
~/openclaw/apply-config.sh
```

Works for any change in `config.json`: changing the model, adding allowlist entries, swapping providers. OpenClaw picks it up within ~2 seconds.

### Update image (e.g. new OpenClaw release)

```bash
# 1. Build and push the new image
docker build -t openclaw:v3 .
docker tag openclaw:v3 "${ACR_NAME}.azurecr.io/openclaw:v3"
az acr login --name "$ACR_NAME"
docker push "${ACR_NAME}.azurecr.io/openclaw:v3"

# 2. Update the tag in env.sh
sed -i '' 's/IMAGE_TAG=v2/IMAGE_TAG=v3/' ~/openclaw/env.sh

# 3. Redeploy (deletes + recreates with new image, then applies config)
~/openclaw/deploy.sh
```

The auth token regenerates on recreate. Fetch the new one with:

```bash
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "node -e c=JSON.parse(require('fs').readFileSync('/home/node/.openclaw/openclaw.json'));console.log(c.gateway.auth.token);"
```

### Rotate a secret

```bash
# Edit env.sh with the new value
$EDITOR ~/openclaw/env.sh

# Redeploy (recreates container with new env vars)
~/openclaw/deploy.sh
```

### View logs

```bash
source ~/openclaw/env.sh
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER"
```

### Open a shell (debugging only)

```bash
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --exec-command /bin/bash
```

> ACI exec sessions get killed on idle — you'll see `exit code 137`. That's SIGKILL on `/bin/bash`, **not your command failing**. Verify edits with a one-shot exec.

### Decommission

```bash
source ~/openclaw/env.sh
az container delete --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" --yes
```

---

## Verification checklist

After every deploy:

```bash
source ~/openclaw/env.sh

# 1. Container running
az container show --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --query "containers[0].instanceView.currentState.state" -o tsv
# Expected: Running

# 2. Telegram plugin loaded
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep "gateway.*ready"
# Expected: "ready (6 plugins: ...telegram...)"

# 3. Config applied
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "cat /home/node/.openclaw/openclaw.json"
# Expected: your config.json contents merged in

# 4. Probe the model directly (validates baseUrl + API key + model name)
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "openclaw infer model run --model microsoft-foundry/Kimi-K2.6-1 --prompt hi"
# Expected: a real reply from the model
# Common errors:
#   "Unknown model: ..."  → models.providers.<p>.models[] missing or model id mismatch
#   "401 / Invalid API key"  → AZURE_OPENAI_API_KEY env var wrong
#   "404 / DeploymentNotFound" → wrong baseUrl or deployment not deployed
#   "config reload skipped (invalid config)"  → check schema mistakes table

# 5. Real test — message the bot on Telegram
```

If `gateway` doesn't appear "ready" within ~3 minutes, check logs for errors:

```bash
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep -iE "error|fail|denied" | tail -20
```

---

## Resource sizing

| Workload | CPU | Memory |
|---|---|---|
| Minimum (testing only) | 0.5 | 1 GB |
| Light (1 channel, no browser) | 1 | 2 GB |
| **Recommended (set in env.sh above)** | **2** | **4 GB** |
| Heavy (multi-channel + browser) | 2 | 6–8 GB |

Approx ACI cost (Standard, 24/7): 1 vCPU+2 GB ~$36/mo; 2 vCPU+4 GB ~$72/mo.

---

## Optional: expose Web UI publicly

By default the gateway binds to loopback — only Telegram works (it polls outbound). To expose the UI at `http://<public-ip>:18789`, add to `config.json`:

```json
{
  "gateway": {
    "mode": "local",
    "bind": "auto"
  }
}
```

Then `apply-config.sh`. **Note**: `gateway.bind` accepts mode names only (`auto`, `lan`, `loopback`, `tailnet`, `custom`) — *not* `0.0.0.0:18789`.

Get the auth token (regenerated on each container recreate):

```bash
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "node -e c=JSON.parse(require('fs').readFileSync('/home/node/.openclaw/openclaw.json'));console.log(c.gateway.auth.token);"
```

For anything beyond personal use, prefer Tailscale (`bind=tailnet`) or a reverse proxy with stricter auth.

---

## Critical gotchas

These are the ones that bit during the original setup. Knowing them up front saves hours.

### Don't use `az container restart`

ACI's `restart` does a full **teardown** of the writable filesystem — auth token regenerates, all in-container config edits vanish, and `restartCount` stays at `0`. There's no in-place restart.

For config changes, edit `config.json` and run `apply-config.sh`. OpenClaw hot-reloads.

For anything that requires a process restart (env vars, image), use `deploy.sh`.

### Don't mount Azure Files at `/home/node/.openclaw`

A fresh share overlays the directory and hides workspace files OpenClaw needs (`canvas/`, `agents/`, `logs/`). Container exits with `ExitCode 1` and **empty logs** (dies before stdout opens). The config-as-code approach above is the maintainable alternative.

### `az container exec` quoting

ACI splits `--exec-command` on whitespace and passes quotes literally. The base64 trick in `apply-config.sh` exists specifically to avoid this — never put quoted JSON or shell-escaped strings in `--exec-command` directly.

```bash
# WRONG — sed receives literal "'s/x/y/'" and errors
--exec-command "sed -i 's/x/y/' /file"

# RIGHT — no inner quotes
--exec-command "sed -i s/x/y/ /file"
```

### Schema mistakes that get rejected

| Wrong | Right |
|---|---|
| `models.default = "..."` | `agents.defaults.model = "..."` |
| `gateway.bind = "0.0.0.0:18789"` | `gateway.bind = "auto"` |
| `models.providers.<p>.endpoint` | `models.providers.<p>.baseUrl` |
| `baseUrl: "https://<r>.services.ai.azure.com/api/projects/..."` | `baseUrl: "https://<r>.openai.azure.com/openai/v1"` |
| `api: "chat-completions"` | `api: "azure-openai-responses"` (or other valid enum) |
| `models: ["Kimi-K2.6-1"]` | `models: [{"id":"Kimi-K2.6-1","name":"Kimi K2.6"}]` |
| `"apiKey": "${AZURE_OPENAI_API_KEY}"` in JSON | Set env var, omit `apiKey` from JSON |

### LanceDB memory schema gotchas

| Wrong | Right |
|---|---|
| `"model": "text-embedding-3-small"` (no `dimensions`) when deployment name differs | Add `"dimensions": 1536` — bypass the hardcoded name lookup |
| Changing embedding model after index exists | Delete `az://openclaw-memory/lancedb` prefix in blob storage first, then change model — old vectors are incompatible |
| `dbPath: "az://openclaw-memory"` (no subpath) | `dbPath: "az://openclaw-memory/lancedb"` — the path after the container name is the table prefix |
| `plugins.entries.memory-lancedb.enabled: true` alone | Also set `plugins.slots.memory: "memory-lancedb"` — enabling the entry isn't enough; the **memory slot** owner must be set explicitly or the default `memory-core` keeps the slot |
| `openclaw memory status` after lancedb is active | `openclaw ltm stats` — lancedb replaces the built-in `memory` CLI namespace with its own `ltm` (long-term memory) commands |

### Cross-region ACR/ACI

If ACR and ACI are in different regions, image pulls cross regions — adds 20–60s to first boot and a small egress fee. Match regions for cleaner ops.

---

## Shared LanceDB memory (laptop ↔ container)

Both your laptop's local OpenClaw and the ACI container write to the same LanceDB index in Azure Blob Storage. Memory captured in one is recalled in the other.

### How it works

```
Laptop ~/.openclaw/memory/lancedb (local)         ─ NOT used when dbPath is az://
Laptop OpenClaw  ─┐
                  ├──▶  az://openclaw-memory/lancedb  (Azure Blob Storage)
ACI Container   ─┘
```

- Memories survive container recreations (no more wipe on `deploy.sh`)
- The blob container `openclaw-memory` in `openclawstoragedgonor` is the single source of truth
- Both sides must use the **same embedding model + dimensions** — vectors from different models are incompatible

### Configure your laptop

Add to `~/.openclaw/openclaw.json` (or let `openclaw config` do it):

```json
{
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
```

Set `AZURE_OPENAI_API_KEY`, `AZURE_STORAGE_ACCOUNT=openclawstoragedgonor`, and `AZURE_STORAGE_KEY` in your shell environment (or `~/.openclaw/env.sh` if you use one locally).

### Verify both sides are connected

When `memory-lancedb` owns the memory slot, it **replaces** the built-in `openclaw memory` subcommand with its own `openclaw ltm` (long-term memory) subcommand. Don't confuse the two — `openclaw memory` will return "unknown command" once lancedb is active.

```bash
# In container
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" \
  --exec-command "openclaw ltm stats"
# Expected: "Total memories: N"

# Also verify in logs
az container logs --resource-group "$RG" --subscription "$SUBSCRIPTION" --name "$CONTAINER" 2>&1 | grep memory-lancedb | tail -3
# Expected:
#   memory-lancedb: plugin registered (db: az://openclaw-memory/lancedb, lazy init)
#   memory-lancedb: initialized (db: az://openclaw-memory/lancedb, model: text-embedding-3-small-1)

# On laptop
openclaw ltm stats
# Expected: same total count — both sides see the same shared index

# Search the index from either side
openclaw ltm search "your query"
```

---

## Reference

| Resource | Default in this guide |
|---|---|
| Working directory | `~/openclaw/` |
| Config source of truth | `~/openclaw/config.json` |
| Secrets file (gitignored) | `~/openclaw/env.sh` |
| Apply script | `~/openclaw/apply-config.sh` |
| Deploy script | `~/openclaw/deploy.sh` |
| Container name | `openclaw-container-dgonor` |
| Image | `${ACR_NAME}.azurecr.io/openclaw:${IMAGE_TAG}` |
| Public port | `18789` |
| FQDN pattern | `<DNS_LABEL>.<region>.azurecontainer.io` |
| Config path inside container | `/home/node/.openclaw/openclaw.json` |
| Required env vars | `AZURE_OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY` |
| Memory blob storage | `az://openclaw-memory/lancedb` in `openclawstoragedgonor` |
| Embedding deployment | `text-embedding-3-small-1` → 1536 dims |
| Default model key | `agents.defaults.model` |
| Memory slot owner key | `plugins.slots.memory` |
| Memory CLI subcommand | `openclaw ltm` (when lancedb owns the slot, not `openclaw memory`) |
| OpenClaw docs | https://docs.openclaw.ai |
