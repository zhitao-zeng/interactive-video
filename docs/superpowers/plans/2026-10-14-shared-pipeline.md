# Shared pipeline integration plan

Goal: consolidate the three approved content branches' common implementation into `codex/feat/shared-pipeline`, validate it, and merge it into local `main`.

Spec: user approved the shared-pipeline/main/content-branch split on 2026-10-14. This approval includes local snapshot commits and the tested main merge; no push or video generation is requested.

## Boundaries

- Preserve each original worktree, content source, production media, budgets and service state.
- Common implementation includes reusable topic adapters and their tests; authored episodes, assets and experiments remain topic-owned.
- Keep generated media, credentials, models, inputs and local symlinks out of commits.
- Capture current source changes before reconciliation. Do not overwrite edits made after capture.
- Reuse the existing Python environment read-only, with `PYTHONPATH=src`; no installation.

## Tasks

1. Snapshot the base studio and three content branches using the reviewed text-file inventory; record commit IDs and excluded local assets. Inspect staged paths before commits.
2. In this isolated worktree, integrate common source, tests, skills, CLI and portable configuration from those immutable commits. Start from the shared studio baseline, three-way reconcile differences, and retain all compatible per-topic adapters. Record the origin and resolution of each conflicting file. Add branch workflow documentation and the common AGENTS ownership rule.
3. Run the combined regression suite and CLI smoke checks. Correct actual merge failures with targeted regression coverage; do not regenerate movies or call production services. One independent code review of the final integration is required by executing-plans.
4. Commit the validated integration, merge it into a separate `main` worktree, and verify ancestry and clean state. Update the content branches from main only when their captured worktrees have not acquired new changes; preserve their content and explain any deferred synchronization.

## Validation focus

- All three project selectors retain their packing behavior and both H3/SD request grammars.
- Agent inputs retain current frames, real requests, original/editorial sources, runtime model/context/tool settings and budgets.
- Frame scope/portrait handling and viewer-first acceptance remain compatible.
- Deliveries retain evidence overlays and the exact 75-frame interaction tail without universalizing a single episode's QC waiver.
- A content branch's worktree location cannot change shared execution roots or accidentally consume another project's content.

## Progress

- Workspace and three sources inspected; all initially point to 440bfdf with uncommitted work. Shared worktree created from main.
- Decision: the existing user-approved chat design is the execution spec; no repeated approval is needed.
