# Copilot instructions — honoyr/ai-workflows

This is a **configuration-only template repository**. It ships skills, agents,
custom commands, and MCP server configs for AI coding assistants. There is **no
application code here** — the deployable systems live in sibling repos.

`CLAUDE.md` and `README.md` contain the full architecture and MCP/template-sync
details. The notes below capture project-specific gotchas that have repeatedly
tripped up automated sessions.

## Working directory discipline (read first)

Sessions are usually *launched* from this repo because the skills live here, but
the actual code changes target a **sibling repo** under `/Users/admin/repos/`:

- `openclaw-deploy` — the OpenClaw ACI container (most work lands here)
- `openclaw-vps`, `openclaw-vault`, `openclaw-azure-template`

Rules:
- Before editing or committing target-repo files, `cd` into that repo (e.g.
  `cd /Users/admin/repos/openclaw-deploy`). Run `git` commands from there so
  commits land in the right repository.
- **Never write target-repo files into this repo.** Build artifacts, patch
  scripts, and deploy helpers belong in the sibling repo, not in
  `ai-workflows/`.
- When a skill (e.g. `openclaw-container-dev`) drives work in another repo,
  treat that repo's root as the working directory for all file and git
  operations.

## Keep the repo root clean

Malformed shell redirects and wrong-cwd writes have repeatedly left stray,
non-ignored files in the repo root (e.g. `Probe`, `Recent`, `Verify`, or a
`patch-*.sh` that belonged in `openclaw-deploy`). `.gitignore` does **not** catch
these.

- After any shell-heavy step, run `git status` and remove unexpected untracked
  files in the root before finishing.
- Avoid commands that can splatter words into filenames (stray `>`/`<`,
  unquoted multi-word redirects, heredocs that misfire).

## `.claude` / `.serena` are symlinks

`.claude → .github/templates/claude` and `.serena → .github/templates/serena`.
Editing a skill via `.claude/skills/...` actually modifies the tracked file at
`.github/templates/claude/skills/...`, and that real path is where `git status`
and diffs show the change. Stage and commit under `.github/templates/`, not under
the `.claude`/`.serena` symlink paths.

## Architecture

- **`.github/templates/claude/`** — skills (`skills/<name>/SKILL.md`), agents,
  custom commands, and permission allowlist (`settings*.json`).
- **`.github/templates/serena/`** — Serena LSP config (`project.yml`).
- **`.github/template-state.json`** — template version + sync config. Add
  `sync_exclusions` (glob patterns) to keep local customizations from being
  overwritten by Template Sync. Review every sync PR before merging.

## Tests & lint

This repo's tests cover the template-sync tooling. Run from the repo root:

```bash
for test in test/test-*.sh; do "$test"; done   # full suite
./test/test-template-sync.sh                    # a single test
```

Tests share assertions/helpers from `test/helpers.sh` (`assert_equals`,
`assert_file_exists`, `assert_output_contains`, `assert_json_valid`, etc.). YAML
is linted/formatted per `.yamllint.yml` and `.yamlfmt.yaml`.

## Conventions

- Conventional commits (e.g. `feat: …`, `docs(discovery): …`).
- Explore directly with grep/glob/view; only delegate to exploration agents when
  explicitly asked (avoids double token usage — see `CLAUDE.md`).
