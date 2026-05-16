# Implementation Plan: `openclaw-vps`

> Status: **draft**
> Companion docs: [`rfc.md`](./rfc.md) · [`design.md`](./design.md) · [`tasks.md`](./tasks.md)
> Audience: experienced DevOps/Linux engineer, no project context.

This document describes **how** to build what's specified in [`design.md`](./design.md). It maps 1:1 to atomic, self-contained commits and is the source for [`tasks.md`](./tasks.md).

The implementation is structured so each phase ends in a usable state and you can pause/resume at any phase boundary.

---

## Phase 0 — Prerequisites & repo bootstrap

**Goal:** new GitHub repo, local toolchain, Azure prerequisites verified.

### 0.1 New repo

- Create `github.com/honoyr/openclaw-vps` (private, MIT license to match `openclaw-deploy`).
- Initialise with `.gitignore` (Ansible, Bicep, shell standards), `README.md` (placeholder pointing to this design folder), MIT `LICENSE`.
- Add a top-level `Makefile` with targets: `lint`, `provision`, `deploy`, `smoke`, `snapshot`, `rollback` — each delegates to `scripts/*.sh`. The makefile is the human entrypoint; CI calls scripts directly.

### 0.2 Local toolchain

Document required tools in `README.md`:

- `az` ≥ 2.60
- `bicep` ≥ 0.27 (bundled with az)
- `ansible-core` ≥ 2.16, `ansible-lint`, `python3-passlib`
- `shellcheck`, `jq`, `restic` (for ad-hoc local restores)

Provide a one-shot `scripts/install-toolchain.sh` for macOS (`brew install …`) — optional but recommended.

### 0.3 Azure prerequisites

In a comment in `bicep/main.bicep`, document the **manual** prereqs (these are not in scope for IaC because they predate the repo):

- Resource group `openclaw-rg` exists in `eastus`.
- Storage account `openclawstorage` with file share `openclaw-workspace` exists.
- Key Vault `openclaw-kv` exists with the existing `openclaw-deploy` secrets.
- A federated GitHub OIDC service principal exists for `repo:honoyr/openclaw-vps:ref:refs/heads/main` (separate from the one used by `openclaw-deploy`).

If any are missing, point to `openclaw-deploy/bicep/` (post-Topic 8b) for the canonical creation Bicep — do not duplicate.

---

## Phase 1 — Bicep (Azure resources)

**Goal:** `bicep build` clean; `az deployment group what-if` shows the planned VM + NSG + snapshot policy + KV access.

### 1.1 `bicep/main.bicep`

- Single entry point. Parameters: `location`, `vmName`, `adminUsername`, `sshPublicKey` (sourced from KV via `getSecret()`), `allowedSshCidrs[]`, `keyVaultName`.
- Composes the modules below in order.
- Outputs: `vmPublicIp`, `vmFqdn`, `managedIdentityPrincipalId` (so the post-deploy script can grant KV access if not already granted).

### 1.2 `bicep/modules/networking.bicep`

- Public IP (Standard SKU, static).
- NSG with one inbound rule: `allow tcp/22 from allowedSshCidrs[]`. Default deny.
- VNet + single subnet (we do not need vnet integration for anything in v1).
- NIC binding all of the above.

### 1.3 `bicep/modules/vm.bicep`

- `Microsoft.Compute/virtualMachines` `Standard_B2s`, image `Canonical:0001-com-ubuntu-server-noble:24_04-lts:latest`.
- OS disk: Premium SSD (P10, 30 GB).
- System-assigned managed identity enabled.
- SSH-key-only auth; password auth disabled.
- `customData` = base64 of a minimal cloud-init that installs `ansible-pull` prerequisites only (`python3`, `curl`). All real config happens via Ansible from the developer laptop or CI.

### 1.4 `bicep/modules/snapshot-policy.bicep`

