# Tasks: OpenClaw VPS Migration (`openclaw-vps`)

> Design: [./design.md](./design.md)
> Implementation: [./implementation.md](./implementation.md)
> RFC: [./rfc.md](./rfc.md)
> Status: pending
> Created: 2026-05-08

## Task 1: Repo bootstrap
- **Status:** pending
- **Depends on:** —
- **Docs:** [implementation.md#phase-0](./implementation.md#phase-0--prerequisites--repo-bootstrap)

### Subtasks
- [ ] 1.1 Create `github.com/honoyr/openclaw-vps` (private, MIT). Initialise with README placeholder pointing to `ai-workflows/docs/wip/openclaw-vps-migration/`, `.gitignore` (Ansible+Bicep+shell), `LICENSE`.
- [ ] 1.2 Add top-level `Makefile` with targets `lint`, `provision`, `deploy`, `smoke`, `snapshot`, `rollback`, each delegating to `scripts/*.sh`.
- [ ] 1.3 Add `scripts/install-toolchain.sh` (macOS Homebrew one-shot for `ansible-core`, `ansible-lint`, `bicep`, `shellcheck`, `restic`, `jq`).
- [ ] 1.4 Add `.github/workflows/lint.yml` — runs `ansible-lint`, `bicep build`, `shellcheck` on PR + push.

## Task 2: Bicep — networking + VM
- **Status:** pending
- **Depends on:** Task 1
- **Docs:** [implementation.md#11-bicepmainbicep](./implementation.md#11-bicepmainbicep), [design.md#31-azure-resources-bicep](./design.md#31-azure-resources-bicep)

### Subtasks
- [ ] 2.1 Create `bicep/main.bicep` with parameters (`location`, `vmName`, `adminUsername`, `sshPublicKey`, `allowedSshCidrs[]`, `keyVaultName`) and outputs (`vmPublicIp`, `vmFqdn`, `managedIdentityPrincipalId`).
- [ ] 2.2 Create `bicep/modules/networking.bicep` — Standard Public IP, NSG (deny all inbound except tcp/22 from `allowedSshCidrs[]`), VNet+subnet, NIC.
- [ ] 2.3 Create `bicep/modules/vm.bicep` — `Standard_B2s`, Ubuntu 24.04 LTS image, Premium SSD 30 GB, system-assigned managed identity, ssh-key-only auth, minimal cloud-init in `customData` (python3 + curl).
- [ ] 2.4 Create `bicep/parameters.prod.json` with concrete prod values; CIDR allowlist clearly marked TODO before public exposure.
- [ ] 2.5 Verify `bicep build bicep/main.bicep` exits 0 in CI.

## Task 3: Bicep — backup + KV access
- **Status:** pending
- **Depends on:** Task 2
- **Docs:** [implementation.md#14-bicepmodulessnapshot-policybicep](./implementation.md#14-bicepmodulessnapshot-policybicep)

### Subtasks
- [ ] 3.1 Create `bicep/modules/snapshot-policy.bicep` — Recovery Services Vault `openclaw-rsv`, daily 03:30 UTC backup policy (14-day retention), VM protection registration.
- [ ] 3.2 Create `bicep/modules/key-vault-access.bicep` — access policy granting VM managed identity `get`/`list` on secrets.
- [ ] 3.3 Document in `docs/runbook.md` "First-time KV setup": six new secrets to create manually (`openclaw-vm-gateway-token`, `…-telegram-bot-token`, `…-cf-tunnel-token`, `…-restic-password`, `…-smb-username`, `…-smb-password`).
- [ ] 3.4 Create `scripts/provision.sh` — wraps `az deployment group create` + prints next-step instructions; idempotent.

## Task 4: Ansible skeleton
- **Status:** pending
- **Depends on:** Task 1
- **Docs:** [implementation.md#phase-2--ansible-in-vm-configuration](./implementation.md#phase-2--ansible-in-vm-configuration)

### Subtasks
- [ ] 4.1 Create `ansible/ansible.cfg`, `ansible/inventory.ini` (single host `prod`), empty `ansible/site.yml` that imports all roles in dependency order.
- [ ] 4.2 Create `ansible/group_vars/all.yml` with `openclaw_version`, `openclaw_plugins[]`, `photo_cron_enabled`, `key_vault_name`, `storage_account`, `workspace_share`, `cloudflare_tunnel_hostname`, `openclaw_env: vm`.
- [ ] 4.3 Create `ansible/host_vars/prod.openclaw.vault.yml` (ansible-vault encrypted) for `vault_restic_repo` and any non-KV secrets.
- [ ] 4.4 Document vault-password storage convention in `README.md` (file path gitignored; CI reads from Actions secret).

## Task 5: Ansible role — `base`
- **Status:** pending
- **Depends on:** Task 4
- **Docs:** [implementation.md#221-base](./implementation.md#221-base)

### Subtasks
- [ ] 5.1 `ansible/roles/base/tasks/main.yml` — apt update + install `curl jq restic ufw fail2ban unattended-upgrades`.
- [ ] 5.2 Configure ufw: deny incoming, allow ssh; enable.
- [ ] 5.3 Configure `unattended-upgrades` for security updates only (template the apt config file).
- [ ] 5.4 Provision 2 GB swapfile at `/swapfile` via `community.general.swapfile`-equivalent task list (fallocate + mkswap + swapon + fstab entry).
- [ ] 5.5 Configure fail2ban sshd jail.

## Task 6: Ansible role — `openclaw_user`
- **Status:** pending
- **Depends on:** Task 5
- **Docs:** [implementation.md#222-openclaw_user](./implementation.md#222-openclaw_user)

### Subtasks
- [ ] 6.1 Create `openclaw` system user (no shell login from network) and group.
- [ ] 6.2 Create `~/.openclaw/`, `~/openclaw-workspace/`, `/etc/openclaw/` with appropriate ownership/modes (0750/0750/0750, openclaw:openclaw).
- [ ] 6.3 Add narrow sudoers entry permitting `openclaw` to run `/bin/systemctl restart openclaw cloudflared` without password.

## Task 7: Ansible role — `nodejs`
- **Status:** pending
- **Depends on:** Task 6
- **Docs:** [implementation.md#223-nodejs](./implementation.md#223-nodejs)

### Subtasks
- [ ] 7.1 Add NodeSource APT repo + signing key for Node 22 LTS.
- [ ] 7.2 `apt install nodejs`.
- [ ] 7.3 Assert `node --version` matches `^22\.` and `npm --version` works.

## Task 8: Ansible role — `openclaw` (core)
- **Status:** pending
- **Depends on:** Task 7
- **Docs:** [implementation.md#224-openclaw](./implementation.md#224-openclaw), [design.md#34-config-rendering-openclawjsonj2](./design.md#34-config-rendering-openclawjsonj2)

### Subtasks
- [ ] 8.1 Install `azure-cli` (needed by secret-fetch script).
- [ ] 8.2 Port `openclaw-deploy/config/config.json` to `ansible/roles/openclaw/templates/openclaw.json.j2`; mirror copy at `config/openclaw.json.j2` for cross-repo diffing.
- [ ] 8.3 Render `/etc/openclaw/openclaw.json` (mode 0640, openclaw:openclaw).
- [ ] 8.4 Write `files/openclaw-fetch-secrets.sh` — `az login --identity` then `az keyvault secret show` for each secret name; produces `/etc/openclaw/env` (mode 0600).
- [ ] 8.5 Write `templates/openclaw-fetch-secrets.service.j2` (Type=oneshot, before openclaw.service).
- [ ] 8.6 Write `templates/openclaw.service.j2` — Type=simple, User=openclaw, EnvironmentFile=/etc/openclaw/env, ExecStart=`/usr/bin/openclaw start --config /etc/openclaw/openclaw.json`, Restart=on-failure, SyslogIdentifier=openclaw-vm, StartLimitBurst=3 StartLimitIntervalSec=900, RestartSec=5.
- [ ] 8.7 `npm install -g @openclaw/openclaw@{{ openclaw_version }}` plus loop over `openclaw_plugins`.
- [ ] 8.8 `daemon-reload` + `systemctl enable --now openclaw-fetch-secrets.service openclaw.service` (handlers).

## Task 9: Ansible role — `cloudflared`
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#225-cloudflared](./implementation.md#225-cloudflared)

### Subtasks
- [ ] 9.1 Add Cloudflare APT repo + key; install `cloudflared`.
- [ ] 9.2 Render `/etc/cloudflared/config.yml` with the new tunnel ID + ingress to `localhost:18789`.
- [ ] 9.3 Extend `openclaw-fetch-secrets.sh` to also write `/etc/cloudflared/token` (mode 0600).
- [ ] 9.4 Install `cloudflared.service` systemd unit (After=openclaw-fetch-secrets.service).

## Task 10: Ansible role — `azure_files_mount`
- **Status:** pending
- **Depends on:** Task 6
- **Docs:** [implementation.md#226-azure_files_mount](./implementation.md#226-azure_files_mount)

### Subtasks
- [ ] 10.1 Write `/etc/openclaw/smb-credentials` (mode 0600, root:root) populated from KV secrets.
- [ ] 10.2 Add `/etc/fstab` entry: `~/openclaw-workspace/` ONLY (cifs, nofail, nosharesock, actimeo=30, noexec, nosuid).
- [ ] 10.3 `mount -a`; verify mount succeeded; verify `~/.openclaw/` is on local disk and NOT on SMB (assert task).

## Task 11: Ansible role — `cron_photo_inbox`
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#227-cron_photo_inbox-conditional-on-photo_cron_enabled](./implementation.md#227-cron_photo_inbox-conditional-on-photo_cron_enabled)

### Subtasks
- [ ] 11.1 Port `openclaw-deploy/scripts/install-photo-cron.sh` content to a task list (the steps it runs, not the script verbatim).
- [ ] 11.2 Install `openclaw-photo-cron.service` (oneshot) + `.timer` (every 10 min).
- [ ] 11.3 Wrap entire role in `when: photo_cron_enabled` so it's skipped when disabled.

## Task 12: Ansible role — `backup`
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#228-backup](./implementation.md#228-backup), [design.md#36-backup--recovery-3-layers-defence-in-depth](./design.md#36-backup--recovery-3-layers-defence-in-depth)

### Subtasks
- [ ] 12.1 `restic init` against `azure:openclaw-backups:/vm-prod/` (idempotent: skip if already initialised).
- [ ] 12.2 Install `/usr/local/sbin/openclaw-backup.sh` running `restic backup ~/.openclaw/ /etc/openclaw/`.
- [ ] 12.3 Install `openclaw-backup.service` + `openclaw-backup.timer` (daily 03:00 UTC).
- [ ] 12.4 Verify a `--dry-run` backup invocation succeeds.

## Task 13: Ansible role — `health`
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#229-health](./implementation.md#229-health)

### Subtasks
- [ ] 13.1 Install `/usr/local/sbin/openclaw-healthcheck.sh` — curl `http://127.0.0.1:18789/api/v1/health`, structured log `status=ok|fail latency_ms=N`, non-zero on fail.
- [ ] 13.2 Install `openclaw-healthcheck.service` + `.timer` (every 5 min).
- [ ] 13.3 Confirm `openclaw.service` already configured with `StartLimitBurst=3 StartLimitIntervalSec=900` (Task 8.6) so 3 fails surfaces to journal.

## Task 14: Operator scripts (upgrade / rollback / snapshot)
- **Status:** pending
- **Depends on:** Task 8, Task 12
- **Docs:** [implementation.md#24-scriptsupgradesh](./implementation.md#24-scriptsupgradesh), [design.md#37-upgrade-procedure](./design.md#37-upgrade-procedure)

### Subtasks
- [ ] 14.1 `scripts/snapshot.sh` — `az snapshot create --tags reason=$1`; lists snapshots; prunes >14d.
- [ ] 14.2 `scripts/upgrade.sh` — pre-snapshot → git pull → `ansible-playbook --tags openclaw` → wait active → smoke → on fail print rollback hint.
- [ ] 14.3 `scripts/rollback.sh` — flags `--version <ver>` (npm pin via ansible), `--restic` (state restore), `--snapshot` (manual VM restore instructions).
- [ ] 14.4 `scripts/restore-vm.sh` — interactive snapshot picker → swap OS disk → restart VM. Marked manual / dangerous in script header.

## Task 15: Smoke harness + iOS Shortcuts
- **Status:** pending
- **Depends on:** Task 8
- **Docs:** [implementation.md#41-port-smoke-harness](./implementation.md#41-port-smoke-harness)

### Subtasks
- [ ] 15.1 Copy `openclaw-deploy/scripts/smoke-prod.sh` + `scripts/lib/` verbatim. Replace ACI-specific probes (`az container exec`) with `ssh openclaw_admin@vm "openclaw …"`. Add `--env vm` flag.
- [ ] 15.2 Copy `openclaw-deploy/scripts/gen-ios-shortcuts.py`. Add `--env {vm,aci}` flag controlling tunnel hostname + bot token. Output names prefixed `OpenClaw-VM-…`.
- [ ] 15.3 Add `test/test-ansible-syntax.sh`, `test/test-bicep-build.sh`, `test/test-systemd-units.sh`, `test/helpers.sh` (port from `openclaw-deploy/test/`).

## Task 16: CI/CD — deploy + nightly snapshot
- **Status:** pending
- **Depends on:** Task 8, Task 15
- **Docs:** [implementation.md#phase-3--cicd-github-actions--oidc](./implementation.md#phase-3--cicd-github-actions--oidc)

### Subtasks
- [ ] 16.1 Create new Azure AD app registration + federated credential for `repo:honoyr/openclaw-vps:ref:refs/heads/main`. Document az CLI commands in `docs/runbook.md` "First-time CI setup".
- [ ] 16.2 `.github/workflows/deploy.yml` — OIDC login → `bicep what-if` (fail on Delete) → `ansible-playbook --check` (PR comment) → on push to main: `ansible-playbook` + `smoke-prod.sh` + auto-rollback on fail using `.last-deploy` previous version.
- [ ] 16.3 `.github/workflows/nightly-snapshot.yml` — `cron: '17 3 * * *'`, OIDC, `az snapshot create`, prune >14d.
- [ ] 16.4 Verify all three workflows go green on a no-op PR.

## Task 17: Documentation
- **Status:** pending
- **Depends on:** Task 14, Task 15
- **Docs:** [implementation.md#phase-5--documentation--operator-handoff](./implementation.md#phase-5--documentation--operator-handoff)

### Subtasks
- [ ] 17.1 `README.md` (top-level) — quickstart, link to `docs/runbook.md`.
- [ ] 17.2 `docs/runbook.md` — day-to-day ops: upgrade, add plugin, rotate token, read logs, restart, ad-hoc snapshot, first-time KV/CI setup.
- [ ] 17.3 `docs/migration-from-aci.md` — step-by-step operator guide; references companion PR in `openclaw-deploy`.
- [ ] 17.4 `docs/disaster-recovery.md` — four scenarios from `design.md §5.5` with copy-pasteable sequences.
- [ ] 17.5 `docs/upgrade-procedure.md` — six-step flow from `design.md §3.7`.
- [ ] 17.6 `docs/dual-environment-ops.md` — operational rules from RFC §10 (cron pinning, canary, version drift, log tags).

## Task 18: Companion update in `openclaw-deploy`
- **Status:** pending
- **Depends on:** Task 11
- **Docs:** [implementation.md#57-companion-update-in-openclaw-deploy](./implementation.md#57-companion-update-in-openclaw-deploy)

### Subtasks
- [ ] 18.1 Open PR in `openclaw-deploy` to disable photo-inbox cron (set `PHOTO_CRON_ENABLED=false` in `scripts/env.sh.example` + comment in `deploy.sh`).
- [ ] 18.2 Update `openclaw-deploy/README.md` to point at new `openclaw-vps` repo and clarify "ACI is now the stable channel."
- [ ] 18.3 Merge after the VM has been live and serving traffic for ≥3 days.

## Task 19: Cutover & soak
- **Status:** pending
- **Depends on:** Task 16, Task 17
- **Docs:** [implementation.md#phase-6--cutover--soak](./implementation.md#phase-6--cutover--soak)

### Subtasks
- [ ] 19.1 Run `scripts/provision.sh` against prod subscription. VM provisioned.
- [ ] 19.2 Run `ansible-playbook ansible/site.yml`. `openclaw.service` active; cloudflared tunnel up at `openclaw-vm.posthub.cc`.
- [ ] 19.3 Pair Mac + iPhone clients against new tunnel + bot B. Capture pairing screenshots in `docs/migration-from-aci.md` if useful for future reference.
- [ ] 19.4 Daily for 7 days: review `journalctl -u openclaw -p err`, verify nightly restic backup + Azure snapshot succeeded.
- [ ] 19.5 Day 3: bump `openclaw_version` on VM as a canary upgrade test; verify smoke; rollback drill.
- [ ] 19.6 Day 7: land Task 18 PR (pin photo-cron to VM only).

## Task 20: Final verification
- **Status:** pending
- **Depends on:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11, Task 12, Task 13, Task 14, Task 15, Task 16, Task 17, Task 18, Task 19

### Subtasks
- [ ] 20.1 Run `testing-process` skill — execute `make lint`, run `test/test-*.sh` suite, run `scripts/smoke-prod.sh` against live VM, verify second `ansible-playbook` run reports `changed=0` (idempotency contract).
- [ ] 20.2 Run `documentation-process` skill — verify `README.md`, `docs/*.md` in `openclaw-vps`, plus update `docs/wip/openclaw-platform-roadmap/roadmap.md` in `ai-workflows` to add a Topic-8c entry pointing at this work, and update `openclaw-deploy/README.md` to reflect dual-env reality.
- [ ] 20.3 Run `solid-code-review` skill with project language inputs `bash, ansible, bicep` — review playbooks, scripts, Bicep modules, focusing on idempotency, secret handling, and rollback safety.
- [ ] 20.4 Verify all 8 acceptance criteria from `design.md §7` pass.
