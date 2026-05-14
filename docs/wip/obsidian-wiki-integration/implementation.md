# Implementation Plan — Obsidian Wiki Integration

This document expands the design into bite-sized phases. Each phase ends in a working, verifiable state. Phases are ordered so the system stays usable at every step.

**Repos touched**
- `honoyr/openclaw-deploy` — container config + container-side scripts
- `honoyr/openclaw-vault` (new, private) — the vault repo
- New: a tiny Cloudflare Worker project (lives in `honoyr/openclaw-attach-worker` or as a `worker/` folder in deploy repo)

**Pre-reading the implementer should do**
- [`plugins/memory-wiki`](https://docs.openclaw.ai/plugins/memory-wiki) (full page)
- [`cli/wiki`](https://docs.openclaw.ai/cli/wiki)
- [`concepts/memory`](https://docs.openclaw.ai/concepts/memory)
- `~/repos/openclaw-deploy/config/config.json` — see `plugins.entries.memory-wiki` (already present, lines ~274–308) and `plugins.entries.active-memory` (added in prior task)
- `~/repos/openclaw-deploy/scripts/deploy.sh` and `scripts/env.sh` — deploy flow + secret loading
- `~/repos/openclaw-deploy/scripts/smoke-prod.sh` — health-check pattern; we'll add a wiki probe
- `~/repos/ai-workflows/docs/guides/openclaw-local-mac-pro-feasibility.md` Appendix A — memory architecture context

---

## Phase 1 — Vault repo + Cloudflare Worker (foundation)

**Outcome:** Empty private vault repo exists; Worker serves `/<sha256>` 302s for any blob in `openclaw-attachments`. Nothing in OpenClaw is touched yet.

### 1.1 Create the GitHub vault repo

- Create `github.com/honoyr/openclaw-vault` (private).
- Add `README.md` with a one-line description and link back to this design doc.
- Add `.gitignore`:
  ```
  _attachments/
  .openclaw-wiki/cache/*.json
  .obsidian/workspace*.json
  .obsidian/workspace.json
  .DS_Store
  ```
- Add `.gitattributes`:
  ```
  reports/* merge=ours
  *.md text eol=lf
  ```
- Create a deploy-key-style fine-grained PAT scoped to this repo only, `contents: read+write`. Save as a secret to be loaded into container env.

### 1.2 Stand up the Cloudflare Worker

- Create new Worker `openclaw-attach` on the `posthub.cc` zone.
- Add CNAME / route binding: `attach.posthub.cc/* → openclaw-attach`.
- Worker has two responsibilities:
  1. `GET /<sha256>` → mint Azure SAS for `openclaw-attachments/<sha256>/*` (read-only, 10-min TTL), 302 to it.
  2. `GET /` → return 404.
- Worker secrets: `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, `AZURE_CONTAINER=openclaw-attachments`.
- KV namespace `BACKEND_CONFIG` with one key: `default_backend = "azure"`. (Forward-looking; not strictly used yet.)
- Test path: upload a known blob with sha-as-prefix manually via `az`, hit `https://attach.posthub.cc/<sha>`, confirm 302 → download works.
- Document the Worker source in `honoyr/openclaw-deploy/worker/README.md`.

### 1.3 Create the Azure Blob container

- `az storage container create --name openclaw-attachments --account-name openclawstoragedgonor`
- Set access tier `Hot`.
- No public access (Worker mints SAS).

---

## Phase 2 — Container: memory-wiki Obsidian mode + git wiring

**Outcome:** Container's vault folder is a git working tree pushing to the new repo. memory-wiki renders Obsidian-friendly Markdown. No file-ingest yet.

### 2.1 Switch render mode

In `~/repos/openclaw-deploy/config/config.json`, under `plugins.entries.memory-wiki.config`:

- Change `vault.renderMode` from `"native"` → `"obsidian"`.
- Add an `obsidian` block: `enabled: true`, `useOfficialCli: false`, `vaultName: "OpenClaw Vault"`, `openAfterWrites: false`.
- Leave everything else untouched.

### 2.2 Initialize the vault as a git repo (on Azure Files)

- Add a one-time setup script `scripts/wiki-git-init.sh`:
  - Idempotent: only runs if `/mnt/openclaw-workspace/.git-openclaw-vault/HEAD` is missing.
  - Steps: `git init --separate-git-dir /mnt/openclaw-workspace/.git-openclaw-vault /mnt/openclaw-workspace/wiki/main`
  - `git remote add origin https://<token>@github.com/honoyr/openclaw-vault.git`
  - `git -C /mnt/openclaw-workspace/wiki/main pull origin main || true` (in case bootstrap files exist)
- Add `OPENCLAW_VAULT_GIT_TOKEN` to `scripts/env.sh` (do **not** commit; user pastes locally).
- Make the deploy script run `wiki-git-init.sh` once per deploy.

### 2.3 Periodic push from container

- New cron entry in the container's existing cron setup: `*/15 * * * * /usr/local/bin/wiki-push.sh`
- `wiki-push.sh`:
  - Acquire `flock` on `/mnt/openclaw-workspace/.git-openclaw-vault/.push.lock`.
  - `cd /mnt/openclaw-workspace/wiki/main`
  - `git add -A`
  - `git diff --cached --quiet || git commit -m "wiki: periodic $(date -u +%Y-%m-%dT%H:%M:%SZ)"`
  - `git pull --rebase --autostash origin main || handle-conflict`
  - `git push origin main`
- `handle-conflict`: log to `/mnt/openclaw-workspace/logs/wiki-conflicts.log`, leave rebase paused, `git rebase --abort`, send an alert via the existing Telegram notify channel.

### 2.4 Smoke probe

- Extend `scripts/smoke-prod.sh` with a probe:
  - `openclaw wiki status --json` → assert `obsidian.enabled == true`, `vaultMode == "bridge"`, `health == "ok"`.
- Run a full deploy via `scripts/deploy.sh` and verify the new probe passes.

---

## Phase 3 — File-ingest pipeline (container)

**Outcome:** Sending a file via Telegram results in (a) original in `openclaw-attachments` blob, (b) `sources/<slug>.md` in the vault with a working `attach.posthub.cc` link, (c) the vault commit pushed to GitHub within 15 minutes.

### 3.1 Locate the existing file-handling code path

- Find where the runtime currently handles incoming Telegram attachments (probably in the `channels/telegram` plugin or the `photo-inbox` plugin, both of which we touched in prior sessions).
- Decide between Option A (extend that handler) vs. Option B (workspace watcher).
- If Option A is feasible without forking OpenClaw core, use it. Otherwise B.

### 3.2 Implement the attachments uploader

- Module: `scripts/attachments-uploader/` in `openclaw-deploy`, or a thin plugin if Option A.
- Inputs: file bytes + metadata (`mime`, `origin`, `uploadedBy`, `uploadedAt`).
- Steps:
  1. `sha256 = hash(bytes)`
  2. If blob `openclaw-attachments/<sha>/<name>` exists → skip upload (idempotent).
  3. Else `az storage blob upload` (or SDK call).
  4. Optionally extract text:
     - PDF: `pdftotext`
     - Image: existing OCR path (Gemini vision; already wired for photo-inbox)
     - Audio: existing transcription (Whisper-flavor; already wired)
     - Plain text/markdown: pass through
  5. Build frontmatter (see design §7.3 payload shape).
  6. `openclaw wiki ingest --stdin` with the frontmatter + body.

### 3.3 Wire the trigger

- Option A: hook into the runtime's "non-photo file received" code path. Photos continue to use the existing `photo-inbox` pipeline (don't merge those yet — separate concern).
- Option B (fallback): cron `*/2 * * * *` watches `/mnt/openclaw-workspace/file-inbox/incoming/` and processes new entries.

### 3.4 Post-ingest push

- After successful `wiki ingest`, call `wiki-push.sh` synchronously (it's idempotent and lock-protected). This makes new sources land in GitHub within seconds, not 15 min.

### 3.5 Smoke probe

- Extend smoke: upload a tiny known PDF via the test harness, assert (a) blob exists, (b) `sources/<slug>.md` appears in vault, (c) `git ls-files | grep <slug>` finds it.

---

## Phase 4 — rclone backup to Google Drive

**Outcome:** Azure Blob `openclaw-attachments` is mirrored to Google Drive hourly.

### 4.1 Add rclone to the container image

- Edit `Dockerfile`: add `rclone` install (static binary download; `apk add rclone` if Alpine has it in this version).
- Verify image size impact is minor (~30 MB).

### 4.2 Google service account setup

- Create a GCP project (or reuse existing) with a service account.
- Enable Drive API.
- Create a shared drive `openclaw-backup` (or a regular drive folder) and share it with the SA email.
- Download SA JSON key. Add to `env.sh` as `GDRIVE_SA_JSON` (base64-encoded; the container decodes at boot).

### 4.3 rclone config

- `scripts/rclone-setup.sh` builds `~/.config/rclone/rclone.conf` on container boot from env vars.
- Backends:
  - `azureblob:` — connection string from existing storage account vars
  - `gdrive:` — service account auth

### 4.4 Cron

- `0 * * * * /usr/local/bin/rclone-backup.sh`
- Script: `rclone sync azureblob:openclaw-attachments gdrive:openclaw-backup/attachments --fast-list --transfers=4 --tpslimit=10 2>>/mnt/openclaw-workspace/logs/rclone.log`
- Adds a metrics line: count of files synced, total bytes.

### 4.5 Smoke probe

- Probe: `rclone ls gdrive:openclaw-backup/attachments --max-depth 2 | wc -l` returns >0 after first hourly run.

---

## Phase 5 — Mac + iPhone clients

**Outcome:** Vault opens on Mac and iPhone in Obsidian. Container writes flow to both devices within ~5–10 min.

### 5.1 Mac one-time setup

- Install [Obsidian](https://obsidian.md) (free).
- Clone vault with separate git dir:
  ```sh
  git clone --separate-git-dir ~/.local/share/openclaw-vault.git \
    git@github.com:honoyr/openclaw-vault.git \
    "$HOME/Library/Mobile Documents/com~apple~CloudDocs/openclaw-vault"
  ```
- In Finder, right-click the `openclaw-vault` folder → **"Keep Downloaded"** (prevents iCloud eviction; critical).
- Open Obsidian → "Open folder as vault" → select the folder.

### 5.2 Mac launchd agents

- `~/Library/LaunchAgents/com.user.openclaw-vault-pull.plist` — `StartInterval: 300`, runs `git -C <vault> pull --rebase --autostash`.
- `~/Library/LaunchAgents/com.user.openclaw-vault-push.plist` — `WatchPaths: [<vault>]`, debounce 30 s, runs commit + push.
- Load both with `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.user.openclaw-vault-{pull,push}.plist`.

### 5.3 iPhone one-time setup

- Open Files app → enable iCloud Drive → confirm `openclaw-vault` folder is visible.
- Install Obsidian iOS from App Store (free).
- Open Obsidian → "Open vault from iCloud" → select `openclaw-vault`.
- Install iSH from App Store (free).
- In iSH:
  - `apk add git openssh`
  - `ssh-keygen -t ed25519 -C "iphone-ish"`
  - Add public key to GitHub account
  - Test: `git -C /iCloud/openclaw-vault pull` (path depends on how iSH mounts iCloud; document the exact mount setup in the task notes)
- Create two iSH aliases in `~/.profile`: `wpull` and `wpush` for one-command push/pull.

### 5.4 Document the user-facing workflow

- Add `docs/guides/obsidian-vault-user-guide.md` (will be created in `ai-workflows`).
- Sections: "How to revisit a topic", "Where files I sent are stored", "How to add a note from iPhone", "What to do if there's a sync conflict".

---

## Phase 6 — Verification + lock-in checks

**Outcome:** End-to-end test on real flows; portability claim is proven, not just designed.

### 6.1 End-to-end test

- Send a PDF via Telegram → confirm:
  - Blob exists in `openclaw-attachments`
  - `sources/*.md` appears in vault
  - File visible in Mac Obsidian within 5 min
  - File visible in iPhone Obsidian within 10 min (iCloud)
  - Clicking `attachment` link in Mac Obsidian → PDF opens in browser

### 6.2 Portability dry-run

- Manually copy a single attachment to a test GCS bucket.
- In Worker KV, add a `gcs_test` backend pointing at it.
- Hit `attach.posthub.cc/<that-sha>?backend=gcs_test` → confirm 302 to GCS-signed URL.
- Revert.

### 6.3 Conflict-handling test

- On Mac, edit `concepts/foo.md`. On container, run `openclaw wiki apply metadata concepts/foo …` simultaneously.
- Confirm: rebase succeeds, both edits preserved, or `wiki-conflicts.log` records a clean abort with Telegram alert.

### 6.4 Documentation pass

- Move `docs/wip/obsidian-wiki-integration/` to `docs/features/obsidian-wiki-integration/` (post-implementation home).
- Update `openclaw-local-mac-pro-feasibility.md` Appendix A with a pointer to this feature.
- Update `CLAUDE.md` if any new conventions emerged.

### 6.5 SOLID review + final smoke

- Run `solid-code-review` skill on the Worker + container scripts.
- Run full `scripts/smoke-prod.sh`; confirm all probes pass including the two new ones (wiki status + post-ingest source page existence).
