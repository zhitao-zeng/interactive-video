# Prompt and Skill Unification Implementation Plan

> **For agentic workers:** Execute inline with executing-plans; one independent final review. Preserve all inherited uncommitted work.

**Goal:** Give creation and production one coherent sound, style and phase contract without changing existing films or budgets.

**Architecture:** Keep the existing creator and episode Agent. Resolve the current native-speech default only at new creative intake, preserve it in exported structured input, and validate delivery where it is authored. Consolidate sound guidance, attach production task instructions once, and carry selected style into new first-frame requests, review and freshness.

**Tech Stack:** Existing Python environment, pytest, existing image provider and Agent adapters. No dependency installation.

**Spec:** User-approved findings in main/.runtime/prompt-skill-audit-20261020/result.json and the user's “收拢一下”.

## Global Constraints

- New interactive creation defaults to no_reference_performance_first; explicit reference mode and historical frozen inputs remain supported.
- Do not change approved dialogue, camera choices, old media, budgets or sample-image review status.
- Do not invoke image/video APIs or restart production workers.
- Do not implement the unrelated general creator-to-material adapter or subjective audio review.
- Integrate only this task's diff over the captured main WIP baseline; no commit or push.

## Review Focus

- Existing creative sessions without the new effective profile must not silently acquire a new policy.
- Explicit reference mode must remain available; conflicting none/reference choices must fail before creation.
- Structured native speech must contain exactly one bound English performance, with words unchanged.
- New first-frame style changes must invalidate the image; old contracts without style snapshots remain readable.
- Initial and resumed production must receive the same selected policy once, including when the plan is already prepared.

## Task 1: Sound authoring and handoff

**Files:** story/voice_delivery.py; interactive/{creation,creative_contract}.py; episode/inputs.py; skills/episode-production references and tests/test_creative_agent.py.

**Interfaces:** Reuse NATIVE_SPEECH_POLICY and native_speech_selected. Extract performance_error(cue) for both creator and request validation. New creator manifests expose profile; exports preserve that profile and chapter_script.profile.

- [x] Reproduce missing default/profile export and Chinese emotion accepted under the explicit native policy.
- [x] Resolve new-task policy without mutating task.json; require the shared sound guide; reject conflicting policy and unusable English delivery at creation.
- [x] Consolidate speech/physical sound/postmix music instructions in sound-direction.md. Link from phase/output contracts, retaining legacy/full-prompt distinctions.
- [x] Run creator/native speech/skill-resource tests; retain results in the task runtime.

## Task 2: Phase roles and task instruction composition

**Files:** episode/{session,prompts,flow}.py; interactive/first_frame_entry.py; corresponding session/first-frame tests.

**Interfaces:** task_instructions(ctx) composes the selected speech and first-frame blocks once; flow appends it for both initial and resumed invocations.

- [x] Capture an initial/resumed composed task and the default adapter's creation role.
- [x] Route creation_only to the creative contract; script_preparation to request compilation. Keep production role and supported tools.
- [x] Remove first-frame entry's duplicate built-in agent_instructions and speech nesting; append task instructions once in flow.
- [x] Verify no-audio policy, caller-specific instructions, first-frame protocol and tools remain available on continuation.

## Task 3: First-frame style propagation

**Files:** style_packs.py; media/first_frames.py; episode/first_frames.py; tests/test_first_frame_production.py.

**Interfaces:** A compact selected style snapshot supplies an effective image prompt, is included in independent image review, and is retained in new source_contract records.

- [x] Capture image-provider and review inputs using local stubs; reproduce missing style.
- [x] Append selected appearance rules to the source-authored opening without rewriting it; preserve actual request text separately.
- [x] Include the same selected style in review and cache identity; reject stale new frames after style changes while leaving historical contracts readable.
- [x] Verify source prompt/IDs/history retained, style rejection blocks adoption, and changing style cannot reuse a previous candidate.

## Final verification and integration

- [x] Run affected tests, replay the audit probes, validate skill resources and inspect the task-only diff.
- [x] Obtain one independent review of the task-only diff and fix material findings with reproductions.
- [x] Run the full suite, excluding only the independently established preexisting repair-services snapshot mismatch if unchanged.
- [x] Compare main against the captured baselines, apply only this task's changes, and run relevant checks on main.
- [x] Close coordination records; report no commits/push, no media generation and remaining external validation limits.
