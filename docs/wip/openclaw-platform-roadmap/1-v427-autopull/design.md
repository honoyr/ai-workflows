# Topic 1 — Bump to v2026.4.27 + auto-pull-latest

> Parent: [`../roadmap.md#1`](../roadmap.md#1--bump-to-v2026427--auto-pull-latest)
> Companion: [`./implementation.md`](./implementation.md)

## Problem

Every OpenClaw release we manually bump `BASE_TAG=v2 → v3 → v4 …` in `env.sh`, in `Dockerfile`, in the guide doc, and in our heads. The opaque `vN` numbering carries no information about which upstream version it points at, and the `az acr import` step is a documented manual command rather than an automated step in `build-image.sh`.

Two changes solve this:

1. **Tag rename:** drop `vN`. Use `openclaw:<upstream>` (e.g. `openclaw:2026.4.27`) for raw imports, and `openclaw:custom-<upstream>-<rev>` (e.g. `openclaw:custom-2026.4.27-1`) for our wrapper builds. Tags now self-document.
2. **Auto-pull:** `pull-latest.sh` resolves the GitHub `latest` release tag, compares against a cached `~/openclaw/.upstream-version` file, and conditionally runs `az acr import`. `build-image.sh` invokes it at the top, then builds with the resolved tag.

## Goal

`./build-image.sh && ./deploy.sh` should always end with the container running the latest stable upstream OpenClaw, without any manual edits to `BASE_TAG` or hand-typed `az acr import`.

## Approach

### Resolve

```
upstream_tag=$(curl -s https://api.github.com/repos/openclaw/openclaw/releases/latest | jq -r .tag_name | sed 's/^v//')
```

→ `2026.4.27` today.

### Compare

`~/openclaw/.upstream-version` caches the last resolved tag. If equal and the ACR repo already has that image, skip the import (saves a round-trip). Otherwise:

```
az acr import \
  --name "$ACR_NAME" \
  --source "ghcr.io/openclaw/openclaw:${upstream_tag}-slim" \
  --image "openclaw:${upstream_tag}" \
  --force
```

### Build

`build-image.sh` then runs `az acr build` with `--build-arg BASE_IMAGE=acrdgonor2.azurecr.io/openclaw:${upstream_tag}` and tags the wrapper as `openclaw:custom-${upstream_tag}-${rev}` where `${rev}` increments per upstream tag.

### Deploy

`deploy.sh` reads the latest custom tag from a small state file (`~/openclaw/.last-build`) and points the ACI container group's image at it.

## Why this tag scheme

| Old | New | Information conveyed |
|---|---|---|
| `openclaw:v3` | `openclaw:2026.4.27` | exact upstream version |
| `openclaw:custom-v3-1` | `openclaw:custom-2026.4.27-1` | upstream + wrapper revision |

Reading a deployed image tag now tells you the upstream OpenClaw version at a glance, and the wrapper revision tells you whether our customisations (gosu, cloudflared, future Gemini CLI install per Topic 10) have changed.

## Trade-offs

- **Caching:** the `.upstream-version` file lives on the operator laptop, not in ACR. If a teammate runs the script on their laptop with stale cache, they'd still call ACR — `az acr import --force` is idempotent, so it's a no-op when the image already exists. Acceptable.
- **Beta channel:** GitHub's `releases/latest` returns the latest **stable** release. To test betas (`https://docs.openclaw.ai/install/development-channels`), accept an env override: `OPENCLAW_CHANNEL=beta ./pull-latest.sh` lists all releases and picks the latest pre-release. Out of scope for first cut.

## Validation

1. Wipe `~/openclaw/.upstream-version` and run `pull-latest.sh` → it should resolve `2026.4.27`, run `az acr import`, and write the cache file.
2. Re-run → it should detect cache hit and skip the import.
3. Run `build-image.sh` → builds `openclaw:custom-2026.4.27-1`. Verify in ACR: `az acr repository show-tags --name acrdgonor2 --repository openclaw`.
4. Run `deploy.sh` → ACI restarts with the new tag. `az container exec ... openclaw --version` returns `2026.4.27`.

## Out of scope

- Beta channel handling.
- Cross-machine cache sharing for `.upstream-version`.
- Automating wrapper-revision bump on Dockerfile change (manual `--rev N+1` for now).
