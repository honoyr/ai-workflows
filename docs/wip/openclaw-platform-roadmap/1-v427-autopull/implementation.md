# Implementation — v2026.4.27 + auto-pull-latest

> Design: [`./design.md`](./design.md)
> Files touched: `~/openclaw/{pull-latest.sh,build-image.sh,deploy.sh,env.sh,docker/Dockerfile}`, `docs/guides/openclaw-azure-setup-guide.md`.

## Step 1: Create `~/openclaw/pull-latest.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

CACHE_FILE="$(dirname "$0")/.upstream-version"
GH_API="https://api.github.com/repos/openclaw/openclaw/releases/latest"

# 1. Resolve latest stable upstream tag
echo "[pull-latest] Resolving latest OpenClaw release..."
upstream_tag="$(curl -fsSL "$GH_API" | jq -r .tag_name | sed 's/^v//')"
[[ -n "$upstream_tag" && "$upstream_tag" != "null" ]] || {
  echo "[pull-latest] failed to resolve tag" >&2; exit 1;
}
echo "[pull-latest] upstream=$upstream_tag"

# 2. Compare against cache
cached=""
[[ -f "$CACHE_FILE" ]] && cached="$(cat "$CACHE_FILE")"

# 3. Check if image already exists in ACR
acr_has_tag=$(az acr repository show-tags \
  --name "$ACR_NAME" \
  --repository openclaw \
  --subscription "$SUBSCRIPTION" \
  --output tsv 2>/dev/null | grep -Fx "$upstream_tag" || true)

if [[ "$cached" == "$upstream_tag" && -n "$acr_has_tag" ]]; then
  echo "[pull-latest] cache hit + ACR has openclaw:$upstream_tag — skipping import"
else
  echo "[pull-latest] importing ghcr.io/openclaw/openclaw:${upstream_tag}-slim -> $ACR_NAME/openclaw:$upstream_tag"
  az acr import \
    --name "$ACR_NAME" \
    --subscription "$SUBSCRIPTION" \
    --source "ghcr.io/openclaw/openclaw:${upstream_tag}-slim" \
    --image "openclaw:${upstream_tag}" \
    --force
  echo "$upstream_tag" > "$CACHE_FILE"
fi

# 4. Export for downstream scripts
export OPENCLAW_UPSTREAM_TAG="$upstream_tag"
echo "$upstream_tag"
```

Make executable: `chmod +x ~/openclaw/pull-latest.sh`.

## Step 2: Update `~/openclaw/env.sh`

Replace the `BASE_TAG` and `CUSTOM_TAG` block:

```bash
# OLD
# export BASE_TAG=v3
# export CUSTOM_TAG=custom-v3-1

# NEW — resolved at build time by pull-latest.sh
# BASE_TAG / CUSTOM_TAG are now exported by build-image.sh
export ACR_NAME=acrdgonor2
export WRAPPER_REV=1   # bump manually when Dockerfile changes
```

## Step 3: Rewrite `~/openclaw/build-image.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"

# 1. Pull latest upstream
upstream_tag="$("$HERE/pull-latest.sh" | tail -1)"
[[ -n "$upstream_tag" ]] || { echo "no upstream_tag" >&2; exit 1; }

custom_tag="custom-${upstream_tag}-${WRAPPER_REV}"
base_image="${ACR_NAME}.azurecr.io/openclaw:${upstream_tag}"
target_image="${ACR_NAME}.azurecr.io/openclaw:${custom_tag}"

echo "[build] base=$base_image target=$target_image"

az acr build \
  --registry "$ACR_NAME" \
  --subscription "$SUBSCRIPTION" \
  --image "openclaw:${custom_tag}" \
  --build-arg "BASE_IMAGE=${base_image}" \
  --file "$HERE/docker/Dockerfile" \
  "$HERE/docker"

echo "$custom_tag" > "$HERE/.last-build"
echo "[build] done. tag=$custom_tag"
```

## Step 4: Update `~/openclaw/docker/Dockerfile`

Top of file:

```dockerfile
ARG BASE_IMAGE=acrdgonor2.azurecr.io/openclaw:2026.4.27
FROM ${BASE_IMAGE}
# ...rest unchanged...
```

(Default `BASE_IMAGE` is just a fallback for hand-runs; CI/build-image.sh always passes `--build-arg`.)

## Step 5: Update `~/openclaw/deploy.sh`

Near the top, replace any hardcoded `${ACR_NAME}.azurecr.io/openclaw:custom-vN-M` with:

```bash
custom_tag="$(cat "$(dirname "$0")/.last-build")"
image="${ACR_NAME}.azurecr.io/openclaw:${custom_tag}"
```

…and pass `$image` to `az container create`. Token-extraction + next-steps heredoc remain as-is.

## Step 6: End-to-end run

```bash
rm -f ~/openclaw/.upstream-version ~/openclaw/.last-build
~/openclaw/build-image.sh   # imports + builds
~/openclaw/deploy.sh        # deploys
```

Expected:
- ACR has both `openclaw:2026.4.27` and `openclaw:custom-2026.4.27-1`.
- `az container exec ... openclaw --version` → `2026.4.27`.
- `~/openclaw/.upstream-version` contains `2026.4.27`.
- `~/openclaw/.last-build` contains `custom-2026.4.27-1`.

## Step 7: Update the guide

`docs/guides/openclaw-azure-setup-guide.md`:

- Replace any `v3`/`custom-v3-1` references with the new scheme.
- Replace the manual `az acr import` snippet with: "Run `~/openclaw/pull-latest.sh` (called automatically by `build-image.sh`)."
- Add a short paragraph describing `WRAPPER_REV` bump rule: "Bump `WRAPPER_REV` in `env.sh` whenever you change `docker/Dockerfile` or `docker/openclaw-init.sh`. Don't bump it for upstream-only changes — the upstream version handles that."

## Step 8: Sanity test the cache

```bash
~/openclaw/pull-latest.sh   # should print "cache hit + ACR has openclaw:2026.4.27 — skipping import"
```

## Rollback

If something breaks, the old `env.sh` block + manual `az acr import` snippet from git history (or the previous version of the guide) is the rollback. The new ACR images don't conflict with old ones — old `openclaw:v3` is still there until manually deleted.
