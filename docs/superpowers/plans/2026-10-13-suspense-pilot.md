# Suspense Pilot Implementation Plan

> **For agentic workers:** Use executing-plans to implement task by task. Current user explicitly requests implementation; no additional handoff approval.

**Goal:** Complete the missing suspense assets and produce one full SD2.5 480p pilot from the approved 32-shot draft.
**Architecture:** Add a suspense adapter to the existing interactive pipeline in this isolated worktree. Reuse supplied Shen Xu images, generate Xu Cheng and location references, prepare exact evidence artwork, compile with one male voice per request, then run the existing reviewed renderer and delivery pipeline.
**Tech Stack:** Python, existing novel_manga production contracts, Pillow for typeset documents, ffmpeg, built-in image generation, existing SD2.5 endpoint.
**Spec:** projects/suspense/revisions/2026-10-13-investigation/{screenplay.md,storyboard.review.json,adaptation-notes.md}

## Global Constraints
- User-selected SD2.5 native generation resolution: 480p. Export: 1080x1920, 25 fps, H.264/AAC.
- Preserve all dialogue and source-shot IDs. One audible male character per request. Offscreen dialogue retains its speaker.
- Same-wall adjacent doors: 801 left, 802 right. Xu stays in 802 during investigation.
- 177-second authored body budget, 179-second backend budget plus exactly 75 interaction frames. No selected route or invented case solution.
- No commits, pushes, other-chat edits, or manual per-shot changes once full production starts.

## Review Focus
- API payload receives 480p rather than just a changed export label.
- Xu reaction with Shen offscreen voice does not get Xu voice or visible speaking instructions.
- Evidence is not revealed earlier than its authored shot.
- Reference images map to the correct named locations and characters.
- No inherited animation prompt or forced fixed-camera instruction contradicts the shot.

### Task 1: Prepare and compile suspense
**Files:** src/novel_manga/interactive/suspense.py; scripts/interactive_video.py; configs/styles/suspense-live.json; tests/test_suspense_production.py.
**Interfaces:** prepare_suspense(root: Path) -> Path; pack_suspense(directory: Path) -> dict; existing production.pack dispatches to this adapter.
- [x] Test material preserves dialogue, sources, 179 backend seconds, voices and native 480p payload.
- [x] Implement conversion from approved review JSON into chapter_script, bible, interaction and explicit request groups.
- [x] Run tests and compile; inspect actual prompts for photo style, camera and speaker constraints.
- [x] Keep changes uncommitted per workspace authorization.

### Task 2: Assets and voices
**Files:** assets/suspense/ (ignored generated media), projects/suspense/asset-manifest.json; suspense.py voice builder.
**Interfaces:** prepare_suspense imports local image masters into series_assets; build_suspense_voices(root) creates the two voice bank entries using existing provider generation.
- [x] Generate Xu Cheng and corridor/801/802 references with built-in image tool.
- [x] Typeset printed log, maintenance title, notebook and two separate 017 papers.
- [x] Generate separate clean Mandarin voice samples, preserve task receipts and attach only the active speaker voice.
- [x] Repack and validate all consumed references exist.

### Task 3: Full trial and delivery
**Files:** outputs/suspense-sd25; delivery/suspense/sd25; runtime ledger and shared active-session row.
**Interfaces:** production.run_episode(root, 'sd25', project='suspense') -> delivery receipt.
- [x] Fresh review of new adapter and compiled requests before submitting full film.
- [x] Run full generation and assembly. Content review was interrupted at the user’s explicit request after repeated service errors; export a clearly marked unreviewed trial. No manual take-by-take intervention.
- [x] Report actual generation resolution, delivered duration, quality status and output links, including failures honestly.
