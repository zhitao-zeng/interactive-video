# 根因层 Agent 修复 Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task; native implementation and one final independent review. User explicitly authorized implementation and automatic production in the current message, after reviewing the concrete four-root-cause proposal.

**Goal:** Let the existing sandbox repair Agent correct verified source fields, select/create reference assets and repair interaction audio before any video retry; sync novel and resume the existing backrooms episode with current takes/counts intact.

**Architecture:** Extend current decision/RepairProposal route. Source-backed `input` edits compile existing shots using rebuild_clips and convert, not final-English patching. Assets are selected from reviewed candidates or created through the existing source/image-reviewed asset factory. `audio` adopts a verified independent ambience asset into delivery policy, triggers only export/review. The host validates/adopts; sandbox never mutates production. Review findings remain evidence to verify.

**Spec:** Approved root-cause diagnosis in suspense-reference-study/.runtime/backrooms-rootcause-20261015/root-cause.json and current user instruction.

## Execution record

User authorized the concrete prior root-cause plan and automatic continuation; no second design permission requested. Native implementation of Tasks 1/2 shares one action contract. Existing source check and asset factory were extracted without behavior changes to avoid a real dependency cycle. The ordinary novel path also supports independently reviewed ambience for an entirely silent clip, remuxing video unchanged; dialogue clips are rejected. Independent reviews found factory imports/policy compatibility, candidate-block cache, partial compile skip, implicit expressions views, no-assembly audio recovery, controlled publication path and refreshed media QC defects. Each was reproduced and fixed with regression tests. Existing reference single-view selection also prepares without a video debit at zero quota. All old inputs/takes remain available.

## Global Constraints

- Preserve actual generation history and old versions; no resets, no hidden quota expansion.
- Preserve unaffected clips, source anchors, bound dialogue and original scene facts.
- Root coordinates code/execution; Agents inspect imagery and propose artistic changes.
- Use existing project environments, image APIs and temporary Codex-login Luna standby.
- Enable native, automatic final-cut and independent review; failures cannot be accepted by metadata alone.
- No push; integrate local shared main/content branches and novel after tests, preserving other WIP.

## Review Focus

- Exhausted video quota cannot block source or audio repair and cannot silently grant generation.
- References must be independently checked real images and only selected clips may change.
- Source changes must reach rebuilt Chinese and actual English request; stale cached translations cannot survive.
- An audio-only correction exports/reviews without video generation or erasing independent issues.
- Same failure after corrected input cannot repeatedly consume new seeds; previous good anchors remain intact.

### Task 1: Scoped upstream Agent actions

**Files:** repair/agent.py, repair/managed.py, new repair/upstream.py, skills/video-repair/SKILL.md; tests/test_agent_upstream.py.

**Interfaces:** decision actions input/asset/audio plus existing edit/retake/keep/stop. Upstream returns RepairProposal for source/assets or assembly_changed for audio; no new queue/database. Runtime evidence catalogs reference existing reviewed candidate paths. Input compiles via rebuild_clips and convert.

- [x] Write failing tests for input edits reaching script and compiled request, no out-of-scope/timing/dialogue mutation, image candidate checks, quota-zero source repair.
- [x] Run tests RED; implement minimal host action validation/publication and evidence export.
- [x] Run scoped tests GREEN; preserve legacy request-only contract and max3 candidates.
- [x] Commit only task source/tests/skill/plan.

### Task 2: Audio adoption and export/review continuation

**Files:** repair/upstream.py, rendering/reviewed.py, interactive/delivery.py, review/final_cut.py, review/movie_context.py; tests/test_agent_audio_repair.py and interactive/final-cut regressions.

**Interfaces:** delivery_policy.interaction_ambience stores independently verified local audio path/provenance. Export validates actual audio duration and verified speech-free review. Existing native fallback retains honest ASR overlap finding. An assembly-only adoption continues final review without runner.run or generation debit.

- [x] Write failing tests for choosing verified audio, rejecting mixed/unreviewed source, independent review context changing with policy and audio-only re-export with no render.
- [x] Run RED; implement action and export path, retain fallback evidence and all prior export versions.
- [x] Run relevant suites GREEN and commit.

### Task 3: Integration and actual automatic continuation

**Files:** tests for cross-route regressions, docs record; novel synchronized minimal files; production runtime catalogs and existing episode only through host actions.

- [x] Run applicable/full suites and one independent branch review; fix actual important findings with regression tests.
- [x] Commit/merge shared main and compatible content branches; sync same implementation to isolated novel tree, test and apply only nonconflicting files to actual novel main.
- [x] Agents prepare actual asset/story/audio candidates and corrected review from current movie evidence; host exports them to repair sandbox.
- [x] Register corrected review; run fixed full automatic flow. Input-only repairs are legal at zero video budget. Any new video allowance must be explicit, conditional on verified changed input, logged without resetting original counts; at most one conditional extra for each qualifying exhausted source range; previously unspent video balances remain available without a new grant.
- [ ] Wait for full native/final/independent review; retain honest result and every old take/version. Send movie, preview, review and receipt to existing Mac destination; verify transfer receipts.
- [x] Record changed behavior/tests/commits/counts/review result/delivery limitations. Do not claim media passed unless gates passed.
