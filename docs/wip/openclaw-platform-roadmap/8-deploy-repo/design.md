# Topic 8 — Versioning: extract `~/openclaw/` into `openclaw-deploy` repo

> Parent: [`../roadmap.md#8`](../roadmap.md#8--versioning-strategy-for-our-docker-setup)
> Companion: [`./implementation.md`](./implementation.md)

## Problem

`~/openclaw/` lives unversioned on one laptop. Secrets sit next to scripts in plaintext (`env.sh`). There's no CI, no rollback, no audit trail, no second-machine reproducibility, and the only person who can deploy is whoever has SSH to that laptop. As we add Bicep (Topic 9), CLI installs (Topic 10), and a v4.27 auto-pull pipeline (Topic 1), the lack of versioning becomes the bottleneck.

## Goal

A new private GitHub repo, **`openclaw-deploy`**, that:

1. Holds all wrapper code (Dockerfile, init script, build/deploy/local-up scripts, config.json schema).
2. Holds Bicep templates that declaratively describe ACR / Storage / ACI / Function (Topic 9) / Key Vault.
3. Has GitHub Actions wired to ACR for build-on-push and a manual-approval deploy job.
4. Pulls secrets from Azure Key Vault at deploy time. `env.sh` becomes a thin wrapper that hydrates env vars from Key Vault, with no plaintext secrets in the repo.

This unlocks Topics 1, 9, 10 and gives us a real rollback story.

## Repo layout

```
openclaw-deploy/
├── README.md              # quickstart + architecture diagram
├── .github/workflows/
│   ├── build-and-import.yml   # on push: pull-latest.sh + az acr build
│   └── deploy.yml             # workflow_dispatch: deploy.sh (manual approval)
├── docker/
│   ├── Dockerfile
│   └── openclaw-init.sh
├── scripts/
│   ├── pull-latest.sh
│   ├── build-image.sh
│   ├── deploy.sh
│   ├── local-up.sh
│   └── env.sh.example       # documents required env vars; real env.sh hydrated from Key Vault
├── bicep/
│   ├── main.bicep           # entry point
│   ├── modules/
│   │   ├── acr.bicep
│   │   ├── storage.bicep
│   │   ├── keyvault.bicep
│   │   ├── aci.bicep
│   │   └── function.bicep   # Topic 9 lives here
│   └── parameters.dev.json
├── config/
│   ├── config.json          # OpenClaw runtime config (non-secret)
│   └── secrets-keys.txt     # documents which Key Vault secret each env var maps to
└── docs/
    ├── runbook.md           # day-to-day ops
    └── recovery.md          # disaster scenarios
```

## Three-layer separation

| Layer | Where | Why |
|---|---|---|
| **Code** | git repo | versioned, peer-reviewable, CI-able |
| **Images** | ACR (`acrdgonor2`) | binary artifacts, tagged per upstream version (Topic 1) |
| **Secrets** | Azure Key Vault | not in git, audit logged, rotatable, accessible from GitHub Actions via OIDC federation |

## Key Vault wiring

Existing secrets in `~/openclaw/env.sh` move to Key Vault as named secrets:

| Env var (current) | Key Vault secret name |
|---|---|
| `AZURE_OPENAI_API_KEY` | `azure-openai-api-key` |
| `TELEGRAM_BOT_TOKEN` | `telegram-bot-token` |
| `AZURE_STORAGE_KEY` | `azure-storage-key` |
| `BRAVE_SEARCH_API` | `brave-search-api` |
| `CF_TUNNEL_TOKEN` | `cf-tunnel-token` |

The new `env.sh` becomes:

```bash
get_secret() { az keyvault secret show --vault-name "$VAULT" -n "$1" --query value -o tsv; }
export AZURE_OPENAI_API_KEY="$(get_secret azure-openai-api-key)"
# ...etc
```

GitHub Actions authenticates to Azure via **OIDC federation** (no long-lived service principal secret), assumes a workload identity with read access to the vault.

## CI/CD model

- **Push to `main`** → `build-and-import.yml`:
  1. Run `pull-latest.sh` to import latest ghcr image into ACR.
  2. Run `az acr build` to build wrapper.
  3. Tag artefacts. No deploy.
- **Manual `workflow_dispatch`** → `deploy.yml`:
  1. Required reviewers approve.
  2. Hydrate env from Key Vault.
  3. Run `deploy.sh`.
  4. Print gateway token + next-steps heredoc as workflow output.

This means a routine v4.27 → v4.28 upgrade is `git push` + click-approve, no laptop required. Pairs with Topic 9 to make the laptop fully optional.

## Why not Docker Hub

We already pay ACR. Cross-cloud pull adds latency. ACR has integrated managed identity for ACI image pulls. No reason to mirror.

## Why not folded into `ai-workflows`

Different lifecycle. `ai-workflows` is a docs/skills monorepo; `openclaw-deploy` is ops infra. Mixing them muddies CI scope (this repo's CI shouldn't fire on changes to `~/.claude/skills/`), audit trails, and access control.

## Migration plan (high level)

1. Create empty `openclaw-deploy` repo.
2. `git mv` files from `~/openclaw/` into the new structure.
3. Write Bicep that *describes* the existing ACR / Storage / ACI resources. Test with `az deployment group what-if` — should show zero changes.
4. Provision Key Vault, populate secrets from current `env.sh`.
5. Rewrite `env.sh` to hydrate from Key Vault.
6. Wire OIDC federation and Actions workflows.
7. Delete plaintext secrets from local `env.sh`. Keep `~/openclaw/` as a rolled-back checkout for emergency.

## Risks

- **Bicep drift on existing infra** — the Bicep description must match what's already deployed or `what-if` will try to recreate things. Mitigation: import existing resource state with `az resource show` and copy properties verbatim.
- **OIDC federation mis-config** — Actions can't assume the workload identity. Mitigation: test with `azure/login@v2` first, document the federated-credential subject pattern.
- **Key Vault outage during deploy** — if KV is unreachable, deploys fail. Mitigation: cache last-known secrets in encrypted local file as emergency fallback (out of scope for first cut).

## Out of scope

- Multi-environment (dev/staging/prod). One environment for now.
- Tearing down old ACR images / cleanup policies — handled separately.
- Migrating `~/openclaw/` git history. Fresh repo, single migration commit.