- Recovery Services Vault `openclaw-rsv`.
- Backup policy: daily 03:30 UTC, 14-day retention.
- Protect the VM via `Microsoft.RecoveryServices/vaults/backupFabrics/protectionContainers/protectedItems`.

### 1.5 `bicep/modules/key-vault-access.bicep`

- `Microsoft.KeyVault/vaults/accessPolicies` add: VM managed identity → `get`, `list` on secrets.
- New secrets to **add manually** to KV (document in `docs/runbook.md` "First-time setup"):
  - `openclaw-vm-gateway-token`
  - `openclaw-vm-telegram-bot-token`
  - `openclaw-vm-cf-tunnel-token`
  - `openclaw-vm-restic-password`
  - `openclaw-vm-smb-username` (likely shared with ACI)
  - `openclaw-vm-smb-password` (likely shared with ACI)

### 1.6 `bicep/parameters.prod.json`

- Concrete values for prod. CIDR allowlist defaults to `0.0.0.0/0` with a comment "tighten before public release". Real prod value committed via ansible-vault or runtime override.

### 1.7 `scripts/provision.sh`

- One-shot: `az deployment group create --resource-group openclaw-rg --template-file bicep/main.bicep --parameters @bicep/parameters.prod.json`.
- After deployment succeeds, prints the public IP / FQDN and instructs the operator to run `ansible-playbook` next.
- Idempotent if re-run (Bicep handles this).

**Verify Phase 1:** `bicep build bicep/main.bicep` exits 0; `scripts/provision.sh --what-if` shows expected resources.

---

## Phase 2 — Ansible (in-VM configuration)

**Goal:** `ansible-playbook site.yml` against a freshly-provisioned VM produces a running `openclaw.service` reachable via Cloudflare tunnel.

### 2.1 Skeleton

- `ansible/ansible.cfg` — set `inventory = inventory.ini`, `roles_path = roles`, `vault_password_file = ./vault-password-file`.
- `ansible/inventory.ini` — single host `prod` with `ansible_host = <vm public ip>`, `ansible_user = openclaw_admin`, `ansible_ssh_private_key_file = ~/.ssh/openclaw_vm_ed25519`.
- `ansible/site.yml` — applies all roles in dependency order to the `prod` host.

### 2.2 Roles

Implement in this order; each is a self-contained commit. Within each role, follow the standard layout (`tasks/main.yml`, `handlers/main.yml`, `templates/`, `files/`, `defaults/main.yml`).

#### 2.2.1 `base`
- apt update + install: `curl jq restic ufw fail2ban unattended-upgrades`.
- Configure `/etc/ufw` to allow ssh only; enable.
- Configure `unattended-upgrades` for security updates only (no openclaw-related package upgrades — those are managed by us).
- Add 2 GB swapfile via `mount` + `fstab`.
- Configure `fail2ban` for sshd jail.

#### 2.2.2 `openclaw_user`
- Create `openclaw` system user (no login shell from network — but allow `sudo systemctl` via narrow sudoers entry).
- Create `~/.openclaw/`, `~/openclaw-workspace/` (mountpoint), `/etc/openclaw/` with correct ownership/modes.

#### 2.2.3 `nodejs`
- Add NodeSource APT repo for Node 22 LTS.
- Install `nodejs` package.
- Verify `node --version` and `npm --version` via `command` module + `assert`.

