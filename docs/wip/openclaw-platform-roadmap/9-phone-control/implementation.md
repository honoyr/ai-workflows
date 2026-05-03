# Implementation — Phone-based ACI start/stop

> Design: [`./design.md`](./design.md)
> Lives in `openclaw-deploy` repo (Topic 8 dependency). Adds `function/` folder + `bicep/modules/function.bicep`.

## Step 1: Bicep — Function App + identity + role assignment

`bicep/modules/function.bicep`:

```bicep
param functionAppName string
param storageAccountName string  // reuse existing
param rgName string
param tenantId string = subscription().tenantId

resource fn 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: resourceGroup().location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    siteConfig: {
      linuxFxVersion: 'NODE|20'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: storageConnString }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'node' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'TELEGRAM_BOT_TOKEN', value: '@Microsoft.KeyVault(SecretUri=${kv}/secrets/telegram-bot-token)' }
        { name: 'TELEGRAM_WEBHOOK_SECRET', value: '@Microsoft.KeyVault(SecretUri=${kv}/secrets/telegram-webhook-secret)' }
        { name: 'SHORTCUT_SECRET', value: '@Microsoft.KeyVault(SecretUri=${kv}/secrets/shortcut-secret)' }
        { name: 'AZ_RG', value: rgName }
        { name: 'AZ_CONTAINER', value: 'openclaw-container-dgonor' }
        { name: 'AZ_SUBSCRIPTION', value: subscription().subscriptionId }
        { name: 'OPENCLAW_TG_WEBHOOK', value: 'https://openclaw.posthub.cc/api/v1/webhook/telegram' }
        { name: 'ALLOWED_CHAT_IDS', value: '<your-telegram-chat-id>' }
      ]
    }
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${functionAppName}-plan'
  location: resourceGroup().location
  sku: { name: 'Y1', tier: 'Dynamic' }
  kind: 'functionapp'
  properties: { reserved: true }
}

// Role assignment: ACI Contributor on RG
resource aciRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(rgName, functionAppName, 'aci-contributor')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '...container-instance-contributor-id...')
    principalId: fn.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionUrl string = 'https://${fn.properties.defaultHostName}'
```

(Look up the exact role definition ID for `Container Instance Contributor`: `az role definition list --name "Container Instance Contributor" --query [0].id -o tsv`.)

Wire into `bicep/main.bicep`:

```bicep
module fn './modules/function.bicep' = {
  name: 'function'
  params: {
    functionAppName: 'openclaw-control-dgonor'
    storageAccountName: storage.outputs.name
    rgName: resourceGroup().name
  }
}
```

Add `telegram-webhook-secret` and `shortcut-secret` to Key Vault (generate with `openssl rand -hex 32`).

## Step 2: Function code

`function/control/function.json`:

```json
{
  "bindings": [
    { "type": "httpTrigger", "direction": "in", "name": "req", "methods": ["post", "get"], "authLevel": "anonymous" },
    { "type": "http", "direction": "out", "name": "$return" }
  ]
}
```

`function/control/index.js`:

```js
const { DefaultAzureCredential } = require('@azure/identity');
const { ContainerInstanceManagementClient } = require('@azure/arm-containerinstance');

const SUB = process.env.AZ_SUBSCRIPTION;
const RG = process.env.AZ_RG;
const CONTAINER = process.env.AZ_CONTAINER;
const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TG_WEBHOOK_SECRET = process.env.TELEGRAM_WEBHOOK_SECRET;
const SHORTCUT_SECRET = process.env.SHORTCUT_SECRET;
const OPENCLAW_WEBHOOK = process.env.OPENCLAW_TG_WEBHOOK;
const ALLOWED = (process.env.ALLOWED_CHAT_IDS || '').split(',').map(s => s.trim());

const cred = new DefaultAzureCredential();
const aci = new ContainerInstanceManagementClient(cred, SUB);

async function tgReply(chat_id, text) {
  await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id, text })
  });
}

async function ensureAllowed(chat_id) {
  if (!ALLOWED.includes(String(chat_id))) {
    await tgReply(chat_id, '⛔ Not authorised.');
    return false;
  }
  return true;
}

async function aciStart() { await aci.containerGroups.beginStart(RG, CONTAINER); }
async function aciStop()  { await aci.containerGroups.beginStop(RG, CONTAINER); }
async function aciStatus() {
  const cg = await aci.containerGroups.get(RG, CONTAINER);
  return cg.instanceView?.state || cg.provisioningState || 'unknown';
}

module.exports = async function (context, req) {
  // Path A: iOS Shortcut → ?secret=...&action=start|stop|status
  if (req.query.secret) {
    if (req.query.secret !== SHORTCUT_SECRET) return { status: 403, body: 'forbidden' };
    const a = req.query.action;
    try {
      if (a === 'start') { await aciStart(); return { body: 'starting' }; }
      if (a === 'stop')  { await aciStop();  return { body: 'stopping' }; }
      if (a === 'status') { return { body: await aciStatus() }; }
      return { status: 400, body: 'bad action' };
    } catch (e) { return { status: 500, body: e.message }; }
  }

  // Path B: Telegram webhook
  const tgSecret = req.headers['x-telegram-bot-api-secret-token'];
  if (tgSecret !== TG_WEBHOOK_SECRET) return { status: 403, body: 'forbidden' };

  const upd = req.body;
  const msg = upd?.message;
  if (!msg) return { status: 200, body: 'ignored' };

  const text = (msg.text || '').trim();
  const chat_id = msg.chat.id;

  if (!await ensureAllowed(chat_id)) return { status: 200, body: 'ok' };

  try {
    if (text === '/start_aci')      { await aciStart(); await tgReply(chat_id, '🟢 Starting OpenClaw…'); }
    else if (text === '/stop_aci')  { await aciStop();  await tgReply(chat_id, '🛑 Stopping OpenClaw…'); }
    else if (text === '/status_aci') { await tgReply(chat_id, `📊 ${await aciStatus()}`); }
    else {
      // Pass through to OpenClaw gateway
      const status = await aciStatus();
      if (status === 'Running') {
        await fetch(OPENCLAW_WEBHOOK, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(upd)
        });
      } else {
        await tgReply(chat_id, `⏳ OpenClaw is ${status}. Send /start_aci to wake it up.`);
      }
    }
  } catch (e) {
    context.log.error(e);
    await tgReply(chat_id, `❌ ${e.message}`);
  }

  return { status: 200, body: 'ok' };
};
```

