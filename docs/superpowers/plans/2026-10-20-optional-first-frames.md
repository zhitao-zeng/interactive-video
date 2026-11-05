# Optional fresh first-frame production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the authorized new Backrooms edition using independently checked fresh first frames and selective supplemental references through the existing Agent loop.

**Architecture:** Add one optional entry/profile mode and one host command to the existing episode Agent. Preserve logical source assets; store the actual frame/video-reference binding separately and feed existing rendering, cache and review infrastructure.

**Tech Stack:** Existing Python environment, PhanRouter image API, local MiniMax H3 Ref2VA, existing sandbox Agent bridge, pytest/ffmpeg.

**Spec:** docs/superpowers/specs/2026-10-20-optional-first-frames.md

## Global Constraints

- Every authored shot gets a newly API-generated opening; no old-video frame or derivative.
- Preserve original movies, takes and counts; use a separately prepared edition.
- First-frame-first; supplement only uncovered new people/content.
- Existing Agent, source/request checks, review and bounded repairs remain enabled.
- Final 1080x1920/25fps/H264/AAC; native H3 768P/8step LoRA is recorded separately.
- Preserve inherited WIP; integrate only the task delta; no commits or pushes.

## Review Focus

- Ordinary reference-only requests remain byte-for-byte equivalent in their condition layout.
- A supplier receipt survives an interrupted image operation without another paid request.
- An image rejection cannot admit a video or overwrite the last accepted frame.
- Opening/camera/asset changes reject stale frames; dialogue-only changes keep them.
- Offscreen speakers, additional visible people and audio numbering remain source-bound when image slots collapse to the first frame.

### Task 1: Native H3 endpoint and actual-reference plumbing

**Files:** providers/local_h3.py; media/generation.py; media/cache.py; application/rendering/cached_takes.py; application/agents/episode/contracts.py; tests/test_first_frame_production.py.

**Interfaces:** `create_video(..., first_frame: Path|None=None)` appends image/keyframe/frame_index0; `generation.image_paths(ctx, clip)` selects `first_frame.path` plus `extra_references` in opted-in clips, otherwise original paths. The first frame is also the first ordinary reference. Existing source Subject numbers stay fixed; contract picture expectations map original logical pictures to the real frame/supplement slots.

- [ ] Write red tests for default unchanged payload, real keyframe0, actual first/supplement paths and original voice numbering.
- [ ] Implement the optional provider argument and pure reference selection; reuse existing cache identities rather than adding hashes.
- [ ] Run new tests and relevant local-H3/cache/dialogue regressions.

### Task 2: Agent-owned first-frame preparation and repair

**Files:** new application/agents/episode/first_frames.py; episode/{tools,client,bridge,prompts,inspection}.py; rendering/submission.py; tests/test_first_frame_production.py.

**Interfaces:** `first_frames` consumes `output/first-frames.json` containing `frames:[{clip_id,image_prompt,image_references,visible_characters,extra_references,h3/source-compatible prompt_h3,source_quote}]`. It emits current per-clip candidate/review paths and remaining image attempts. Successful clips persist `first_frame:{path,extra_references,visible_characters,source_contract,prompt,review}` and the Agent's validated complete H3 string. `require_current(ctx,clip)` rejects absent/stale frames before any video submit.

- [ ] Write red tests for failed review, unchanged supplier resume, source-only references and stale opening.
- [ ] Implement three bounded image candidates per clip, original task receipt reuse, independent image check and evidence export to the Agent sandbox.
- [ ] Register the host command and current capabilities; document required per-shot source facts and mapped image slots in the Agent prompt.
- [ ] Retain native semantic/video/whole-film review and exercise existing repair/bridge tests.

### Task 3: Optional entry and one-shot grouping

**Files:** scripts/interactive_video.py; interactive/{production,authored}.py; first_frames.py; tests/test_first_frame_production.py.

- [ ] Write red entry/grouping tests including the existing three-second S19 and populated editions.
- [ ] Add `--h3-input-mode first-frame`, freeze the new edition mode, prohibit changing a live edition, and split only generation groups while preserving all source shots and wording.
- [ ] Use max(4, authored_seconds) only for this mode's generation request; retain authored timing.
- [ ] Verify default grouping, approved-material source trace and old edition counters remain unchanged.

### Task 4: Integration and authorized production

- [ ] Run targeted and full relevant tests, then a fresh whole-branch code review; fix evidenced issues before media calls.
- [ ] Apply only the isolated task delta to main, retaining current other-session edits.
- [ ] Prepare a new Backrooms edition from the approved material and old shared assets, saving original edition status/counts and lineage.
- [ ] Run the selected optional entry with existing Agent and review enabled; do not manually patch individual shots or reset budgets.
- [ ] Verify every selected take has a new accepted first-frame receipt, correct actual image roles, source trace and final media checks.
- [ ] Deliver preview/full movie and final findings to Mac mini; record sender success without claiming remote filesystem verification.

## Execution ruling

The user explicitly requested full implementation/production, approved the optional entry and selected the supplementary-reference policy. Execute inline in the isolated worktree, preserve all experiment evidence and use a fresh reviewer at the end. Skill examples proposing commits or additional approvals do not expand the task's commit permission or require asking again for already authorized reversible work.
