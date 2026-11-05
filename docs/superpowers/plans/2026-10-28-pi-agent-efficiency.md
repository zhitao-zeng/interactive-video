# Pi Agent trajectory efficiency implementation plan

> For agentic workers: execute inline using the executing-plans approach; one final independent review. Preserve other sessions' work and do not commit without authorization.

**Goal:** Improve the existing editable Pi kit using failures from EP08B/EP09A/W22, then complete one fresh episode with no operator content intervention.

**Architecture:** Keep native read/write/edit/bash and editable production scripts. Supply reliable media primitives, exact capability information and a bounded native session; do not add film stages, a production planner, or a new review service.

**Tech stack:** existing Pi 1.4.0/Bun, project Python and media SDK, ffmpeg, existing Eureka GPU environment.

**Spec:** User-approved recommendations in this conversation: eliminate repeated environment discovery, provide reusable batch/wait and postproduction code, retain creative autonomy, report measured facts and finish within the existing limits. The user authorized optimization followed by one fresh episode on 2026-10-28.

## Global constraints

- Baseline source: pi-experiments-20261028/experiments/pi-agent-kit-v2-20261028 (uncommitted work copied into this isolated branch).
- Old tasks, takes, receipts and logs remain untouched. This branch owns only its copied kit, plan, tests and new run.
- New validation: one episode, 4 hours total, at most 256 LLM requests including retries/compaction, 128000 output ceiling. No quota reset or extra media after a hard stop. User may choose the episode while implementation proceeds.
- One scheduler launches the production Agent. Host does not modify episode content or patch a running production copy. Agent's own script edits are allowed and retained in its native history.
- Keep API credentials out of model-facing diagnostics and new logs. Reuse environments; no new model downloads.
- A successful process is not film acceptance. Report file metadata, content self-review, unresolved issues and delivery independently.

## Task 1 — media contracts and recovery

Files: template/tools/media.py, audio_service.py, plan.example.py, test_starter.py.

- [ ] Add failing tests proving SD reference_audios and H3 audios reach the actual SDK payload, first-frame incompatibilities are rejected before submission, and audio duration is validated before charging.
- [ ] Forward audio arguments, record actual audio attachment metadata and retain API response/error receipts.
- [ ] Add failing tests for accepted-but-reply-lost audio requests and same-request audio continuation.
- [ ] Save the audio job request/id locally, reuse completed windows, query a known job and never start another worker after an uncertain POST. Make service submission recoverable using a caller-selected job id; no durable queue framework.
- [ ] Run focused tests with MockTransport and a fake audio sensor, not paid calls.

## Task 2 — reusable execution and postproduction

Files: template/tools/assemble.py, add_music.py, new template/tools/batch.py, test_starter.py.

- [ ] Add a small synthetic 24fps-to-25fps multi-cut regression detecting cumulative audio/video drift and stale output reuse.
- [ ] Adapt EP09A's successful actual-frame-count approach into the existing assemble helper, including optional crop, subtitles and music. Preserve versioned outputs and actual source-to-timeline metadata.
- [ ] Add an optional bounded-concurrency batch runner for existing request JSON files. Use owned subprocess handles, save one result per request and wait directly; no ps/pgrep/sleep polling by the LLM.
- [ ] Verify batch failure reporting/child cleanup and ffmpeg output. No clip IDs or fixed shot count.

## Task 3 — native session budget and complete requests

Files: template/tools/agent.mjs, runtime.mjs, request_budget.mjs, associated JS/offline Pi tests, starter.py.

- [ ] Reproduce oversized recent images and lost original text. Remove silent text truncation. Reduce image transport copies while preserving originals; if images cannot fit, trigger native compaction or report a concrete size failure before network dispatch. Protect task text.
- [ ] Count every actual provider request, persist a single original start/deadline across resume, and refuse request 257. Let started media jobs retain their receipts/results; stop new dispatch at expiry.
- [ ] Report real request/turn/time statistics and the Agent-selected output paths separately from its quality claims. Include tested early-stop/resume behavior without unlimited auto-reprompts.
- [ ] Keep known credentials out of model-facing tool results using a small native-tool result sanitizer if the SDK exposes it; do not build a sandbox framework.
- [ ] Verify using fake SSE, including edit/bash, compaction/retry accounting, deadline, resume and credential masking.

## Task 4 — useful starting knowledge

Files: template/SYSTEM.md, TOOLS.md, SKILL.md, capability information generated at init, README.md.

- [ ] Document verified capabilities, correct Python/existing SDK locations, publication directory, native audio versus Eureka perception, reference image/audio semantics and available music catalogue.
- [ ] Teach optional reusable scripts, current-state notes, targeted checks and evidence-based stopping. Preserve original source/full skills; avoid re-reading completed media unless needed.
- [ ] Preserve creative judgment: design visual information/action/reaction from the source before selecting risk-reduction tactics; do not require all scenes to use a fixed shot pattern.
- [ ] Review the whole change once and fix material findings. No broad unrelated repository test sweep.

## Task 5 — fresh validation and comparison

- [ ] Prepare a new input-only workspace from the original package. Freeze host code/SDK configuration, model and original limits; do not reuse an earlier episode's generated movie or script.
- [ ] Start one authorized production Agent plus its audio service after checking resource availability. Record PID, paths, start/deadline and native session id in research state.
- [ ] Monitor real progress without steering content. Stop truthfully on a hard limit; do not quietly restart with more budget.
- [ ] At exit, verify the selected film exists/decodes, inventory receipts and metadata, and review its actual trajectory and self-review. Artistic assessment through an independent Agent as required by project instructions.
- [ ] Compare first-media latency, total wall time, model requests, process-polling calls, tool errors, full re-encodes and outcome against the historical baseline. Different episode/model means descriptive comparison, not controlled speedup.

## Execution notes

- 2026-10-28: isolated branch created; baseline kit copied without changing the maintainer's worktree. Required TDD skill file was not present in either local skill root; use explicit failing regressions for the reproduced defects. No commit is authorized, so preserve the plan and validation records locally.

- Progress: 16 focused Python regressions passed; three reproduced transport failures fixed, real ffmpeg drift regression fixed; native edit/bash/resume plus credential masking passed. JS body-budget and persisted-request-budget checks passed. Remaining: final request-limit integration, docs/preflight/review, W24 fresh run (user selected W24 explicitly).
