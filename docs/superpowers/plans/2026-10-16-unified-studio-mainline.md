# Unified Studio Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Consolidate adventure, romance and suspense into one tested mainline and shared entrypoints while retaining their original production data and budgets.

**Architecture:** Merge immutable snapshots through a new worktree. Keep one Codex session adapter and existing production tools. Select genre settings from the common configuration and resolve linked historical editions to their original directories.

**Tech Stack:** Python, Git worktrees, existing pytest environment and local filesystem links.

**Spec:** docs/superpowers/specs/2026-10-16-unified-studio-mainline.md

## Global Constraints

- Original content worktrees, private credentials, generated media and counts remain untouched.
- No image/video/TTS/author model calls, remote deployment or push.
- No historical output copied or reset; linked historical episodes resolve to the original path.
- Main is the maintained source; keep per-project settings, per-episode sessions and bounded repair permissions.

## Review Focus

- Old production state must not become a new edition through a path alias.
- Creator and production Codex sessions must retain their distinct tool/image contracts.
- Source/asset/audio fixes and final-review feedback must survive overlapping branch changes.
- Project defaults and episode overrides must not silently change another project's service or resolution.
- Uncommitted source snapshots must not mutate another conversation's checkout/index or import generated media.

### Task 1: Integrate snapshots and one Agent implementation

**Files:** Existing src/, tests/, skills/, scripts/ and owner-specific projects/ from the three snapshots; shared `application/agents/episode/codex_session.py`.

**Interfaces:** Preserve existing episode tools and `run_session(work_dir, prompt_text, *, resume, timeout, config, expected_output, enable_tools, project_root)`; creation uses that same adapter.

- [x] Capture tracked diffs and new text/code files with an isolated Git index, commit snapshot refs, merge in the isolated worktree and reconcile each actual conflict.
- [x] Retain both sets of existing Codex tests, run them against the unified implementation, fix any missing creator/production behavior. Expected: both modes pass without Docker/model calls.
- [x] Run the integrated full suite, resolve important integration failures, and commit the source integration.

### Task 2: Central project configuration and historical edition selection

**Files:** configs/interactive.json, interactive/runtime.py, interactive/production.py, scripts/interactive_video.py, tests/test_studio_mainline.py.

**Interfaces:** `project_config(root, project)` returns common defaults overlaid with that project's production/services; `run_episode` and CLI use resolved historical edition paths. Status can select `--edition` without generating. `create --task --output-dir` calls the single creator directly, without invoking novel reading/planning.

- [x] Add tests with a temporary config proving project overrides retain unrelated defaults and episode workers/native resolution still win. Run them before implementation; expected: missing project_config or wrong override behavior.
- [x] Add a linked historical edition test asserting the original directory is passed to the runner, its count is retained, and status never invokes rendering. Run before implementing; expected: alias path/default-only status failure.
- [x] Add a CLI dispatch test that records a direct creator call, rejects unexpected novel workflow/media calls, and retains resume/feedback/config arguments. Expected: create is absent before implementation.
- [x] Implement the smallest changes in existing entrypoints, retain argument behavior for current callers, run all affected tests and commit.

### Task 3: Cut over the local entry and verify the real retained data

**Files:** AGENTS.md, README.md, docs/branch-workflow.md, docs/active-sessions.md and task-specific runtime receipts; local data links excluded from Git.

**Interfaces:** Main owns source and project manifests; local aliases select the existing project-owned data and resolve old editions without copying media.

- [x] Bind only project-owned historical inputs/outputs/delivery/reference paths in the isolated/main worktrees; retain a plain path/size inventory and original snapshot refs. Do not overwrite existing data.
- [x] Run CLI help plus read-only status for one actual edition per project; compare its original path, report and stored generation count. Expected: original state reported, no media/model calls or count changes.
- [x] Run the complete suite and one independent whole-branch review; fix important findings with regressions, retain minor/ruling notes if any.
- [x] Commit and integrate locally through shared-pipeline/main. Update the sole-maintenance instructions and own coordination row, retain old worktrees and other uncommitted work, report exact validation and anything unverified.