#### 2.2.4 `openclaw`
- Install `azure-cli` (needed by the secret-fetch script).
- Render `/etc/openclaw/openclaw.json` from `templates/openclaw.json.j2` (copied from `openclaw-deploy/config/config.json` and Jinja2-ised).
- Install `files/openclaw-fetch-secrets.sh` to `/usr/local/sbin/`. The script does `az login --identity` then `az keyvault secret show --vault-name {{ key_vault_name }} --name <each>` and writes `KEY=value` lines to `/etc/openclaw/env` (mode 0600, owner openclaw).
- Install `templates/openclaw-fetch-secrets.service.j2` (oneshot, before openclaw.service).
- Install `templates/openclaw.service.j2` (`Type=simple`, `User=openclaw`, `EnvironmentFile=/etc/openclaw/env`, `ExecStart=/usr/bin/openclaw start --config /etc/openclaw/openclaw.json`, `Restart=on-failure`, `SyslogIdentifier=openclaw-vm`, `RestartSec=5`).
- `npm install -g @openclaw/openclaw@{{ openclaw_version }}` plus `openclaw_plugins` list (loop with the `community.general.npm` module, `global: yes`).
- `systemctl daemon-reload` + `systemctl enable --now openclaw-fetch-secrets.service openclaw.service`.

#### 2.2.5 `cloudflared`
- Add Cloudflare APT repo + key.
- `apt install cloudflared`.
- Render `/etc/cloudflared/config.yml` (tunnel ID + ingress route to `localhost:18789`).
- The token is fetched from KV the same way as openclaw secrets — extend `openclaw-fetch-secrets.sh` to also write `/etc/cloudflared/token`.
- Install `cloudflared.service` (depends on `openclaw-fetch-secrets.service`).

#### 2.2.6 `azure_files_mount`
- Write `/etc/openclaw/smb-credentials` (mode 0600, owner root) with `username=`, `password=` from Key Vault.
- Add `/etc/fstab` line: `//openclawstorage.file.core.windows.net/openclaw-workspace /home/openclaw/openclaw-workspace cifs nofail,credentials=/etc/openclaw/smb-credentials,dir_mode=0770,file_mode=0660,uid=openclaw,gid=openclaw,serverino,nosharesock,actimeo=30,noexec,nosuid 0 0`.
- `mount -a`.
- **Critical:** verify `~/.openclaw/` is NOT mounted from SMB — only `~/openclaw-workspace/`.

#### 2.2.7 `cron_photo_inbox` (conditional on `photo_cron_enabled`)
- Port `scripts/install-photo-cron.sh` from `openclaw-deploy` to an Ansible task list.
- Install `openclaw-photo-cron.service` (oneshot) + `.timer` (every 10 min).
- Skip entirely if `photo_cron_enabled: false`.

#### 2.2.8 `backup`
- `restic init` against `azure:openclaw-backups:/vm-prod/` if not initialised (use `RESTIC_PASSWORD` from KV).
- Install `openclaw-backup.service` (`ExecStart=/usr/local/sbin/openclaw-backup.sh` running `restic backup ~/.openclaw/ /etc/openclaw/`).
- Install `openclaw-backup.timer` (daily 03:00 UTC).
- Test backup can be invoked manually with `--dry-run`.

#### 2.2.9 `health`
- Install `/usr/local/sbin/openclaw-healthcheck.sh` — curls `http://127.0.0.1:18789/api/v1/health`, prints structured `status=ok latency_ms=N`, exits non-zero on failure.
- Install `openclaw-healthcheck.timer` (every 5 min).
- Configure `openclaw.service` `Restart=on-failure` + `StartLimitBurst=3 StartLimitIntervalSec=900` so 3 fails in 15 min stops the unit and surfaces to journal.

### 2.3 group_vars / host_vars

`ansible/group_vars/all.yml`:

```yaml
openclaw_version: "5.6"
openclaw_plugins:
  - "@openclaw/brave-plugin"
  - "@openclaw/memory-lancedb-plugin"
photo_cron_enabled: true
key_vault_name: "openclaw-kv"
storage_account: "openclawstorage"
workspace_share: "openclaw-workspace"
cloudflare_tunnel_hostname: "openclaw-vm.posthub.cc"
openclaw_env: "vm"
```

`ansible/host_vars/prod.openclaw.vault.yml` (ansible-vault):

```yaml
vault_restic_repo: "azure:openclaw-backups:/vm-prod/"
```

(All other secrets stay in KV.)

