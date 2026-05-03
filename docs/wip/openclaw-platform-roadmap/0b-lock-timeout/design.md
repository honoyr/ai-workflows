# Topic 0b — Bump session-lock acquire timeout

> Parent: [`../roadmap.md#topic-0b`](../roadmap.md#topic-0b--bump-session-lock-timeout)
> Companion: [`./implementation.md`](./implementation.md)

## Problem

OpenClaw's session writer (`agents/sessions/<id>.jsonl`) coordinates concurrent writers via an `*.jsonl.lock` sidecar and `fcntl` advisory locks. With a 10 s default acquire timeout, contended writes (CLI probe + UI + Telegram all hitting the same session) commonly fail with `SessionWriteLockTimeoutError`. The runtime then mis-classifies that as a model-failover event (`decision=candidate_failed reason=unknown`) and silently swaps to the next entry in the configured-default fallback chain.

Root cause is two-fold: (a) Azure Files SMB doesn't carry POSIX advisory locks reliably, and (b) the default 10 s timeout is short enough that even legitimate slow writes time out under load.

## Goal

Eliminate the spurious timeout by raising the acquire timeout to **60 000 ms**. Combined with Topic 4 strict-mode routing, this removes both the visible symptom (silent model swap) and the underlying noise (legitimate-but-slow writers giving up).

This is a one-line config change. It is **not** a fix for SMB protocol semantics — that's parked under Topic 0. It is a pragmatic timeout bump that buys us back stability without architectural change.

## Why 60 s

- Empirically, contended SMB lock acquires resolve in 1-15 s under our load. 60 s is 4× the worst observed case.
- A stuck writer (genuinely deadlocked) takes 60 s to surface vs 10 s today. Acceptable trade-off for a single-user system.
- OpenClaw's request-level timeout is higher than 60 s for normal model calls, so we're not introducing a new ceiling.

## Validation

After deploy, drive concurrent load: open the UI, leave a CLI `infer model run` running, post a Telegram message. Watch the gateway logs (`az container logs --follow`) for any `SessionWriteLockTimeoutError`. Run for ~5 min. Zero occurrences = pass.

## Risks

- Wrong config key (the schema uses several similar names — see implementation step 1). Mitigation: inspect the running container's effective config first.
- A future genuine deadlock takes longer to surface. Mitigation: add a dashboard alarm on the metric/log if/when we add observability later. Not in scope for this task.

## Out of scope

- Switching to NFS Premium → parked as Topic 0.
- Disabling fallback chains globally → addressed differently in Topic 4.
- Moving sessions off the SMB share → would solve the problem but breaks Mac connectivity (see Topic 0 parking-lot rationale).
