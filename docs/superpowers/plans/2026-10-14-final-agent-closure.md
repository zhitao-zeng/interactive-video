# Final movie and rejected candidate repair implementation plan

> For agentic workers: execute the approved two existing-loop fixes with the existing project tools; coordinator integrates and runs, Agents inspect imagery and propose repairs.

**Goal:** Feed delivered-movie failures and candidate rejection reasons back into the existing bounded repair flow, backport to the novel tools, then resume the existing backrooms episode and deliver its actual result.

**Architecture:** Keep the current runner, episode_review feedback, managed repair, sandbox proposals and generation history. A final-review callback checks the assembled or delivered movie before the loop decides no repair remains. Rejected candidate contents and reasons return to the same sandbox proposer at most twice more.

**Tech Stack:** Existing project Python environment, pytest, ffmpeg, existing VLM and Claude Code sandbox services.

**Spec:** The user approved the two defects and their correction in this conversation: final review must re-enter repair; rejected proposals must be revised with their rejection reason. The user explicitly selected automatic production and forbade manual shot patching.

## Global constraints

- Existing H3 episode: `suspense-backrooms-h3-ep01-v2_1`, baseline 28 takes. Keep histories and per-clip 3-take limits; every sandbox call remains 600 seconds.
- No new queue/database, global QC waiver, manual prompt/shot/timing patch, or unrelated environment/service change.
- Novel main has unrelated uncommitted edits. Backport only task-owned hunks after tests, preserving request_scope and original dialogue bindings.
- Existing old delivery remains frozen; final-loop exports and receipts use a separate task destination.
- Review outcomes require actual evidence. Technical passage, successful Agent execution and content acceptance remain separate.

## Review focus

- A native pass followed by a real final-movie failure must produce eligible repairs.
- A stale or unavailable final review must not certify a movie or trigger blind retakes.
- Bad proposals must not publish or spend video counts; stop, source change and service errors must stop that proposal path.
- A source keep/recheck must not erase an unresolved final-movie failure.
- Novel render/review/finish must consume the current final feedback, rather than overwrite it with a saved native pass.

### Task 1: rejected proposal correction

**Files:** `application/repair/agent.py`, `managed.py`, repair skill and `tests/test_repair_agent_feedback.py`.

- [x] Fail then pass the case `bad hood -> exact rejection feedback -> valid candidate -> one publication`.
- [x] Bound proposals to three and retain every candidate/rejection receipt.
- [x] Cover stop, input change, model failure and no remaining video budget.
- [x] Export final movie evidence frames into the sandbox; unavailable required evidence blocks rather than blind editing.
- [x] Integrate with whole-movie feedback and backport the added hunks to the novel tree.

### Task 2: final movie feedback and shared render loop

**Files:** new `application/review/final_cut.py`, `rendering/reviewed.py`, `review/execution.py`, interactive agent/delivery entry.

**Interfaces:** `review_assembly(runner, media, *, receipt=None)` returns a structured final review; `apply_feedback(directory, review, final)` returns the existing review enriched with current clip failures. `reviewed.run(..., final_review=None)` accepts an optional delivered-movie callback, with assembled-movie review as its ordinary default.

- [x] Add a failing integration fixture where `_review` returns native pass but the final callback fails `clip_01`; assert managed preparation/render executes and the second final review is required for passage.
- [x] Add fixtures for unmapped issue IDs, incomplete final review, unchanged rejected inputs, exhausted take counts and unresolved final issues after keep.
- [x] Extract real movie frames with clip and global-time labels, provide approved source/bible/request/caption context, and require actual observations and explicit clip associations.
- [x] Keep the native verdict and source bindings; merge final failures into the existing feedback map and export their frames via Task 1.
- [x] Call the delivered-movie callback before the repair loop exits; never mark it accepted only because native clips passed.
- [x] Run focused tests, then the full existing suite and independent code review before local integration.

### Task 3: novel backport and actual episode continuation

**Files:** novel isolated worktree matching common modules, `agents/episode/media_operations.py`, completion/publication acceptance paths and focused tests.

- [x] Add final review after actual assembly and expose current feedback in render/review/finish; preserve novel request_scope/dialogue protections.
- [x] Run novel focused and full regression, then apply only tested task-owned source/test hunks to actual main; preserve all unrelated WIP.
- [x] Snapshot the existing backrooms delivery/episode metadata, confirm baseline count 28 and unchanged limits, then run the single complete automatic H3 workflow using current assets.
- [x] Await the terminal review outcome, retain every rejected candidate/take, and send the resulting version plus truthful review/count receipts to the existing Mac target.

All real model/generation outcomes and any remaining content/budget blocker belong to the task runtime, not simulated test claims.

Actual terminal outcome: candidate, 28→36 takes, 8 Mac files sent with exit0. Automatic review retained one audio failure; the independent post-terminal art supplement confirmed seven additional visual categories. This is completion of the bounded production/delivery run, not content acceptance. Original three-take limits are unchanged; there was no manual shot repair.
