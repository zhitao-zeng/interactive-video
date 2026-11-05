# Native four finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. No child agents or commits are authorized for this recovery.

**Goal:** Complete normal W07/W10/W12/W14 exports using current takes and original budgets.

**Architecture:** Use this cold checkout against the exact existing production contexts. Extend the existing scoped input repair only for source-reviewed postmix music_plan metadata; project external_music_cues from authored shots while preserving every native field. Run normal review/music/finish and delivery.

**Tech Stack:** Python, existing episode tools, ffmpeg.

**Spec:** R/EP06A-finish-music/README.md and current recovery task.

## Global Constraints

Preserve existing source, requests, takes, receipts, IDs, counters, limits and remaining time. No production or cold-C source edits. No additional workers beyond four episode processes. No commits or pushes.

## Review Focus

- Mixed H3/Fast episodes use the selected take's saved model grammar; retain strict checks.
- Invalid music anchors reject before source review/publication.
- Metadata-only changes cannot alter native fields or cause retakes.
- Source review must receive before/after root music_plan and all affected shot metadata.
- Real elapsed tool time is subtracted from original remaining time, including failed calls.

### Task 1: Recover current finish entry

- [x] Inspect processes, status, WIP, instructions, and exact contexts.
- [x] Confirm actual-take speech patch already applied; run the three focused suites.
- [x] Create an owned recovery runner using episode_lock, normal EpisodeTools and delivery; record each outcome and original budget debit.
- [x] Run W12 finish and W07 normal review/finish without new video.

### Task 2: Existing postmix input plumbing

Files: input_contracts.py, input_candidate.py, input_effects.py, input_repair.py, source_support.py, input_compilation.py; tests/test_postmix_music_plan_input.py.

- [x] Reproduce top-level music_plan rejection and protected native compiler drift.
- [x] Accept dict music_plan; validate it against the current candidate shots with music_plan_issues.
- [x] Copy music_plan into candidate_script and canonical effects; include old/new plans in source_check.
- [x] For strictly pure postmix edits, project only external_music_cues using the existing packing shape, protect all other native fields and require source consistency. Native changed remains [].
- [x] Run focused postmix/input tests.
- [x] Adopt W14 Agent candidate through normal input/source gate, then registered music selection and finish.

### Task 3: Finish W10 and clear actionable failures

- [x] Inspect exact existing music candidate and timeline; repair only selected source range through normal music selection.
- [x] Run normal finish and delivery. Preserve unresolved current visual findings as candidate receipts; complete mixed exports did not require new takes. Formal content acceptance remains unfinished.
- [x] Verify receipts and actual files; report accepted vs candidate truthfully. Preserve unsubmitted WIP and all history.

Outcome: All four canonical exports exist with normal technical_passed=true; content remains candidate under actual final review. No new image/video generation, no commits/pushes. Evidence: .runtime/native-four-finish-20261023/four-complete-exports-result.json.
