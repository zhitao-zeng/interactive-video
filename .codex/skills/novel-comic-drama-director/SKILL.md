---
name: novel-comic-drama-director
description: Convert Chinese novel files or chapter text into production-ready vertical manga-drama episodes with source-faithful splitting, reusable assets, bounded planning, native H3 speech performance or selected reference-audio/TTS workflows, subtitles and ASR. Use for novel-to-video creation, storyboards, character/location design, pipeline integration, or repairing repeated shots, inconsistent characters, mismatched speech and subtitles.
---

# Novel Comic Drama Director

Build a chapter-faithful 9:16 manga-drama pipeline. Reuse identities and locations,
not complete dialogue compositions. Follow the selected production profile for
native speech, voice reference, locked TTS or postmix. Put exact native dialogue
in the video prompt; supply audio only when that mode selects it. Keep the existing
lip-sync review/postprocessing policy.

## Run the workflow

For a complete script/storyboard/interactive draft without media production, use
this repository's [unified creator entry](../../../docs/unified-creative-agent.md).
It directly opens one persistent author from the complete package or original
brief, with camera, speech, genre, spatial-blockout and repair guidance in the
installed episode-production package. That creation-only phase does not enter
the production steps below or consume video takes.

1. Read the nearest project instructions and inspect existing plans, assets, reports, and generated media before changing anything.
2. Preserve the input chapter boundary and source-to-shot trace. Treat one source chapter as one episode when chapters exist.
3. Lock the series bible, then build separate character and location asset libraries before episode keyframes.
4. Convert the episode into scenes, shots, and speech turns. Keep these levels distinct:
   - episode: one source chapter;
   - scene: one location/time/emotional phase;
   - shot: one visual beat and camera setup;
   - turn: one narrator or one visible speaker utterance.
5. Bind each turn to its exact words and owner. For native H3, author the performance with the scene and obtain subtitle timing from generated speech. For a selected locked-audio workflow, synthesize and lock each turn's audio before video.
6. Keep authored camera, reference roles and speaker visibility. Native H3 can use dialogue, POV or offscreen delivery; a locked-audio close-up workflow uses its speaker composition and selected recording. Reuse identity, costume and location assets without imposing the same complete composition on different turns.
7. Render subtitles from the exact turn text and measured or forced-aligned speech boundaries.
8. Run the required media, subtitle, ASR, source-trace, and safety gates. Keep face consistency as a monitoring signal only.
9. Regenerate only units that fail required gates, then re-render and re-run final admission.

For native H3 creation, read the actual Agent's [authoring and speech performance](../../../skills/episode-production/references/authoring.md) and [output contract](../../../skills/episode-production/references/output-contract.md). Prefer delivery before exact dialogue, ordered reactions after their trigger, and meaningful closed-mouth pauses/endings. When no audio reference is selected, verify zero actual audio conditions; a prompt edit alone is insufficient. Preserve required voice locks. The user preferred the 2026-10-15 short front-loaded/no-reference pilot; this is evidence for that combination, not a universal no-reference rule or full-episode acceptance.

Read [production-contract.md](references/production-contract.md) for the locked-audio workflow's chapter splitting, output schemas, concurrency and provider routing. The current host/selected profile owns the active contract.

Read [asset-shot-design.md](references/asset-shot-design.md) when creating character assets, location plates, scenes, shots, keyframes, or video prompts.

Read [av-quality-gates.md](references/av-quality-gates.md) when producing audio, subtitles, dialogue clips, QC reports, or deciding whether an episode is deliverable.

## Enforce the non-negotiables

- Keep the story faithful to the source characters, relationships, event order, causality, and chapter ending.
- Enter chapter-specific action or conflict within ten seconds, including the fixed intro.
- Keep the speaking owner unambiguous and follow the supplied profile's shot/turn grouping.
- In a locked-audio close-up workflow, keep its speaker composition usable for the utterance. In native H3, preserve the authored shot, listener reaction or offscreen voice instead of forcing every line into a face close-up.
- Use the exact rendered audio as the subtitle timing source. Treat character-count interpolation as a fallback proxy, not word-level synchronization.
- Measure spoken-content accuracy with ASR and retain per-turn errors; an acceptable episode average must not hide badly wrong individual lines.
- Use listening feedback for delivery, meaningful pauses and music balance; ASR completeness does not establish natural speech. Retain the character's manner rather than giving every line the same emotion.
- Verify that subtitles were actually burned into the delivered pixels, not merely that an ASS file exists.
- Do not run screenshot-based mouth review, SyncNet-like scoring, LatentSync, or other lip-sync remediation. Occasional mouth mismatch is accepted by this pipeline policy.
- Prompt narrator shots as closed-mouth B-roll; do not add a separate mouth-motion admission gate.
- Preserve 1080x1920, 25 or 30 fps, H.264/AAC MP4 and 1080x1920 JPEG cover/end screens.

## Work within this repository

Prefer the existing project scripts and reports:

- `src/novel_manga/pipeline.py` and `production_runtime.py` are the canonical executable workflow; they must remain usable without Codex or this Skill directory;
- `src/novel_manga/production.py` compiles the scene/shot/turn hierarchy and creates reusable character/location assets;
- `src/novel_manga/runtime_backends.py` is the model-neutral command contract for alignment and ASR;
- `src/novel_manga/planner.py` implements bounded schema/domain validation feedback for OpenAI-compatible and command planners;
- `scripts/audit_multivoice_asr.py` for per-turn speech-content CER;
- `src/novel_manga/admission.py` for source trace, subtitle, ASR, and production admission;
- `src/novel_manga/qc.py` for container, resolution, frame-rate, audio, black-frame, silence, and freeze checks;
- `src/novel_manga/face_consistency.py` for non-blocking identity monitoring.

Do not call remote generation merely to test the Skill. Validate schemas and scripts locally first, then use the smallest authorized media probe.
