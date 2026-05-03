# Implementation — Bump session-lock acquire timeout

> Design: [`./design.md`](./design.md)
> Estimated total wall-clock: ~15 min including redeploy.

## Step 1: Identify the exact config key

The OpenClaw config schema has multiple plausible keys for this timeout. We need to find the right one rather than guessing. From a working container:

```sh
source ~/openclaw/env.sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "gosu node openclaw config show --json"
```

Search the output for keys containing `lockTimeout`, `acquireTimeout`, or `session.lock`. Likely candidates:

- `gateway.session.lock.acquireTimeoutMs`
- `agents.session.acquireTimeoutMs`
- `messages.session.lockAcquireTimeoutMs`

Note the exact path. If `config show` doesn't expose it, grep the openclaw npm package source for `LockTimeout` or the literal string `10000` next to `session`/`jsonl`:

```sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "gosu node sh -c 'grep -rn LockTimeout /usr/local/lib/node_modules/@openclaw/openclaw/dist/ | head -20'"
```

## Step 2: Add the override

Edit `~/openclaw/config.json` and add the override under the relevant section. Example assuming the key is `gateway.session.lock.acquireTimeoutMs`:

```jsonc
{
  // ...existing config...
  "gateway": {
    "session": {
      "lock": {
        "acquireTimeoutMs": 60000
      }
    }
  }
}
```

If the key sits under `agents` or `messages`, follow the same pattern. Re-base64-encode for `OPENCLAW_CONFIG_B64` (handled automatically by `deploy.sh` reading `config.json`).

## Step 3: Redeploy

```sh
~/openclaw/deploy.sh
```

No image rebuild needed — the config is injected via env var. Wait for the container to be `Running`, then verify it picked up the new value:

```sh
az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --name "$CONTAINER" \
  --exec-command "gosu node openclaw config get gateway.session.lock.acquireTimeoutMs"
```

(Substituting the actual key path.) Expected output: `60000`.

## Step 4: Validation under load

In three terminals:

1. **Terminal A:** `az container logs --follow --resource-group "$RG" --name "$CONTAINER" | grep -i lock`
2. **Terminal B:** open `https://openclaw.posthub.cc` and start a chat, paste the gateway token, send messages every few seconds.
3. **Terminal C:** in parallel, run a CLI probe loop:

```sh
for i in {1..20}; do
  az container exec --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --name "$CONTAINER" \
    --exec-command "gosu node openclaw infer model run --model microsoft-foundry/Kimi-K2.6-1 --prompt 'ping $i'"
  sleep 2
done
```

While B and C run, also send a Telegram message to `@dgonorOpenClawbot`.

**Pass criteria:** Terminal A shows zero `SessionWriteLockTimeoutError` lines for the duration of the test.

## Step 5: Commit + record

- Commit `~/openclaw/config.json` change to whatever versioning lives there today (will move to `openclaw-deploy` repo per Task 2).
- Update Topic-0b status in `tasks.md` from `pending` → `done`.
- Drop a one-line note under `docs/guides/openclaw-azure-setup-guide.md`'s troubleshooting section: "Session lock timeout raised from 10 s to 60 s — see Topic 0b in the platform roadmap."

## Rollback

If the new timeout introduces a regression (e.g., a real deadlock blocks the runtime for 60 s and breaks UX), revert by removing the config override and redeploying. SMB lock failures will return but the system functions.