### 2.4 `scripts/upgrade.sh`

- Take a pre-upgrade VM snapshot via `scripts/snapshot.sh`.
- `git pull` to ensure latest playbook.
- `ansible-playbook ansible/site.yml --tags openclaw` (just the openclaw role).
- Wait for `openclaw.service` active.
- Run `scripts/smoke-prod.sh`. If fail, exit 1 with instructions for `scripts/rollback.sh`.

### 2.5 `scripts/rollback.sh`

- `--version <ver>` flag: `ansible-playbook ansible/site.yml --tags openclaw --extra-vars "openclaw_version=<ver>"`.
- `--restic` flag: stop openclaw, restore latest restic snapshot to `~/.openclaw/`, start openclaw.
- `--snapshot` flag: prints instructions for full VM restore (manual step via `restore-vm.sh`).

**Verify Phase 2:** `ansible-playbook site.yml` against fresh VM exits 0. Second run reports `changed=0`. `journalctl -u openclaw -n 50` shows `gateway ready`.

---

## Phase 3 — CI/CD (GitHub Actions + OIDC)

**Goal:** push to `main` triggers `bicep what-if` → `ansible --check` → on success, `ansible-playbook` apply → `smoke-prod`.

### 3.1 OIDC trust

- Document (in `docs/runbook.md`) the Azure AD app registration + federated credential setup for the new repo. (The az CLI commands are well-known; do not duplicate Azure docs in our repo.)

### 3.2 `.github/workflows/lint.yml`
- Triggers: PR, push.
- Jobs: `ansible-lint`, `bicep build bicep/main.bicep`, `shellcheck scripts/*.sh test/*.sh`.

### 3.3 `.github/workflows/deploy.yml`
- Triggers: push to `main`, manual `workflow_dispatch`.
- Steps:
  1. `azure/login` action with OIDC.
  2. `bicep what-if` against `openclaw-rg` — fail if any `Delete` actions on existing resources.
  3. Install `ansible-core`, `ansible-lint`.
  4. Decrypt `vault-password-file` from Actions secret.
  5. `ansible-playbook site.yml --check --diff` — comment diff on the commit.
  6. If push to `main`: `ansible-playbook site.yml`.
  7. `scripts/smoke-prod.sh` — if fail, run `scripts/rollback.sh --version <previous>` (read from `.last-deploy` file in repo, similar to `openclaw-deploy/.last-build`).

### 3.4 `.github/workflows/nightly-snapshot.yml`
- `schedule: cron: '17 3 * * *'` (slightly after the in-VM restic backup so we capture post-backup state).
- `azure/login` OIDC.
- `az snapshot create` from the VM's OS disk; tag `nightly=true`.
- Prune snapshots older than 14 days.

**Verify Phase 3:** open a no-op PR; lint workflow runs green; merge; deploy workflow runs green; tunnel still serves traffic.

---

## Phase 4 — Smoke harness & operator scripts

**Goal:** `scripts/smoke-prod.sh` exists and parity-tests against `openclaw-deploy/scripts/smoke-prod.sh`.

### 4.1 Port smoke harness
- Copy `openclaw-deploy/scripts/smoke-prod.sh` and `scripts/lib/` verbatim.
- Replace ACI-specific probes with VM-equivalent (e.g. `az container exec` → `ssh openclaw_admin@vm "openclaw …"`).
- Add `--env vm` flag for any output disambiguation.

### 4.2 iOS Shortcuts generator
- Copy `openclaw-deploy/scripts/gen-ios-shortcuts.py`.
- Add `--env {vm,aci}` flag controlling tunnel hostname + bot token used in generated shortcuts.
- Output filenames: `OpenClaw-VM-<action>.shortcut` to keep them visually distinct from the existing ACI pack.

### 4.3 Snapshot + restore
- `scripts/snapshot.sh` — `az snapshot create` with `--tags reason=<arg>`.
- `scripts/restore-vm.sh` — interactive: lists snapshots, prompts for one, builds replacement disk, swaps OS disk, restarts VM. **Manual** because data loss potential is high.

