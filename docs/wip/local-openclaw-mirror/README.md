# Local OpenClaw (macOS) — Diverged from Container

**Cut-over date:** 2026-05-20
**Source:** `~/repos/openclaw-deploy/config/config.json` (Azure ACI container) was used as a one-shot template. After this date, the local and container installs are **independent** — no automatic sync.

## What was copied (one-shot)
- **Plugin config** mirroring container's `plugins.entries`: `active-memory`, `brave`, `memory-lancedb`, `memory-wiki`
- **Skills**: `obscite`, `obsingest`, `obspull`, `obspush`, `insurance-appeal` → `~/.openclaw/skills/`
- **Wiki vault**: `git clone honoyr/openclaw-vault` → `~/Documents/OpenClawVault`
  (initial copy was a tarball extract because git clone hung; remote is set so future `git pull` works)
- **Lancedb vectors**: `az://openclaw-memory/lancedb/` → `~/.openclaw/memory/lancedb/` (47 blobs, ~0.1 MB)

## What was deliberately NOT copied
- **Session history** (`openclaw-state-snapshots/_state/agents/`) — clean slate per user choice
- **Encrypted credentials** (`auth-profile-secrets/`) — AES-GCM blobs tied to container's master key, unusable locally
- **Telegram channel state cache** — local bot (`@dgonor42MacBot`) has its own group (`-1003756481858`)
- **File-ingest watcher / photo-cron / cloudflared tunnel / wiki-git push loop** — Mac-local runs LAN-only

## Source-of-truth split
| Asset | Authoritative location | Notes |
|---|---|---|
| Local session/agent state | `~/.openclaw/` | Trusted to disk; no snapshot loop |
| Local wiki edits | `~/Documents/OpenClawVault` | Push to GitHub manually with `git push` if desired |
| Container session/agent state | `openclaw-state-snapshots` blob | Container ACI continues snapshotting independently |
| Wiki canonical repo | `honoyr/openclaw-vault` (GitHub) | Both installs can push; manual conflict resolution if both edit |
| Attachments | `openclaw-attachments` blob (Azure) | Served via CF worker `attach.posthub.cc` |
| LanceDB | LOCAL `~/.openclaw/memory/lancedb/` and SEPARATE container `az://openclaw-memory/lancedb/` | Two divergent vector stores after this date |

## Plugin/config differences from container
- `gateway.bind = loopback` (LAN-only, no cloudflared)
- `agents.defaults.model.primary = openai-codex/gpt-5.5` (matches container), fallback `microsoft-foundry/Kimi-K2.6-1`
- `memory-lancedb.config.dbPath` is LOCAL — do **NOT** point this at the SMB share (Azure Files); polling cost was $270/mo
- `channels.telegram` uses a different bot (`@dgonor42MacBot`) and group (`-1003756481858`, topics 1/5/8)

## How to maintain
- After config edits: `openclaw gateway restart`
- Logs: `tail -f /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log`
- Plugins list: `openclaw plugins list`
- Doctor: `openclaw doctor`
- Backup of pre-cut-over config: `~/.openclaw/openclaw.json.pre-clone-mirror`
- Backup of pre-Telegram-topic-fix config: `~/.openclaw/openclaw.json.pre-group-setup`

## Re-pulling wiki updates from GitHub
```bash
cd ~/Documents/OpenClawVault
git fetch origin main
git pull --rebase origin main   # or merge if you prefer
```
