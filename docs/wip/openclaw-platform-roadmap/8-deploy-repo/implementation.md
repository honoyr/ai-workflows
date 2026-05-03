# Implementation — `openclaw-deploy` repo

> Design: [`./design.md`](./design.md)

## Step 1: Create the repo + scaffold

```bash
gh repo create openclaw-deploy --private --clone
cd openclaw-deploy
mkdir -p docker scripts bicep/modules config docs .github/workflows
```

Add `.gitignore`:

```
.upstream-version
.last-build
env.sh
*.log
.DS_Store
```

`README.md` lives at root with a quickstart + architecture diagram.

## Step 2: Move and adapt scripts

Copy the post-Topic-1 versions of these into `scripts/`:

- `pull-latest.sh`
- `build-image.sh`
- `deploy.sh`
- `local-up.sh` (will be authored under Topic 5; placeholder for now)

Adjust pathing: `HERE` should resolve to `scripts/`, not the repo root. Update relative paths to `docker/` and `config/` accordingly.

Move `docker/Dockerfile` and `docker/openclaw-init.sh` to `docker/`.

Move `config.json` → `config/config.json`.

Add `scripts/env.sh.example` documenting required env vars (no values):

```bash
export SUBSCRIPTION="..."
export RG="..."
export ACR_NAME="..."
export VAULT="..."           # Azure Key Vault name
export CONTAINER="..."
# Secrets (hydrated by env.sh from Key Vault):
# AZURE_OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, AZURE_STORAGE_KEY,
# BRAVE_SEARCH_API, CF_TUNNEL_TOKEN
export WRAPPER_REV=1
```

## Step 3: Provision Azure Key Vault

```bash
az keyvault create \
  --name openclaw-kv-dgonor \
  --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --enable-rbac-authorization true

# Grant yourself secret-management
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/openclaw-kv-dgonor"

# Populate from current env.sh
source ~/openclaw/env.sh
for pair in \
  "azure-openai-api-key:$AZURE_OPENAI_API_KEY" \
  "telegram-bot-token:$TELEGRAM_BOT_TOKEN" \
  "azure-storage-key:$AZURE_STORAGE_KEY" \
  "brave-search-api:$BRAVE_SEARCH_API" \
  "cf-tunnel-token:$CF_TUNNEL_TOKEN"; do
    name="${pair%%:*}"; value="${pair#*:}"
    az keyvault secret set --vault-name openclaw-kv-dgonor -n "$name" --value "$value"
done
```

## Step 4: Rewrite `scripts/env.sh`

```bash
#!/usr/bin/env bash
# Non-secret config — safe to commit (this file only after secrets removed)
export SUBSCRIPTION="d7e7222e-bf82-4b07-b21c-f8c7d06b3607"
export RG="myCentralRG"
export ACR_NAME="acrdgonor2"
export VAULT="openclaw-kv-dgonor"
export CONTAINER="openclaw-container-dgonor"
export WRAPPER_REV=1

_kv() { az keyvault secret show --vault-name "$VAULT" -n "$1" --query value -o tsv 2>/dev/null; }

export AZURE_OPENAI_API_KEY="$(_kv azure-openai-api-key)"
export TELEGRAM_BOT_TOKEN="$(_kv telegram-bot-token)"
export AZURE_STORAGE_KEY="$(_kv azure-storage-key)"
export BRAVE_SEARCH_API="$(_kv brave-search-api)"
export CF_TUNNEL_TOKEN="$(_kv cf-tunnel-token)"

# Sanity
for v in AZURE_OPENAI_API_KEY TELEGRAM_BOT_TOKEN AZURE_STORAGE_KEY BRAVE_SEARCH_API CF_TUNNEL_TOKEN; do
  [[ -n "${!v}" ]] || { echo "[env] missing $v from Key Vault $VAULT" >&2; return 1; }
done
```

Note this file IS committed once it contains zero secret values; `env.sh.example` exists for first-time setup documentation.

## Step 5: Bicep — describe existing infra

`bicep/main.bicep`:

```bicep
targetScope = 'resourceGroup'

@description('Environment name (dev/prod)')
param env string = 'dev'

module acr './modules/acr.bicep' = {
  name: 'acr'
  params: { name: 'acrdgonor2' }
}

module storage './modules/storage.bicep' = {
  name: 'storage'
  params: { name: 'openclawstoragedgonor', shareName: 'openclaw-workspace' }
}

module keyvault './modules/keyvault.bicep' = {
  name: 'keyvault'
  params: { name: 'openclaw-kv-dgonor' }
}

module aci './modules/aci.bicep' = {
  name: 'aci'
  params: {
    name: 'openclaw-container-dgonor'
    acrName: acr.outputs.name
    storageAccountName: storage.outputs.name
    shareName: storage.outputs.shareName
    keyVaultName: keyvault.outputs.name
  }
  dependsOn: [acr, storage, keyvault]
}
```

Each module describes the existing resource using current property values (pull from `az resource show`). `aci.bicep` is the largest — it includes container env vars referencing Key Vault secrets via `secureValue`.

Validate:

```bash
az deployment group what-if \
  --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --template-file bicep/main.bicep \
  --parameters bicep/parameters.dev.json
```

Expect "no changes". If `what-if` shows changes, update Bicep until it matches.

## Step 6: GitHub Actions — OIDC + workflows

### Federated credential

```bash
# Create app registration for Actions
APP_ID=$(az ad app create --display-name openclaw-deploy-actions --query appId -o tsv)
SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)

# Federated credential for main branch
az ad app federated-credential create --id "$APP_ID" --parameters @- <<EOF
{
  "name": "main-branch",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<your-gh-user>/openclaw-deploy:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF

# Roles: ACR contributor, Key Vault secrets user, AcI contributor
RG_SCOPE="/subscriptions/$SUBSCRIPTION/resourceGroups/$RG"
for role in "AcrPush" "Key Vault Secrets User" "Container Instance Contributor"; do
  az role assignment create --assignee "$APP_ID" --role "$role" --scope "$RG_SCOPE"
done
```

Add GitHub repo secrets: `AZURE_CLIENT_ID=$APP_ID`, `AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)`, `AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION`.

### `.github/workflows/build-and-import.yml`

```yaml
name: build-and-import
on:
  push:
    branches: [main]
    paths: ['docker/**', 'scripts/pull-latest.sh', 'scripts/build-image.sh']
permissions: { id-token: write, contents: read }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - run: source scripts/env.sh && scripts/build-image.sh
```

### `.github/workflows/deploy.yml`

```yaml
name: deploy
on: { workflow_dispatch: }
permissions: { id-token: write, contents: read }
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: prod   # require reviewer approval via env protection rule
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - run: source scripts/env.sh && scripts/deploy.sh
```

In GitHub repo settings → Environments → `prod`, add yourself as a required reviewer.

## Step 7: Migrate, cut over, delete plaintext secrets

```bash
# After verifying Bicep what-if + Actions both work end-to-end:
# 1. push first commit to openclaw-deploy
# 2. test deploy via Actions
# 3. confirm container running new image
# 4. archive ~/openclaw/ as ~/openclaw.archive-pre-migration/ (keep as emergency rollback)
# 5. delete plaintext env.sh from anywhere outside Key Vault
mv ~/openclaw ~/openclaw.archive-pre-migration
```

Update the operator setup guide (`docs/guides/openclaw-azure-setup-guide.md`) with the new repo location and "to deploy, push to main and run `gh workflow run deploy.yml`".

## Step 8: Verify rollback path works

Pick the previous wrapper image tag (`openclaw:custom-2026.4.27-1`) and roll back via:

```bash
gh workflow run deploy.yml -f wrapper_tag=custom-2026.4.27-1
```

(Add a `wrapper_tag` input to `deploy.yml` so manual rollback to a specific tag is possible.)

## Risks during migration

- ACI container restart during cutover — accept ~30 s downtime; do during low-use window.
- Bicep drift causing accidental resource recreation — `what-if` is mandatory before any apply.
- Locking ourselves out of Key Vault — keep one emergency RBAC role assignment for your user identity outside of any Bicep-managed scope.