**Verify Phase 4:** `scripts/smoke-prod.sh` against the live VM passes all probes.

---

## Phase 5 — Documentation & operator handoff

### 5.1 `README.md` (top-level)
- Quickstart (clone → install toolchain → `make provision` → `make deploy`).
- Link to `docs/runbook.md`.

### 5.2 `docs/runbook.md`
- Day-to-day ops: how to upgrade, how to add a plugin, how to rotate the gateway token, how to read logs, how to restart, how to take an ad-hoc snapshot.

### 5.3 `docs/migration-from-aci.md`
- Step-by-step for the operator. References the ACI repo's runbook for "what to leave running there."
- Includes the **one-line PR to `openclaw-deploy`** to set `PHOTO_CRON_ENABLED=false` (or equivalent).

### 5.4 `docs/disaster-recovery.md`
- The four scenarios from `design.md §5.5`, each with a copy-pasteable recovery sequence.

### 5.5 `docs/upgrade-procedure.md`
- The 6-step upgrade flow from `design.md §3.7`.

### 5.6 `docs/dual-environment-ops.md`
- Operational rules from RFC §10: which env runs cron, who's canary, version drift policy, log-tag conventions.

### 5.7 Companion update in `openclaw-deploy`
- Tracked as a separate PR in the other repo: disable photo-inbox cron, add link from its README to the new `openclaw-vps` repo, document "ACI is now the stable channel."

---

## Phase 6 — Cutover & soak

### 6.1 First boot
- `scripts/provision.sh` to create VM.
- `ansible-playbook site.yml` to configure.
- Manually pair Mac + iPhone against the new tunnel hostname + new bot.

### 6.2 Soak
- 7 days dual-running. ACI continues to serve `openclaw.posthub.cc` + bot A. VM serves `openclaw-vm.posthub.cc` + bot B.
- Daily: review `journalctl -u openclaw -p err`, verify nightly snapshot + restic backup ran.
- Day 3: bump `openclaw_version` on VM as canary test.

### 6.3 Pin photo-cron
- Land the companion PR in `openclaw-deploy` to disable photo-cron there.
- Verify VM's photo-cron is processing new uploads end-to-end.

### 6.4 Steady state decision
- At 4–8 weeks: revisit whether to retire ACI or keep dual-env permanently. Document outcome in `docs/dual-environment-ops.md`.

---

## File-touch summary (for atomic-commit hygiene)

Each numbered task in [`tasks.md`](./tasks.md) corresponds to roughly one of these scope groups; a developer following the task list can commit at every task boundary without leaving the repo in a broken state.

| Scope group | Files touched |
|---|---|
| Repo init | `README.md`, `.gitignore`, `LICENSE`, `Makefile`, `.github/workflows/lint.yml` |
| Bicep | `bicep/**`, `scripts/provision.sh` |
| Ansible skeleton | `ansible/ansible.cfg`, `ansible/site.yml`, `ansible/inventory.ini`, `ansible/group_vars/all.yml` |
| Each Ansible role | `ansible/roles/<role>/**` (one commit per role) |
| Config template | `ansible/roles/openclaw/templates/openclaw.json.j2`, `config/openclaw.json.j2` |
| Deploy CI | `.github/workflows/deploy.yml`, `.github/workflows/nightly-snapshot.yml` |
| Smoke harness | `scripts/smoke-prod.sh`, `scripts/lib/**`, `test/**` |
| Operator scripts | `scripts/upgrade.sh`, `scripts/rollback.sh`, `scripts/snapshot.sh`, `scripts/restore-vm.sh` |
| iOS shortcuts | `scripts/gen-ios-shortcuts.py` |
| Docs | `docs/**` |
| Companion (ACI) | `openclaw-deploy` PR — separate repo |
