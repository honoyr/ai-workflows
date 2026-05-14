# Obsidian Wiki Integration for OpenClaw

**Status:** design
**Scope:** container (Azure ACI) + Mac + iPhone now; VM later (Phase 6, out of scope)
**Related:** [`openclaw-local-mac-pro-feasibility.md`](../../guides/openclaw-local-mac-pro-feasibility.md) Appendix A · OpenClaw [`plugins/memory-wiki`](https://docs.openclaw.ai/plugins/memory-wiki) · [`cli/wiki`](https://docs.openclaw.ai/cli/wiki)

## 1. Problem

1. Sessions produce scattered knowledge; no way to revisit or learn a topic without re-prompting the agent.
2. Files sent to OpenClaw (Telegram/UI) lose their connection to the synthesized notes that referenced them. No "click to see the original PDF you sent last week."
3. No unified view across container, future VM, Mac, and iPhone.

## 2. Goals

| # | Goal |
|---|---|
| G1 | Single Obsidian-compatible vault, readable on Mac and iPhone via native Obsidian apps. |
| G2 | Container writes synthesized knowledge (`sources/`, `entities/`, `concepts/`, `syntheses/`, `reports/`). |
| G3 | Source files sent during sessions are linked back to original via a **portable URL** (provider-agnostic). |
| G4 | iPhone access works **without paid services** (iCloud + iSH for write-back). |
| G5 | Storage portable: switching from Azure Blob to GCS/S3 later requires **zero markdown edits**. |
| G6 | Architecture extends to local VM/Mac Pro later with no design changes. |

## 3. Non-goals

- Multi-user collaborative editing.
- Real-time sync. Eventual consistency (< 1 h lag) is fine for the revisit/review use case.
- Replacing `memory-lancedb` / `active-memory`. They stay the recall layer; the vault is the compiled-knowledge layer.

## 4. How the two OpenClaw docs relate

The user's confusion is reasonable. Both docs describe the **same wiki** through different surfaces:

| Surface | What it is | Who uses it |
|---|---|---|
| `plugins/memory-wiki` | The plugin/engine — vault layout, compile pipeline, structured claims, dashboards, bridge mode, agent tools (`wiki_search`, `wiki_get`, `wiki_apply`, `wiki_lint`). | The OpenClaw runtime + agents. |
| `cli/wiki` | The CLI surface for the same plugin — `openclaw wiki status/init/ingest/compile/search/get/apply/bridge import/obsidian …`. | Humans and ops scripts. |
| Obsidian app | A **third client** opening the same `<vault>/` folder as a vault. Knows nothing about OpenClaw. | Humans on Mac/iPhone. |

They're **complementary**: same files, three readers. Anything one writes the others can read.

## 5. Current state (container)

`~/repos/openclaw-deploy/config/config.json` already has memory-wiki enabled in **bridge mode** at `/mnt/openclaw-workspace/wiki/main` with `renderMode: native`. `active-memory` was just enabled in the prior task. The only missing pieces for Obsidian access are:

1. Flip `renderMode` from `native` → `obsidian` and add the `obsidian.*` config block.
2. Wire a **file-ingest pipeline**: file received → blob upload → `wiki ingest` → `sources/*.md`.
3. Wire **git** as the vault transport.
4. Stand up a **Cloudflare Worker** at `attach.posthub.cc` for portable attachment URLs.
5. Hook up **Mac + iPhone** clients.
6. Add **rclone backup** mirror to Google Drive.

## 6. Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ CONTAINER (Azure ACI: openclaw-container-dgonor)                  │
│                                                                   │
│ ┌──────────────────────────┐   ┌─────────────────────────────┐    │
│ │ OpenClaw runtime         │   │ /mnt/openclaw-workspace     │    │
│ │  · memory-lancedb        │   │  /wiki/main          ←vault │    │
│ │  · active-memory         │──►│   ├─ sources/               │    │
│ │  · memory-wiki (bridge,  │   │   ├─ entities/              │    │
│ │    renderMode=obsidian)  │   │   ├─ concepts/              │    │
│ │  · file-ingest handler   │   │   ├─ syntheses/             │    │
│ └──────────┬───────────────┘   │   ├─ reports/               │    │
│            │                   │   ├─ _attachments/  (.gitignored)│
│            │ blob put          │   └─ .openclaw-wiki/cache/  │    │
│            ▼                   │  /.git-openclaw-vault/      │    │
│ ┌──────────────────────────┐   │   (separate git dir)        │    │
│ │ Azure Blob               │   └──────────────┬──────────────┘    │
│ │ openclaw-attachments/    │                  │                   │
│ │  <sha256>/<filename>     │                  │ cron */15: git    │
│ └──────────┬───────────────┘                  │ commit + push     │
│            │                                  ▼                   │
│            │ rclone hourly  ┌─────────────────────────┐           │
│            ▼                │ github.com/honoyr/      │           │
│ ┌──────────────────────────┐│ openclaw-vault (private)│           │
│ │ Google Drive backup      │└─────────────────────────┘           │
│ │  openclaw-backup/        │           ▲                          │
│ │  attachments/            │           │ git pull / push          │
│ └──────────────────────────┘           │                          │
└────────────────────────────────────────│──────────────────────────┘
                                         │
       ┌─────────────────────────────────┼────────────────────────┐
       ▼                                                          ▼
┌──────────────────────────────────┐              ┌────────────────────────────┐
│ MAC                              │              │ iPHONE                     │
│ Working tree:                    │  iCloud      │ Obsidian iOS               │
│  ~/Library/Mobile Documents/     │  syncs       │  vault = iCloud folder     │
│  com~apple~CloudDocs/            │─────────────►│                            │
│  openclaw-vault/                 │              │ iSH shell                  │
│ .git separate:                   │              │  · git pull / push         │
│  ~/.local/share/                 │              │  · uses gh CLI w/ PAT      │
│  openclaw-vault.git/             │              └────────────────────────────┘
│ launchd agent (5 min):           │
│  git pull --rebase               │
│ fswatch debounced:               │
│  git commit -am … && git push    │
│ Obsidian Mac app: opens vault    │
└──────────────────────────────────┘
            ▲
            │ user clicks attachment link
            ▼
   https://attach.posthub.cc/<sha256>
   (Cloudflare Worker; free tier)
            │
            ▼ 302 to current backend (SAS URL)
   azure today · gcs tomorrow · whatever next year
```

## 7. Components

### 7.1 memory-wiki Obsidian render mode

In `config/config.json`, under `plugins.entries.memory-wiki.config`:

- `vault.renderMode: "obsidian"` (was `native`)
- Add `obsidian` block:
  - `enabled: true`
  - `useOfficialCli: false` (CLI not packaged in container; agent doesn't need it for now)
  - `vaultName: "OpenClaw Vault"`
  - `openAfterWrites: false`
- Keep everything else — `vaultMode: bridge`, `search.corpus: all`, dashboards on.

### 7.2 Vault git repo

- **Remote:** `github.com/honoyr/openclaw-vault` (private). Default branch `main`.
- **`.git` location:** **separate from vault folder** to dodge iCloud-evicts-`.git` corruption on Mac, and to keep the Azure Files share clean.
  - Container: `/mnt/openclaw-workspace/.git-openclaw-vault/`
  - Mac: `~/.local/share/openclaw-vault.git/`
  - Initialized with `git init --separate-git-dir <gitdir> <worktree>`.
- **`.gitignore`** at vault root:
  - `_attachments/` (binaries go to blob, not git)
  - `.openclaw-wiki/cache/*.json` (regenerated by `wiki compile`; cause merge churn)
  - `.obsidian/workspace*.json` (per-device window state)
  - `.DS_Store`
- **Auth:** GitHub Personal Access Token stored in:
  - Container: `env.sh` secret loaded as `OPENCLAW_VAULT_GIT_TOKEN`
  - Mac: macOS Keychain via `git credential-osxkeychain`
  - iPhone iSH: `~/.netrc` (it's a personal device)

### 7.3 Attachments pipeline

Triggered when a user sends a file via any channel that the container ingests (Telegram, UI). Two viable approaches:

**Option A — handler in OpenClaw runtime (preferred)**
Extend the existing file-handling path (the same one that already routes photos to `openclaw-inbox-photos` blob) so all non-photo file types go to a new container, `openclaw-attachments`, and trigger `wiki ingest`.

**Option B — workspace watcher**
A cron in the container watches `/mnt/openclaw-workspace/file-inbox/`, processes new files, and calls `openclaw wiki ingest`.

We pick **Option A** when possible (lower latency, fewer moving parts); fall back to B if the runtime path is hard to extend without modifying OpenClaw core.

Per-file flow:

1. Compute sha256 of original bytes.
2. PUT to `openclaw-attachments/<sha256>/<original-filename>` (idempotent on sha256).
3. Extract a text excerpt (existing PDF/OCR/audio paths the runtime already has).
4. Call `openclaw wiki ingest --stdin` with payload:
   ```yaml
   ---
   pageType: source
   id: source.<sha256-short>
   sourceId: source.<sha256-short>
   sha256: <sha256>
   attachment: https://attach.posthub.cc/<sha256>
   mime: <mime>
   origFilename: <name>
   origin: telegram://chat/<chatId>/message/<msgId>
   uploadedBy: <user>
   uploadedAt: <iso>
   privacyTier: local-private
   ---
   ```
   Body = extracted text/OCR/transcript.
5. Wiki writes `sources/<slug>.md`; `wiki compile` (auto, already enabled) regenerates digests/dashboards.

### 7.4 Cloudflare Worker `attach.posthub.cc`

- New subdomain on existing `posthub.cc` zone in Cloudflare (already managed for `openclaw.posthub.cc`).
- Worker resolves `/<sha256>` → 302 redirect to a **short-lived signed URL** from the current backend.
- Backend mapping in **Workers KV**:
  ```json
  { "default_backend": "azure",
    "azure": { "account": "openclawstoragedgonor", "container": "openclaw-attachments" } }
  ```
  Adding GCS later = add a `gcs` key and flip `default_backend`.
- Signed URL generation (Azure SAS) requires the Worker to have a Storage Account Key. Two options:
  - **Account-scoped read-only SAS minted at the edge** (Worker holds the account key as a Worker secret). Simpler.
  - **User-delegation key fetched via a sidecar service every hour.** More secure but adds infra. Defer.
- Picked: account-key + short SAS (10-minute expiry) for now; rotate quarterly.
- Security: sha256 is 256 bits of entropy → practically unguessable. Acceptable for `privacyTier: local-private`. For `sensitive`/`confirm-before-use`, add a short HMAC token in the path (`/<sha>/<hmac>`); deferred to phase 2.

### 7.5 Container ↔ GitHub sync

- Post-ingest hook (Option A path) or `wiki compile` post-step: `git add -A && git commit -m "wiki: ingest <id>" && git push origin main` (lock-protected).
- Belt-and-braces cron `*/15 * * * *`: `git add -A && git commit -m "wiki: periodic" --allow-empty=false && git push` for anything the hook missed.
- Lock file `/mnt/openclaw-workspace/.git-openclaw-vault/.push.lock` to serialize.

### 7.6 Azure Blob ↔ Google Drive backup

- rclone configured in container image (`apk add rclone` or download static binary).
- Token: Google Service Account JSON in `env.sh` as `GDRIVE_SA_JSON`.
- Cron `0 * * * *`: `rclone sync azureblob:openclaw-attachments gdrive:openclaw-backup/attachments --fast-list --transfers=4`.
- Backup is one-way (Azure → GDrive). Restore is manual.

### 7.7 Mac client

- Clone: `git clone --separate-git-dir ~/.local/share/openclaw-vault.git git@github.com:honoyr/openclaw-vault.git ~/Library/Mobile\ Documents/com~apple~CloudDocs/openclaw-vault`
- Right-click the vault folder in Finder → **"Keep Downloaded"** (forces iCloud to never evict files; critical for Obsidian responsiveness and prevents `.obsidian/` corruption).
- launchd agent `com.user.openclaw-vault-pull.plist`: runs every 5 minutes, `git pull --rebase`.
- launchd agent `com.user.openclaw-vault-push.plist`: triggered by `fswatch` on vault root (debounced 30 s), `git add -A && git commit -m "mac: $(date)" && git push`.
- Obsidian app: open the vault folder. Recommended plugins: **Outliner**, **Dataview** (for reading reports), **Templater**, **Backlinks** (built-in).

### 7.8 iPhone client

- iCloud Drive auto-syncs the vault folder from Mac.
- Obsidian iOS: open vault from iCloud Drive → "OpenClaw Vault" appears.
- iSH shell app (free, sideloaded):
  - One-time setup: `apk add git openssh`, `ssh-keygen`, add pubkey to GitHub.
  - When you edit on iPhone and want to push: `cd ~/iCloud-shadow/openclaw-vault && git add -A && git commit -m … && git push`. (iSH can access iCloud via `mount`-ing the Files provider; see implementation.md for the exact dance.)
- Pull from iPhone (rare): `git pull --rebase`.

## 8. Conflict handling

- Container writes: `sources/` (new files, never edits), `reports/*.md` (regenerated), generated blocks inside `entities/*.md` and `concepts/*.md`. Human note blocks within those files are **preserved by memory-wiki** by design.
- Mac/iPhone writes: human notes, `concepts/`, `syntheses/`, free-form pages anywhere.
- Conflict probability is low because files are mostly partitioned by writer.
- For `reports/*.md` regeneration vs. human edits: enable `.gitattributes`:
  ```
  reports/* merge=ours
  ```
  Container's regenerated version wins. Human notes for reports go in a sibling `reports/_notes/` folder.
- `.openclaw-wiki/cache/*.json` is `.gitignore`'d → no conflicts.

## 9. Provider migration runbook (Azure → GCS example)

1. `rclone copy azureblob:openclaw-attachments gcs:openclaw-attachments` (preserve `<sha256>/<name>` keys).
2. Update Worker KV: `default_backend: "gcs"` + add `gcs` config block + GCS HMAC key as Worker secret.
3. Test: `curl -I https://attach.posthub.cc/<known-sha>` returns 302 to a GCS-signed URL.
4. Remove Azure backend after 30-day validation period.

**Zero markdown edits. Zero broken iPhone links.**

## 10. Phase 6 (VM, out of scope)

When the local Mac Pro VM comes up:

- VM clones `openclaw-vault.git` to its own working tree.
- VM writes to **the same `openclaw-attachments` blob today** via the Worker URL (the Worker doesn't care where the writer lives).
- VM contributes to the same git history. Multiple writers (container + VM + Mac) → merge conflicts get harder, but rebase + `.gitattributes ours/theirs` rules contain it.
- Alternative: VM gets its own vault folder in the repo (`vaults/local-mac-pro/`) and the container's stays at `vaults/container/`. Defer this decision until VM bring-up.

## 11. Open questions / parking lot

| Item | Decision needed by |
|---|---|
| HMAC tokens for `sensitive` privacy tier on Worker URLs | Before any sensitive doc is ingested |
| Should `wiki compile` rebuild dashboards on Mac too, or container-only? | Phase 2 |
| Obsidian community plugins to bake into the vault `.obsidian/` config (commit or per-device)? | Phase 2 |
| Multi-vault layout if VM joins (per-environment vs. unified)? | Phase 6 / VM bring-up |
| iSH on iPhone may require Sideloadly / AltStore (TestFlight version available). User to confirm install path. | Phase 5 (iPhone) |