`function/host.json`:

```json
{ "version": "2.0", "extensionBundle": { "id": "Microsoft.Azure.Functions.ExtensionBundle", "version": "[4.*, 5.0.0)" } }
```

`function/package.json`:

```json
{ "name": "openclaw-control", "version": "1.0.0", "dependencies": { "@azure/identity": "^4.0.0", "@azure/arm-containerinstance": "^9.0.0" } }
```

## Step 3: Deploy the Function

```bash
cd function
npm install
func azure functionapp publish openclaw-control-dgonor --javascript
```

Or via GitHub Actions in `openclaw-deploy`:

```yaml
# .github/workflows/deploy-function.yml
name: deploy-function
on:
  push:
    branches: [main]
    paths: ['function/**']
permissions: { id-token: write, contents: read }
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - uses: azure/functions-action@v1
        with:
          app-name: openclaw-control-dgonor
          package: function/
```

## Step 4: Point Telegram webhook at the Function

```bash
TG_TOKEN=$(az keyvault secret show --vault-name "$VAULT" -n telegram-bot-token --query value -o tsv)
TG_SECRET=$(az keyvault secret show --vault-name "$VAULT" -n telegram-webhook-secret --query value -o tsv)
FN_URL="https://openclaw-control-dgonor.azurewebsites.net/api/control"

curl -X POST "https://api.telegram.org/bot${TG_TOKEN}/setWebhook" \
  -H 'content-type: application/json' \
  -d "{\"url\": \"${FN_URL}\", \"secret_token\": \"${TG_SECRET}\"}"
```

Verify: `curl https://api.telegram.org/bot${TG_TOKEN}/getWebhookInfo`

## Step 5: Test from phone

1. Send `/status_aci` to `@dgonorOpenClawbot` → reply with current state.
2. Send `/stop_aci` → state changes to "Stopping" within seconds.
3. Send `/start_aci` → state changes to "Running" within ~30 s.
4. Send a regular chat message while stopped → reply tells you to start first.

## Step 6: iOS Shortcut

Create three Shortcuts:

- **Start OpenClaw:** Get Contents of URL `https://openclaw-control-dgonor.azurewebsites.net/api/control?secret=<SHORTCUT_SECRET>&action=start` (POST).
- **Stop OpenClaw:** same URL with `action=stop`.
- **OpenClaw Status:** same URL with `action=status`, show result.

Add Shortcuts to Home Screen for one-tap.

## Step 7: In-band stop via openclaw skill

### C.7.a Install azure-cli in wrapper

In `docker/Dockerfile`:

```dockerfile
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash
```

### C.7.b Grant container's identity ACI Contributor

If ACI has system-assigned identity, add a Bicep block in `aci.bicep`:

```bicep
identity: { type: 'SystemAssigned' }
```

And a role assignment:

```bicep
resource aciSelfRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, aci.id, 'self-stop')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '...container-instance-contributor-id...')
    principalId: aci.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
```

### C.7.c `aci-control` skill

`config/skills/aci-control/SKILL.md`:

```markdown
---
name: aci-control
description: Stop or check status of this OpenClaw ACI container.
---

When the user says "shut down" / "stop yourself" / "go to sleep", run:
`az login --identity --allow-no-subscriptions`
`az container stop --resource-group <RG> --name <CONTAINER> --no-wait`

When the user says "what's your status", run:
`az container show --resource-group <RG> --name <CONTAINER> --query "instanceView.state" -o tsv`

Reply with the result before the container stops.
```

(Substitute RG/CONTAINER from container env vars at runtime.)

## Step 8: Smoke test in-band

`/agent default` then say "shut down". Should reply "stopping in 60 s" then container actually stops shortly after.

## Rollback

If the Function misbehaves, restore Telegram webhook directly to OpenClaw:

```bash
curl -X POST "https://api.telegram.org/bot${TG_TOKEN}/setWebhook" \
  -d "url=https://openclaw.posthub.cc/api/v1/webhook/telegram"
```

In-band stop can be disabled by removing the `aci-control` skill from `config.json`.

## Operational notes

- Keep one Telegram chat_id allowlisted at a time. Adding a second user means another row in `ALLOWED_CHAT_IDS` env var (Bicep redeploy).
- Function logs in Application Insights — check there if commands seem to be ignored.
- Webhook secret rotation: regenerate, update Key Vault, redeploy Function (env vars refresh automatically), call `setWebhook` again with the new secret.
