# Independent review and original-director repair implementation plan

> **For agentic workers:** execute inline in the existing owned worktree; use the existing Pi producer/reviewer entries. Preserve all uncommitted code and do not commit or push without authorization.

**Goal:** audit existing and future finished movies independently, return viewing-blocking findings to the original director within its original limits, and give GPU52 more pending work.

**Architecture:** extend the existing task-owned lane dispatcher with two independent reviewer slots and a repair handoff. The reviewer remains the existing frozen `--role reviewer` Pi Agent. The host only dispatches, validates the report/export binding, and resumes the original producer; it does not decide artistic fixes.

**Tech Stack:** existing project Python, threading/subprocess, existing Pi/Bun runtime, existing frozen media SDK.

**Spec:** user instruction in this session, 2026-11-05: completed movies get backfill review; future movies auto-review; viewing-blocking problems go to the original Agent; minor issues do not block; running production continues; assign more to GPU52.

## Global constraints

- Original production clocks, request limits, sessions, accepted task IDs, failed takes and exports persist. No grants, resets or fresh replacement director sessions.
- Use Cloud2/Fast3/GPU161 production slots and two review slots. Preserve current five producer runners and the current S2E03B reviewer.
- Only unstarted episodes can change lane or receive updated tools. Reallocate five unstarted GPU16 episodes and two unstarted cloud episodes to FastH3.
- An accepted report must bind the actual current MP4 and contain no blocking findings. `incomplete` and missing reports never imply acceptance.
- Original budgets may already be exhausted for historical movies; review them, save the feedback, and mark repair blocked instead of renewing time.
- A repaired export is reviewed again. Keep rejected exports and reports; do not pay to review unchanged rejected bytes again.
- No new artistic stages, judges, media generation gates, uploads or prompt rewriting by the coordinator.

## Review focus

- Existing review runners survive a controller replacement without duplicate submissions.
- Minor-only accepted reports do not cause any director resume or generation.
- Wrong-version, contradictory, missing or incomplete reports never promote a movie.
- A rejected movie with an expired original director budget records a concrete block without changing counts or clocks.
- A valid repair resumes the same workspace/session, preserves the reviewed export and re-enters independent review only for a changed export.

### Task 1: Review binding and repair eligibility

**Files:** create `scripts/pi_episode_review.py`; extend `tests/test_pi_episode_lanes.py`.

**Interfaces:** report validation consumes the reviewer directory, target MP4 and expected SHA from its initialized TASK; repair eligibility consumes the original `run-budget.json`. Outputs are validated verdict/findings or an explicit protocol/budget error.

- [x] Add tests for accepted/advisory, contradictory accepted/blocking, changed MP4 and exhausted original limits; observe failures before implementation.
- [x] Implement validation without rewriting reviewer verdicts. Hash only where it decides whether a completed review can apply or an unchanged rejected export can skip another paid review.
- [x] Run the targeted tests and retain the result.

### Task 2: Wire existing roles and resume original directors

**Files:** `scripts/pi_episode_review.py`, `scripts/pi_episode_lanes.py`, `tests/test_pi_episode_lanes.py`.

**Interfaces:** the dispatcher owns one state and lock. Review workers select completed movies, adopt the existing S2E03B reviewer, or invoke the frozen normal reviewer entry; production workers take new episodes or eligible repair feedback.

- [x] Add regression fixtures proving rejected reports queue same-session repair, advisory-only accepted reports never queue repair, incomplete reports do not queue content repair, and old running reviewers are adopted.
- [x] Add two review workers; initialize complete reviewer TASKs with original source/authorization, current movie and optional trustworthy timeline. Never expose producer self-review to the reviewer.
- [x] Preserve rejected MP4/producer summary before calling normal `run --resume --prompt` in the original workspace. Only the original Agent chooses and executes fixes.
- [x] Keep production and review failures separate; do not stop unrelated healthy production because review needs attention.
- [x] Run all affected queue/review regressions, then one independent code review.

### Task 3: Freeze, safely hand over and verify live operation

**Files:** immutable generation-3 runtime package under the existing run, the existing STATUS/report and ownership row.

- [x] Freeze the tested dispatcher/helper. Pause only the old dispatcher briefly, record all live runner identities/budgets, and leave producer/reviewer processes running.
- [x] Change only selected unstarted task lane/config/authorization lines to FastH3; preserve input, history and quotas. Start the replacement sole dispatcher with six production slots and two review slots.
- [x] Verify the old runner PIDs/session pointers/runtime/config/clocks are unchanged; verify a new FastH3 task and independent backfill review actually start.
- [x] Inspect the first real reviewer result and act on any protocol failure before expanding repeated paid failures. Report real acceptance separately from started jobs or technical success.

## Decisions and evidence

- The user explicitly authorized execution of the described integration. No extra approval flow or commits are introduced.
- The first S2E03B new-contract review is already running and is adopted, not reset or restarted.
- Timeline is optional: if no exact export-linked timeline exists, the reviewer uses actual seconds rather than a guessed older timeline.

### Execution evidence before cutover

- Task 1/2: initial six missing-module fixtures failed, then 14 queue/review/repair regressions passed.
- Independent code review found two Important gaps: completed exports after producer-budget exit were skipped; empty coverage could pass. Both reproduced RED, fixed, and 21 affected tests passed GREEN.
- Additional source-reference correction: reviewer uses the original selected-package instead of the director-editable input copy; its wire-argument regression failed then passed.
- Real first reviewer S2E03B exited normally with accepted/0 blocking/2 advisory, 41 of96 calls, 938.35 seconds. Current movie SHA binding and populated coverage/limits passed the host validator. No movie or verdict was hand-edited.
- No commits or pushes: task instructions preserve uncommitted source and production history.

- Task 3 complete: 11:26 safe dispatcher handoff retained all five original producer runners and their budgets; Fast3 plus two actual audit sessions observed. All 17 current movies queued, first accepted. Production repair branch remains real-run unverified until a genuine blocking verdict.

### Live failure follow-up 13:14

S2E11B basename-only report plus exact matching initialized/current SHA was falsely blocked; reproduced and fixed without editing verdict. S2E06C transport Connection error gets one same-session recovery within original deadline/requests; repeated error remains stopped. A queued repair expiring while waiting is marked blocked_original_limits before dispatch. All three RED regressions and two original-session/receipt-reuse regressions passed; 26 affected tests green, independent delta review found no Important regressions. Gen4 parent-only cutover retained5 live producers and1 reviewer;06C actually resumed107→112 with original clock/session. 11B promoted only via existing unchanged raw accepted report,84 calls unchanged.05C15min approval pending; no grant applied.

### Explicit15min authorization follow-up

User approved S2E05C original-director repair for900 additional seconds only, unchanged request ceiling256 and take limits. Existing one-use frozen grant consumer selected only for this granted dispatch; pending grant is not consumed in queue. Old stopped harness is backed up and the verified bounded Bash runtime installed at resume, no active producer code changes.29 affected regressions pass; actual frozen consumer isolated check preserves160/256/original start and grants900s once only; independent delta review found no Important issue. Gen5 dispatcher cutover preserved6 producers and2reviewers. Approval stored and queue verification saved; no BGM change folded into repair.
