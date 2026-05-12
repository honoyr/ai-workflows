# Local OpenClaw on Mac Pro 2019 — Feasibility, Performance & Architecture

**Date:** 2026-05-11
**Hardware in scope:** Mac Pro 2019 — Intel Xeon W (16 cores), 128 GB DDR4 ECC, AMD Radeon Pro (32 GB VRAM, likely W6800X Duo or similar), NVMe SSD
**OS options:** dual-boot macOS + Linux (dedicated to OpenClaw)
**Goal:** Decide whether a local install beats the current Azure Container Instance deployment, and how to set it up.

> **One-sentence upfront:** The Mac Pro 2019 is a great node-host (the OpenClaw runtime itself will fly), a mediocre LLM-host (your AMD GPU is workable but slower than expected), and **cannot run [oMLX](https://github.com/jundot/omlx)** — that tool is **Apple Silicon only** (M-series), explicitly incompatible with Intel Macs. Plan around that.

---

## 1. Hardware reality check

| Component | Mac Pro 2019 | Apple Silicon M3 Ultra (for ref.) | NVIDIA RTX 4090 (for ref.) |
|---|---|---|---|
| CPU | Xeon W-3245M, 16C/32T, 3.2 GHz | Apple M3 Ultra, 32C | n/a |
| Memory | 128 GB DDR4-2933 ECC, 6-channel, ~140 GB/s | 192 GB unified, ~800 GB/s | 24 GB GDDR6X |
| GPU | AMD W6800X 32 GB VRAM, ~13 FP32 TFLOPS | M3 Ultra GPU, ~80-tier | RTX 4090, ~83 FP32 TFLOPS |
| Mem bandwidth (where LLMs live or die) | **140 GB/s system + 512 GB/s VRAM** | 800 GB/s unified | 1 TB/s VRAM |
| Compute stack | macOS: **Metal**. Linux: **ROCm 6.x (limited) or Vulkan** (llama.cpp) | macOS: **MLX** (native, fast) | CUDA |
| Killer fact | No CUDA. No MLX. AMD ROCm support for *consumer* W-class is patchy on Linux 6.x. | What omlx is built for. | What every model is built for. |

**Implications:**
- **MLX-based tools (oMLX, mlx-lm, etc.) are off the table.** They depend on Apple Silicon's unified memory model and Neural Engine. Intel + discrete AMD GPU is a fundamentally different architecture.
- **CUDA-based tools (vLLM, sglang, TensorRT-LLM) are off the table.** AMD only.
- **What works on your hardware:**
  - **macOS side:** `llama.cpp` with Metal backend (AMD GPU as Metal device — actually quite decent), `Ollama` (which wraps llama.cpp), `LM Studio` (GUI)
  - **Linux side:** `llama.cpp` with **Vulkan** backend (most reliable on AMD pro cards) or **ROCm** if your specific GPU is in the supported list. `Ollama` (Vulkan/ROCm). Forget vLLM/sglang/etc.

---

## 2. What "running OpenClaw locally" actually buys you

Break the latency budget into three layers and ask where the time really goes:

| Layer | Current (Azure ACI + cloud LLM) | Local on Mac Pro 2019 | Speed-up factor |
|---|---|---|---|
| **OpenClaw node runtime** (plugin load, RPC dispatch, session resolve, channel routing, tool calls, embedded-agent setup) | 50–400 ms per turn, plus SMB-share latency for state (10-50 ms per read/write) | **~5–50 ms.** Local fs, no network, NODE_COMPILE_CACHE, hot-loaded plugins. | ⭐ **5–20× faster** |
| **LLM call** (text generation) | Azure OpenAI gpt-5.5 via Codex: TTFT ~200-500 ms, generation 60-200 tok/s | **Local llama.cpp/Ollama on Metal/Vulkan:** TTFT 100-800 ms, generation 15-40 tok/s for 7-13B, 5-12 tok/s for 30B, 2-5 tok/s for 70B Q4 | ❌ **2–10× slower for equivalent quality** |
| **Storage I/O** (memory-lancedb queries, wiki ingest, photo OCR, file caching) | Azure Files SMB share over network: 5-100 ms per op | **NVMe local: 0.01-0.5 ms per op.** Optional ramdisk: ~0.001 ms. | ⭐ **100–1000× faster** |

### The honest summary

- **You will absolutely see the OpenClaw runtime feel snappier locally** — plugin operations, chat session resolution, memory queries, wiki searches. These are bound by I/O and node startup, not LLM. The biggest wins are here.
- **You will NOT get faster LLM responses** unless you accept a much smaller model. Azure gpt-5.5 (cloud) > anything your Mac Pro can run locally, full stop. Cloud frontier models simply outclass any 7-70B local model.
- **Hybrid is the obvious answer**: run OpenClaw locally (fast runtime, free, private), keep the LLM provider as cloud (gpt-5.5 via Codex OAuth) for primary chat, optionally route some flows (PII-sensitive, offline drafts, embeddings) to a local model.

---

## 3. Recommended architecture (three tiers)

### Tier 0 — "Just make OpenClaw fast"  *(minimum viable, ~1 day to set up)*
- Install OpenClaw natively on Linux (the dedicated machine) via `curl -fsSL https://openclaw.ai/install.sh | bash`.
- Keep cloud LLM provider (Azure OpenAI / OpenAI Codex OAuth — same as your container today).
- State stored on local NVMe (no SMB, no Azure Files). Optional `tmpfs` mount for hot caches.
- Run as a systemd user service (auto-restart, log to journald).
- **Expected outcome:** the OpenClaw runtime itself feels ~10× snappier (lower per-turn overhead, instant plugin loads, instant memory queries). LLM call latency unchanged. Telegram replies arrive faster because the node-side dispatch is faster, but the LLM portion is the same.

### Tier 1 — "Add a local LLM for some flows"  *(add ~1 day)*
- Install **Ollama** on the same machine.
- Pin one or two local models: e.g. `qwen3:30b-a3b` (sparse MoE, fast on AMD), `llama3.3:8b`, `nomic-embed-text` for embeddings.
- Add the local Ollama endpoint as a provider in OpenClaw config:
  ```json
  "providers": {
    "ollama-local": {
      "api": "openai",
      "baseUrl": "http://127.0.0.1:11434/v1",
      "models": [{"id": "qwen3:30b-a3b", ...}]
    }
  }
  ```
- Use cloud (gpt-5.5) for primary, local for:
  - **Embeddings** (privacy + cheap, runs offline)
  - **Photo-inbox extraction** (you don't need frontier-model quality for OCR + categorize)
  - **Long-context summarization drafts** (local is fine for first pass)
  - **Fallback when cloud is rate-limited or offline**

### Tier 2 — "Mostly local"  *(add ~1 week, more tuning)*
- Run a larger local model (e.g. `qwen2.5-72b` Q4 or `llama3.3:70b` Q4) on the AMD GPU + system RAM split. Expect 3-8 tok/s on your hardware — usable for non-interactive flows, painful for chat.
- Configure OpenClaw to use local as primary, cloud as fallback when the local model can't reach a quality bar.
- This is where Mac Pro 2019 hits a wall: 70B Q4 fits in 32 GB VRAM but inference is bandwidth-limited and the AMD card is not as fast as an M3 Ultra or RTX 4090 at the same model.

---

## 4. macOS vs Linux on the same Mac — which to pick?

| Dimension | macOS (Mac Pro on Big Sur/Monterey, last supported) | Linux (Ubuntu/Fedora/etc.) |
|---|---|---|
| OpenClaw node | ✅ supported, official installer works | ✅ supported, official installer works |
| LLM compute | `llama.cpp` + Metal: AMD GPU works as Metal device, good perf | `llama.cpp` + Vulkan: most reliable on AMD pro cards. ROCm: install pain. |
| Storage performance | APFS NVMe: ~3 GB/s read | ext4 / XFS NVMe: similar or slightly faster |
| Headless server use | ⚠️ macOS isn't great as a 24/7 daemon host (login, screen-saver, updates) | ⭐ ideal: systemd, journald, no GUI overhead |
| RAM disk | `diskutil eraseVolume HFS+ ramdisk $(hdiutil attach -nomount ram://...)` — works | `tmpfs` — first-class, simpler |
| Software ecosystem (Ollama, llama.cpp, model zoos) | Good | Best |
| Power management | Tight integration | More control, can disable suspend cleanly |
| Recommendation | Use only if you want to share with macOS workflows | ⭐ **Dedicated Linux is the right choice** for a 24/7 OpenClaw box |

**Verdict for your case (dedicated machine):** **Run Linux full-time.** Use macOS as a desktop on the same hardware only if you sometimes need it for other work — but for an "always-on agent host", Linux removes the macOS-server papercuts (Caffeinate, kernel_task, Spotlight indexing, login items).

---

## 5. Local inference stack — oMLX alternatives for Intel + AMD

oMLX (https://github.com/jundot/omlx) is **Apple Silicon only** — built on Apple's MLX framework, it uses unified-memory zero-copy and compiled MLX graphs. None of that translates to your Xeon + AMD W6800X box. But its *user-visible* benefits (warm-loaded models, persistent KV/prompt cache, fast cold-start, OpenAI-compatible local endpoint) all have solid equivalents.

### Mapping oMLX features → Intel+AMD equivalents

| oMLX feature                       | Intel + AMD equivalent                                                  |
| ---------------------------------- | ----------------------------------------------------------------------- |
| MLX compiled kernels               | **MLC LLM** (compiled Vulkan kernels via TVM/Relax)                    |
| Apple unified-memory zero-copy     | ❌ no equivalent. Mitigate with `--mlock` + `mmap` + 32 GB VRAM headroom |
| Warm-loaded model, hot swap        | **Ollama** `OLLAMA_KEEP_ALIVE=24h`; LM Studio multi-model               |
| Persistent KV / prompt cache       | `llama.cpp --prompt-cache <file>`; KoboldCpp Context Shifting           |
| Quantized KV cache                 | `llama.cpp --cache-type-k q8_0 --cache-type-v q8_0` (Vulkan-compatible) |
| Flash attention                    | `llama.cpp -fa` (Vulkan FA landed in 2025)                              |
| Speculative decoding               | `llama.cpp --draft-model ...` (Vulkan OK)                               |
| OpenAI-compat local server         | **Ollama**, LM Studio, LocalAI, llama-server                            |

### Tier 1 — Closest in spirit to oMLX

- **MLC LLM** (https://llm.mlc.ai) — compiled Vulkan kernels. Closest philosophical equivalent to oMLX. Faster than llama.cpp Vulkan on the same model/quant, at the cost of having to compile/quantize models (or use their prebuilt library). Use this when you want to squeeze maximum perf from the W6800X.
- **llama.cpp + Vulkan** — the workhorse. Every caching primitive worth having: `--mlock`, `--prompt-cache`, quantized KV cache, mmap, `-fa`, session save/load, `--draft-model`. Raw CLI/server, smallest moving parts, every model day-1.

### Tier 2 — Friendlier wrappers (same primitives under the hood)

- **Ollama** ⭐ — built on llama.cpp. Built-in keep-alive (`OLLAMA_KEEP_ALIVE=24h` = warm model resident in RAM/VRAM), auto KV-cache management, mmap, model registry, OpenAI-compatible API. Vulkan support is in for AMD on Linux.
- **LM Studio** — GUI + OpenAI-compatible server. Vulkan on Linux/Win, Metal on macOS.
- **LocalAI** — one daemon serves LLM + embeddings + image + Whisper STT. Good fit for OpenClaw's multimodal needs.

### Tier 3 — Niche / skip for now

- **llamafile** (Mozilla) — single-binary, mmap cold start is *very* fast; AMD GPU offload weaker.
- **KoboldCpp** — smart Context Shifting (avoids reprocessing full context as prompt grows); UX is roleplay-skewed.
- **vLLM** — best-in-class PagedAttention KV reuse across requests, but **CUDA only** in practice. Skip.
- **TGI** (HF text-generation-inference) — ROCm exists but flaky outside MI-class cards. Skip.
- **Candle** (Rust, HF) — lean but weak AMD GPU story. Skip.

### Decision for this project: **Ollama**

Going with **Ollama as the local inference daemon**. Reasoning:
- ~80% of oMLX's user-visible benefits (warm models, KV cache, fast restart, OpenAI-compat) for ~5% of the setup work.
- OpenAI-compatible endpoint → drop-in OpenClaw provider config (`baseUrl: http://localhost:11434/v1`).
- `OLLAMA_KEEP_ALIVE=24h` gives you the "always warm" oMLX feel.
- Native Vulkan path on AMD on Linux.
- Falls back to CPU automatically if VRAM is short.
- Most active community / fastest model availability vs. MLC LLM's compile step.

Keep MLC LLM in the back pocket as a future optimization once you have a stable Ollama baseline and want to chase more tokens/sec on the W6800X.

---

## 6. Performance optimizations specific to your hardware

### NVMe + filesystem
- Mount `/var/lib/openclaw` (or wherever `~/.openclaw` lives) on the **NVMe**, not a spinning disk if any are present.
- Use **XFS** or **ext4** with `noatime,nodiratime` mount options. Don't waste IO on access timestamps you'll never read.
- If your NVMe is PCIe 3.0 x4 (Mac Pro 2019 era), peak is ~3.5 GB/s. Plenty for everything OpenClaw does.

### `tmpfs` for hot caches (the "run on RAM" wish)
You have 128 GB RAM. Put fast-churn paths on `tmpfs`:
```
# /etc/fstab snippets
tmpfs  /var/tmp/openclaw-compile-cache   tmpfs  size=4G,uid=openclaw,mode=0700  0 0
tmpfs  /home/openclaw/.openclaw/cache    tmpfs  size=8G,uid=openclaw,mode=0700  0 0
```
- `NODE_COMPILE_CACHE` (already used in your container) on tmpfs: cold start drops from ~3s to ~1s.
- Plugin compile artifacts: tmpfs.
- Sessions, OAuth tokens, agent state, wiki, memory-lancedb: **NVMe, not tmpfs.** You don't want to lose these on reboot.

### Memory tiering (the "store on RAM" wish, but smarter)
For `memory-lancedb` (vector DB): mmap the index files from NVMe. Linux's page cache will keep hot indexes in your 128 GB RAM automatically. Don't put the database on tmpfs — you'd lose it on reboot, and the OS already caches hot pages. Just give the kernel headroom and watch `vm.dirty_ratio`.

For chat sessions (`agents/<name>/agent/sessions.json`, transcripts): NVMe, no tweaks needed.

For the wiki / memory-wiki vault: NVMe, but consider `bcachefs` or ZFS with a small ARC if you want sub-ms reads for very large vaults. Probably overkill at your data size.

### Node.js performance flags
```
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
export NODE_OPTIONS="--max-old-space-size=8192"   # let it grow; you have plenty of RAM
export UV_THREADPOOL_SIZE=32                       # match your 32 logical cores
```

### CPU affinity (advanced, marginal)
You have 16 physical / 32 logical cores. OpenClaw is single-process Node.js — it benefits from 1-2 fast cores, not 16. Don't pin it. But you can give it real-time-ish priority:
```
systemctl edit --user openclaw
# [Service]
# Nice=-5
# CPUSchedulingPolicy=batch  (or default)
```

### AMD GPU on Linux for llama.cpp (if doing local LLM)
- Install Mesa + Vulkan drivers (`vulkan-tools`, `vulkan-loader`, `mesa-vulkan-drivers`).
- Verify: `vulkaninfo --summary` should list your W6800X.
- Build llama.cpp with `LLAMA_VULKAN=1` (or use a prebuilt Ollama with Vulkan).
- ROCm is the alternative but is hit-or-miss for pro cards on recent kernels. Try Vulkan first.

---

## 7. The container pain points → solved by local?

| Pain point you mentioned | Local fix | Notes |
|---|---|---|
| "Not that flexible" | ✅ huge win | Edit code, `systemctl restart openclaw`, 2-second iteration loop instead of 2-8 min ACI redeploy |
| "Not that powerful" | ⚠️ partial | Faster node runtime + I/O, **same or slower LLM** unless you use small local models |
| "Costly" | ✅ huge win | No ACI hours, no Azure Files storage, no ACR storage. You still pay for cloud LLM API calls if you keep hybrid. |
| "Have to keep in mind container won't survive" | ✅ fully solved | Local fs is your fs. Edit files, debug live, nothing wiped on restart. |
| Multi-day debugging cycles for things like the codex harness | ✅ shorter | Same kinds of issues exist locally, but the debug loop is way faster. Plus you can `npm install` plugins and they actually persist. |

---

## 8. Pros / cons

### Pros of local on Mac Pro 2019 (Linux)
- ⭐ **Massive iteration speed-up** for non-LLM operations (plugins, memory queries, sessions, debug loop)
- ⭐ **Cost savings**: cloud API still paid, but no compute/storage/network for the container itself (~$60-150/month savings depending on ACI uptime)
- ⭐ **Privacy**: any flow you route to local models stays on your box
- ⭐ **Always available**: no Logic-App start/stop dance, no role-assignment wipes, no SMB symlink issues
- ⭐ **Real filesystem**: persistent npm installs, real symlinks, real permission control. No more "I installed codex and it died on container restart."
- 🆗 **Reusable**: same setup gives you a local dev environment for other AI projects (llama.cpp, embeddings, fine-tunes)

### Cons / risks
- ❌ **Frontier LLM speed/quality is cloud-only.** Don't expect local 70B to feel like gpt-5.5. It won't.
- ❌ **oMLX/MLX-anything is out.** You said you found that project — sorry, Intel Mac means no MLX. Use Ollama/llama.cpp instead.
- ⚠️ **Single point of failure.** Your container redeploys today; a local box has no auto-recovery to another machine. Mitigation: weekly backup of `~/.openclaw` to an external drive or off-box.
- ⚠️ **Power/uptime.** Mac Pro 2019 idles around 100-180W; 24/7 = ~75-130 kWh/month = ~$10-20 in electricity in the US. (Still far cheaper than ACI.)
- ⚠️ **Telegram & external services need a public endpoint.** Use **Cloudflare Tunnel** (you already do for the container) or **Tailscale Funnel**. Don't open ports on your home network.
- ⚠️ **Big VRAM but slow.** 32 GB VRAM tempts you to load 70B models — but the W6800X memory bandwidth is ~512 GB/s. M3 Ultra has 800 GB/s of unified, RTX 4090 has 1 TB/s. You'll be VRAM-rich, bandwidth-poor.

---

## 9. Concrete next steps (if you decide to do this)

### Phase 0 — Decide the scope
- [ ] Hybrid (cloud LLM, local node) **← recommended starting point**
- [ ] Mostly local (local LLM, cloud as fallback)
- [ ] All local (no cloud, ever)

### Phase 1 — Linux base
- [ ] Install Ubuntu 24.04 LTS or Fedora 41 (both well-supported on Mac Pro 2019)
- [ ] Enable AMDGPU kernel driver; install Mesa + Vulkan; verify `vulkaninfo`
- [ ] Format the NVMe with XFS; mount with `noatime,nodiratime`
- [ ] Create dedicated `openclaw` user; lock down sshd; set up firewall (`ufw`)
- [ ] Install Cloudflare Tunnel (you have the playbook from the container repo)

### Phase 2 — OpenClaw native install
- [ ] `curl -fsSL https://openclaw.ai/install.sh | bash` as the `openclaw` user
- [ ] `openclaw onboard --install-daemon` → creates systemd user service
- [ ] Copy/adapt `~/repos/openclaw-deploy/config/config.json` to `~/.openclaw/openclaw.json` (no envsubst needed locally — just paste your secrets)
- [ ] Install plugins: `openclaw plugins install @openclaw/codex`, `clawhub:@openclaw/brave-plugin`, `clawhub:@openclaw/memory-lancedb`
- [ ] Auth: `openclaw auth openai-codex` (your existing ChatGPT Plus OAuth carries over)
- [ ] Verify `openclaw doctor`, `openclaw gateway status`
- [ ] Adapt `scripts/smoke-local.sh` from your template repo — it already exists and will catch the same regressions

### Phase 3 — Telegram + channels
- [ ] Reuse your existing Telegram bot token (the bot doesn't care where the server runs)
- [ ] Update bot webhook (if any) to point at the new tunnel host
- [ ] Verify Telegram routing works end-to-end (smoke probe 8 + 14)

### Phase 4 — Local LLM (if/when you want it)
- [ ] Install Ollama: `curl -fsSL https://ollama.com/install.sh | sh` (Linux native, includes Vulkan/ROCm builds)
- [ ] Pull a starter model: `ollama pull qwen3:30b-a3b` (MoE, ~50-100 tok/s on capable hardware) or `ollama pull qwen2.5:7b` for speed
- [ ] Add to OpenClaw config as `ollama-local` provider
- [ ] Decide which flows route to local vs cloud (start with embeddings + photo OCR)

### Phase 5 — Performance polish
- [ ] `tmpfs` mounts for compile cache + hot scratch
- [ ] Node flags via systemd unit `Environment=`
- [ ] Weekly off-box backup of `~/.openclaw` (rsync to external SSD or a NAS)
- [ ] Adapt the smoke harness to run against the local installation

### Phase 6 — Decommission container (or keep as warm standby)
- [ ] Once local has been green for 2-4 weeks, stop the Azure container
- [ ] Optionally keep the Bicep + deploy.sh wired up as a DR path (rebuild in ~10 min if local hardware fails)

---

## 10. Where to put the new code

Don't try to fork `openclaw-deploy`. Two options:

**Option A — new repo `openclaw-local` (recommended).**
- `docker/` → `systemd/` (unit files instead of Dockerfile)
- `scripts/install.sh` (provisions a fresh Linux box: deps, OpenClaw, plugins, tunnel)
- `scripts/smoke-local.sh` (port from template — already mostly works)
- `config/openclaw.json` (no `${VAR}` envsubst needed — just secrets via a separate file)
- `docs/` (mirror the discoveries you'll inevitably accumulate)
- Reuse the same Cloudflare Tunnel + Telegram + Brave wiring

**Option B — branch on `openclaw-azure-template`.**
- Add `local/` subtree alongside `azure/`. Worse — mixes deployment models in one repo. Skip.

---

## 11. Final recommendation

**Do this:**
1. Start with Tier 0 (Linux + native OpenClaw + cloud LLM). 1 day of work. Validate the iteration-speed wins are real for your workflow.
2. Run both old (Azure container) and new (local) in parallel for 1-2 weeks. Compare. The smoke harness from your template will tell you when local is at parity.
3. Add Tier 1 (local Ollama for embeddings + photo extraction). 1 more day.
4. Decommission Azure container when comfortable.
5. **Don't chase local-frontier-LLM parity.** Your Mac Pro 2019 cannot match gpt-5.5. That's fine — keep cloud for hard chat, use local for the volume-flows.

**Don't do this:**
- Don't try to install oMLX. It won't work. Use Ollama/llama.cpp.
- Don't try to run 70B Q4 as your primary chat model on this hardware. Painful.
- Don't dual-boot if you can avoid it. Pick Linux. macOS-server-mode is fighting the OS.
- Don't put the entire `~/.openclaw` on tmpfs. You'll lose state on reboot. Tmpfs the *caches* only.

---

## References

- [oMLX repo](https://github.com/jundot/omlx) — Apple Silicon only, doesn't fit your hardware. Useful to bookmark for if/when you upgrade.
- [OpenClaw install docs](https://docs.openclaw.ai/install) — official installer covers Linux native.
- [Ollama Linux install](https://ollama.com/install.sh) — includes Vulkan/ROCm.
- [llama.cpp Vulkan backend](https://github.com/ggerganov/llama.cpp/blob/master/docs/build.md#vulkan) — best path for AMD on Linux.
- Your existing repos that will be reused:
  - `openclaw-deploy` — config patterns, secrets handling, plugin install order
  - `openclaw-azure-template` — smoke harness, discovery doc format, the persistence model lessons

---

## Appendix A — Agentic RAG capabilities (OpenClaw + this container)

This appendix maps the five common "Agentic RAG" requirements onto OpenClaw and onto our specific Azure container deployment as of 2026-05-11. The motivation: OpenClaw is itself an agentic platform (peer to LangGraph / AutoGen / LlamaIndex), not a host *for* them, and that confuses people evaluating the stack.

### A.1 Capability matrix

| # | Requirement                                                        | OpenClaw (the platform) | This container today                                                                                                                                                                                                       |
| - | ------------------------------------------------------------------ | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Agent tooling & orchestration (plan, tools, memory, critic)        | ✅ Native — agents, sub-agents, MCP plugins, sessions, tool-use loop, hooks. Not a host for LangChain/AutoGen — it is the orchestrator. | ✅ `main` agent + `agents` namespace, MCP bridge, hooks, sessions persisted on Azure Files. |
| 2 | Dynamic / autonomous retrieval (multi-source, in the reasoning loop) | ✅ Native — agents call tools iteratively; chains like vector → web → code → vector are normal. | ✅ `@openclaw/brave` (web), `@openclaw/codex` (shell/HTTP/SQL via code-exec), memory tools (`memory_recall`, `memory_search`, `memory_store`). |
| 3 | Hybrid search + reranking (vector + BM25 + MMR + temporal decay)   | ✅ **Native** in `memory-core` (the default backend): hybrid BM25 + vector via SQLite FTS5 + `sqlite-vec`, configurable weights, MMR diversity, temporal decay. ⚠️ Not exposed by the `memory-lancedb` alternate backend — that one is dense-vector only. | ❌ **Currently misconfigured.** Container has `memory-lancedb` enabled as the memory slot, which **opts out of hybrid + MMR + temporal decay**. The `active-memory` plugin (which triggers automatic recall before replies) is also `disabled`. We're getting vector-only retrieval with no auto-recall. |
| 4 | Long-running iterative compute                                     | ✅ Native — no hard step cap, context-window is the real ceiling. | ⚠️ ACI 2 vCPU / 4 GB RAM is tight for parallel sessions + long loops. This is one of the original drivers for migrating to local. |
| 5 | Multimodal (text + images / audio, retrieval / storage / processing) | ✅ Native — Gemini Embedding 2 indexes images + audio alongside Markdown, plus full vision-LLM pipelines, image-gen. | ✅ Foundry: `gpt-image-2` + `FLUX.2-pro` + vision-capable `gpt-5.5` + `text-embedding-3-small` + `Kimi-K2.6` + `DeepSeek-V4-Flash` + `model-router-1`. Photo-inbox live. Image/audio memory indexing not yet enabled (requires Gemini Embedding 2). |

### A.2 The real architecture (from official docs)

OpenClaw memory is a **layered system** of distinct components — easy to confuse:

| Component | Role | Where docs live |
| --- | --- | --- |
| `memory-core` (default) | Markdown files on disk (`MEMORY.md` + `memory/YYYY-MM-DD.md`) + SQLite index (FTS5 + sqlite-vec). **Has hybrid search + MMR + temporal decay + multimodal built-in.** | [`/concepts/memory`](https://docs.openclaw.ai/concepts/memory), [`/concepts/memory-search`](https://docs.openclaw.ai/concepts/memory-search), [`/reference/memory-config`](https://docs.openclaw.ai/reference/memory-config) |
| `memory-lancedb` (alternate slot) | Replaces `memory-core` storage with LanceDB. **Vector-only.** Useful when you want a portable vector DB or S3-backed storage. | [`/plugins/memory-lancedb`](https://docs.openclaw.ai/plugins/memory-lancedb) |
| QMD backend (alternate) | External `qmd` process for search; configurable BM25-only or vector modes. | [`/reference/memory-config`](https://docs.openclaw.ai/reference/memory-config) under `memory.qmd` |
| `memory-wiki` (companion) | Provenance-rich, structured knowledge base layer beside the active memory plugin. | [`/plugins/memory-wiki`](https://docs.openclaw.ai/plugins/memory-wiki) |
| `active-memory` (sub-agent) | Bounded blocking sub-agent that decides when to call recall **before** the main reply. Surfaces `memory_recall`, `memory_store`, `memory_forget` to agents. | [`/concepts/active-memory`](https://docs.openclaw.ai/concepts/active-memory) |

**Settings live under `agents.defaults.memorySearch`** (not under any one plugin). The hybrid / MMR / decay / multimodal knobs are all there.

### A.3 Live probe results (2026-05-11, `openclaw-container-dgonor`)

Source: `az container exec` against the live ACI, `openclaw plugins list --json`, plus targeted `grep` over compiled plugin bundles.

- `@openclaw/memory-lancedb` v2026.5.7 — `kind: memory`, **`enabled: true`**, `status: loaded`, deps installed (`@lancedb/lancedb ^0.27.2`, `apache-arrow 18.1.0`, `openai ^6.36.0`, `typebox`). Exposes 0 tools / hooks / commands / routes via static metadata. `configSchema: false`.
- Compiled bundle grep counts (case-insensitive) for `memory-lancedb`: `hybrid`: 0, `rerank`: 0, `bm25`: 0, `fts`: 0 — **vector-only**, confirms what docs say.
- `active-memory` stock plugin: `disabled`. The agent has no auto-recall before replies.
- 71 of 97 plugins enabled overall. OpenClaw build: `2026.5.9-beta.1`.

**Implication:** Today, the container does dense-vector retrieval with no automatic recall. We are *not* using OpenClaw's built-in hybrid search — not because the platform lacks it, but because our chosen memory backend doesn't expose it and our active-memory plugin is off.

### A.4 The correct fix — official-docs path (recommended)

This is the canonical setup per the docs. It uses only OpenClaw's own primitives — no sidecars, no MCP servers, no custom scripts.

**Step 1 — Switch the memory slot to `memory-core` (the default)**, which gives us hybrid BM25+vector, MMR, temporal decay, and multimodal out of the box:

```json
{
  "plugins": {
    "slots": { "memory": "memory-core" }
  }
}
```

If we want to keep LanceDB-backed storage (e.g. for the existing `az://openclaw-memory/lancedb` Azure Files mount), keep `memory-lancedb` but understand the tradeoff: vector-only, no FTS, no MMR. Per docs, this is a deliberate alternate backend, not a bug.

**Step 2 — Configure hybrid search with sensible defaults** under `agents.defaults.memorySearch`:

```json
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "query": {
          "hybrid": {
            "enabled": true,
            "vectorWeight": 0.7,
            "textWeight": 0.3,
            "candidateMultiplier": 4,
            "mmr": { "enabled": true, "lambda": 0.7 },
            "temporalDecay": { "enabled": true, "halfLifeDays": 30 }
          }
        },
        "store": {
          "vector": { "enabled": true },
          "fts": { "tokenizer": "unicode61" }
        },
        "cache": { "enabled": true, "maxEntries": 50000 }
      }
    }
  }
}
```

Defaults from docs: `vectorWeight=0.7`, `textWeight=0.3`, `candidateMultiplier=4`, `mmr.lambda=0.7`, `temporalDecay.halfLifeDays=30`. We're explicitly setting them so the intent is auditable.

**Step 3 — Enable `active-memory`** so the agent automatically recalls relevant memory before replies, scoped to direct messages on the `main` agent:

```json
{
  "plugins": {
    "entries": {
      "active-memory": {
        "enabled": true,
        "config": {
          "enabled": true,
          "agents": ["main"],
          "allowedChatTypes": ["direct"],
          "modelFallback": "google/gemini-3-flash",
          "queryMode": "recent",
          "promptStyle": "balanced",
          "timeoutMs": 15000,
          "maxSummaryChars": 220,
          "persistTranscripts": false,
          "logging": true
        }
      }
    }
  }
}
```

Agents now get `memory_recall`, `memory_search`, `memory_store`, `memory_forget`, `memory_get` tools.

**Step 4 (optional) — Enable multimodal indexing** for the photo inbox flow. Requires a Gemini Embedding 2 provider:

```json
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "provider": "gemini",
        "model": "gemini-embedding-2-preview",
        "multimodal": {
          "enabled": true,
          "modalities": ["image"],
          "maxFileBytes": 10000000
        }
      }
    }
  }
}
```

**Step 5 — Restart, reindex, verify:**

```sh
openclaw gateway restart
openclaw memory index --force --agent main
openclaw memory status --deep --agent main
openclaw memory search "<a known phrase>"
```

`memory status --deep` will report whether hybrid is active, embedding provider is healthy, sqlite-vec is loaded, FTS rows are populated, and cache size.

### A.5 When the LanceDB backend is still the right choice

Keep `memory-lancedb` instead of switching to `memory-core` only if:

- You need **S3-backed memory** (LanceDB's `storageOptions: { access_key, secret_key, endpoint }` — `memory-core` is local SQLite).
- You explicitly want a portable, externally-queryable Arrow/LanceDB store outside OpenClaw.
- You're okay with **vector-only** recall, no BM25, no MMR, no temporal decay.

For our use case (single-tenant personal agent, photo inbox, code/chat sessions), `memory-core` is the clear winner per the docs. The "LanceDB on Azure Files" path we currently use was probably chosen for cloud-portable storage, but we lose more than we gain.

### A.6 Reusable response (for the original inquirer)

> OpenClaw is itself an agentic orchestration platform — comparable to LangGraph / AutoGen rather than a host *for* them. Our deployment runs:
>
> - Agents with looping tool-use (memory, web search, code/shell, internal APIs via MCP) and an `active-memory` sub-agent that performs recall **before** the main reply.
> - Multiple frontier LLM providers behind a router (OpenAI gpt-5.5, DeepSeek V4, Kimi K2.6, image: FLUX.2-pro / gpt-image-2).
> - **Hybrid retrieval** (BM25 + vector) with MMR diversity, temporal decay, and optional multimodal (image/audio) embeddings — all built into the default `memory-core` backend (SQLite FTS5 + `sqlite-vec`). LanceDB and QMD are available as alternate backends.
> - Persistent vector memory + sessions + sub-agents.
> - Multimodal pipelines (photo inbox, vision models).
>
> Long-running iterative compute is a function of the chosen runtime — we currently run on Azure Container Instances and are migrating compute-heavy workloads to a dedicated local node.

### A.7 Action items pulled from this appendix

- **Container fix (immediate):** Switch `plugins.slots.memory` to `memory-core` (or document why we're staying on `memory-lancedb`); enable `active-memory`; add the `memorySearch.query.hybrid` config block; reindex. This is a config-only change to `~/.openclaw/openclaw.json` on the container.
- **Local setup (Phase 4.5):** Mirror the same `memory-core` + `active-memory` + hybrid config on the local Mac Pro. With local NVMe, SQLite FTS5 + sqlite-vec is genuinely fast. No sidecar needed.
- **Optional:** Enable Gemini Embedding 2 multimodal indexing once we have a Gemini provider entry wired in (deferred until photo inbox redesign is finalized).
