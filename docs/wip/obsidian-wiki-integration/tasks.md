# Tasks: Obsidian Wiki Integration for OpenClaw

> Design: [./design.md](./design.md)
> Implementation: [./implementation.md](./implementation.md)
> Status: pending
> Created: 2026-05-13

## Task 1: Create private vault repo + scaffolding
- **Status:** pending
- **Depends on:** —
- **Docs:** [implementation.md#11-create-the-github-vault-repo](./implementation.md#11-create-the-github-vault-repo)

### Subtasks
- [ ] 1.1 Create `github.com/honoyr/openclaw-vault` (private), add `README.md` linking back to this design
- [ ] 1.2 Add `.gitignore` (`_attachments/`, `.openclaw-wiki/cache/*.json`, `.obsidian/workspace*.json`, `.DS_Store`)
- [ ] 1.3 Add `.gitattributes` with `reports/* merge=ours` and `*.md text eol=lf`
- [ ] 1.4 Mint a fine-grained PAT scoped to `openclaw-vault` only with `contents: read+write`; save to local secrets vault (do not commit)

## Task 2: Cloudflare Worker for portable attachment URLs
- **Status:** pending
- **Depends on:** —
- **Docs:** [implementation.md#12-stand-up-the-cloudflare-worker](./implementation.md#12-stand-up-the-cloudflare-worker)

### Subtasks
- [ ] 2.1 Create `honoyr/openclaw-deploy/worker/` (or new `honoyr/openclaw-attach-worker` repo) with the Worker source — `GET /<sha256>` resolves backend from KV, mints 10-min Azure SAS, returns 302
- [ ] 2.2 Bind route `attach.posthub.cc/*` to the Worker in Cloudflare dashboard
- [ ] 2.3 Add Worker secrets: `AZURE_STORAGE_ACCOUNT`, `AZURE_STORAGE_KEY`, `AZURE_CONTAINER=openclaw-attachments`
- [ ] 2.4 Create KV namespace `BACKEND_CONFIG` with key `default_backend = "azure"` and full backend config struct (forward-looking for GCS/S3)
- [ ] 2.5 Manual test: upload known blob via `az storage blob upload`, `curl -I https://attach.posthub.cc/<sha>` returns 302, follow → downloads file

## Task 3: Provision Azure Blob attachments container
- **Status:** pending
- **Depends on:** —
- **Docs:** [implementation.md#13-create-the-azure-blob-container](./implementation.md#13-create-the-azure-blob-container)

### Subtasks
- [ ] 3.1 `az storage container create --name openclaw-attachments --account-name openclawstoragedgonor` with no public access, Hot tier
- [ ] 3.2 Verify CORS rules on the storage account permit Worker-signed reads (no CORS needed for server-side 302, but document)

## Task 4: Container — switch memory-wiki to Obsidian render mode
- **Status:** pending
- **Depends on:** Task 1
- **Docs:** [implementation.md#21-switch-render-mode](./implementation.md#21-switch-render-mode)

### Subtasks
- [ ] 4.1 Edit `~/repos/openclaw-deploy/config/config.json`: under `plugins.entries.memory-wiki.config`, change `vault.renderMode` to `"obsidian"` and add `obsidian` block (`enabled: true, useOfficialCli: false, vaultName: "OpenClaw Vault", openAfterWrites: false`)
- [ ] 4.2 Run `scripts/deploy.sh`; verify `openclaw wiki status --json` reports `obsidian.enabled == true`
- [ ] 4.3 Verify existing vault pages still render (no data loss)

## Task 5: Container — vault git wiring
- **Status:** pending
- **Depends on:** Task 1, Task 4
- **Docs:** [implementation.md#22-initialize-the-vault-as-a-git-repo-on-azure-files](./implementation.md#22-initialize-the-vault-as-a-git-repo-on-azure-files)

### Subtasks
- [ ] 5.1 Create `scripts/wiki-git-init.sh` — idempotent `git init --separate-git-dir`, add remote, initial pull-or-push
- [ ] 5.2 Add `OPENCLAW_VAULT_GIT_TOKEN` env var slot to `scripts/env.sh` (user supplies value locally; do not commit)
- [ ] 5.3 Wire `wiki-git-init.sh` into the deploy flow so it runs once per deploy
- [ ] 5.4 Create `scripts/wiki-push.sh` — `flock`-protected commit + rebase + push; logs conflicts and alerts via existing Telegram notify channel
- [ ] 5.5 Add cron entry `*/15 * * * * /usr/local/bin/wiki-push.sh` to the container's cron
- [ ] 5.6 Verify after deploy: container can `git push` (check GitHub commits appear)

## Task 6: Container — file-ingest pipeline
- **Status:** pending
- **Depends on:** Task 2, Task 3, Task 5
- **Docs:** [implementation.md#phase-3--file-ingest-pipeline-container](./implementation.md#phase-3--file-ingest-pipeline-container)

### Subtasks
- [ ] 6.1 Locate the runtime code path handling non-photo Telegram file attachments; decide Option A (extend runtime) vs. Option B (workspace watcher)
- [ ] 6.2 Implement `scripts/attachments-uploader/upload.sh` (or equivalent module): sha256 → blob upload (idempotent) → text extraction (PDF/OCR/audio/text) → frontmatter build → pipe to `openclaw wiki ingest --stdin`
- [ ] 6.3 Wire the trigger from the runtime/watcher to the uploader
- [ ] 6.4 After successful ingest, call `wiki-push.sh` synchronously so the source page reaches GitHub in seconds
- [ ] 6.5 Smoke probe in `scripts/smoke-prod.sh`: upload a test PDF, assert blob exists, `sources/*.md` exists in vault, `git ls-files` includes the new file

## Task 7: rclone backup to Google Drive
- **Status:** pending
- **Depends on:** Task 3
- **Docs:** [implementation.md#phase-4--rclone-backup-to-google-drive](./implementation.md#phase-4--rclone-backup-to-google-drive)

### Subtasks
- [ ] 7.1 Add `rclone` install to container `Dockerfile`
- [ ] 7.2 Provision GCP service account + shared `openclaw-backup` Drive folder; download SA JSON; base64-encode into `env.sh` as `GDRIVE_SA_JSON`
- [ ] 7.3 Create `scripts/rclone-setup.sh` — generates `~/.config/rclone/rclone.conf` on container boot from env vars
- [ ] 7.4 Create `scripts/rclone-backup.sh` — `rclone sync azureblob:openclaw-attachments gdrive:openclaw-backup/attachments` with logging + metrics line
- [ ] 7.5 Add cron `0 * * * * /usr/local/bin/rclone-backup.sh`
- [ ] 7.6 Smoke probe: `rclone ls gdrive:openclaw-backup/attachments` returns >0 files after first hourly run

## Task 8: Mac client setup
- **Status:** pending
- **Depends on:** Task 1, Task 5
- **Docs:** [implementation.md#51-mac-one-time-setup](./implementation.md#51-mac-one-time-setup)

### Subtasks
- [ ] 8.1 Install Obsidian Mac app (free)
- [ ] 8.2 Clone vault with `--separate-git-dir` into `~/Library/Mobile Documents/com~apple~CloudDocs/openclaw-vault`; `.git` at `~/.local/share/openclaw-vault.git`
- [ ] 8.3 In Finder, mark the vault folder "Keep Downloaded" to disable iCloud eviction
- [ ] 8.4 Open vault in Obsidian; verify pages render with backlinks
- [ ] 8.5 Create `~/Library/LaunchAgents/com.user.openclaw-vault-pull.plist` (`StartInterval: 300`, `git pull --rebase --autostash`); load with `launchctl bootstrap`
- [ ] 8.6 Create `~/Library/LaunchAgents/com.user.openclaw-vault-push.plist` (`WatchPaths` on vault, debounced 30 s, commit + push); load with `launchctl bootstrap`
- [ ] 8.7 Verify: container pushes a test commit → appears on Mac within 5 min; Mac edits a file → reaches GitHub within 1 min

## Task 9: iPhone client setup
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#53-iphone-one-time-setup](./implementation.md#53-iphone-one-time-setup)

### Subtasks
- [ ] 9.1 On iPhone: open Files app, confirm `openclaw-vault` is visible under iCloud Drive
- [ ] 9.2 Install Obsidian iOS (free); open vault from iCloud
- [ ] 9.3 Install iSH from App Store; `apk add git openssh`; generate SSH key; add public key to GitHub user
- [ ] 9.4 Document the exact iSH mount path for the iCloud `openclaw-vault` folder (varies by iOS version); store in user guide
- [ ] 9.5 Add `wpull` and `wpush` aliases in iSH `~/.profile`
- [ ] 9.6 Verify: container push appears on iPhone Obsidian within 10 min; iPhone edit → `wpush` in iSH → reaches GitHub

## Task 10: User guide
- **Status:** pending
- **Depends on:** Task 8, Task 9
- **Docs:** [implementation.md#54-document-the-user-facing-workflow](./implementation.md#54-document-the-user-facing-workflow)

### Subtasks
- [ ] 10.1 Create `~/repos/ai-workflows/docs/guides/obsidian-vault-user-guide.md`
- [ ] 10.2 Sections: "How to revisit a topic", "Where files I sent are stored", "How to add a note from iPhone", "Sync conflict recovery", "Provider migration"
- [ ] 10.3 Include screenshots of the Obsidian app showing a `sources/` page with a working `attach.posthub.cc` link

## Task 11: End-to-end + portability verification
- **Status:** pending
- **Depends on:** Task 6, Task 7, Task 9
- **Docs:** [implementation.md#phase-6--verification--lock-in-checks](./implementation.md#phase-6--verification--lock-in-checks)

### Subtasks
- [ ] 11.1 Send a PDF via Telegram → verify blob upload, `sources/*.md` creation, propagation to Mac (<5 min) and iPhone (<10 min), click-through download works
- [ ] 11.2 Portability dry-run: copy one attachment to a test GCS bucket, add `gcs_test` backend to Worker KV, verify `attach.posthub.cc/<sha>` resolves to GCS-signed URL; revert
- [ ] 11.3 Conflict-handling test: simultaneous Mac edit + container `wiki apply` on the same page; verify clean rebase or clean abort with Telegram alert
- [ ] 11.4 Run `scripts/smoke-prod.sh` end-to-end; confirm new probes (wiki status + post-ingest source existence) pass

## Task 12: Final verification (skill-driven)
- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11

### Subtasks
- [ ] 12.1 Run `testing-process` skill — full smoke + manual E2E flows in §11
- [ ] 12.2 Run `documentation-process` skill — promote `docs/wip/obsidian-wiki-integration/` to `docs/features/obsidian-wiki-integration/`, update Appendix A in `openclaw-local-mac-pro-feasibility.md`, update `CLAUDE.md` if conventions emerged
- [ ] 12.3 Run `solid-code-review` skill with `javascript` (Worker) + `bash` (container scripts) inputs to review changes
