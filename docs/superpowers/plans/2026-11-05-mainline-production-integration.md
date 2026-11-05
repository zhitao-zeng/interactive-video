# Current production improvements: mainline integration

**Goal:** integrate the improvements actually used by the current Pi batch and publish the existing GitHub repository, as explicitly requested by the user on 2026-11-05.

**Architecture:** retain the autonomous creator and independent reviewer, existing media tools and batch dispatcher. Merge the reviewer and batch branches; restore the small template/data-entry changes currently present only in frozen runs. No new creative stage or replacement production system.

## Constraints

- Preserve original run packages, sessions, receipts, exports and all limits. Existing running workers/controllers remain on their frozen code.
- A single approved wall-window grant extends time once when dispatched, never request/take limits and never without recorded human authorization.
- Only viewing-blocking findings return to the original director; minor findings do not block; incomplete/tool failures are not automatic acceptance.
- Runtime libraries, credentials, media and the specific batch authorization stay local. Code and documented schemas belong in main.

## Work and verification

1. Merge `codex/reviewer-mode-20261104` and `codex/batch-concurrency-20261105`, preserving both media action restrictions and audio question forwarding. Rebuild the existing provenance entries after integration. Compare Gen8 controller/helper and reviewer-v3 tool copies with source.
2. Add regressions for mainline entry selection and legacy frozen entry preservation; RED then minimal fallback to the same merged starter/template when no historical snapshots exist. Add regressions for configured and absent local BGM catalogues; RED then advertise the existing music action at normal init. Port the two verified v3 instruction additions. Run the affected starter, batch, independent review, audio, grant, music and provenance regressions plus native Bash tests.
3. Obtain one independent whole-change review; fix Important issues with focused regression evidence. Commit and fast-forward main without touching unrelated local media. Push main/integration, publish GitHub, and verify visibility/default branch/remote commit and continued frozen production ownership.

## Review focus

- A new ordinary init must expose actual available music, without manufacturing a configured service when files are absent.
- Reviewer restriction and audio-question passthrough must both survive the merge.
- Mainline dispatch must not require batch-specific snapshot directories; retained historical batches must still prefer their existing snapshots.
- Grant consumption must preserve original starts, request/take limits and history; consumed grants and queued time are never renewed.
- Acceptance stays bound to the actual export; original-session repairs and already-submitted tasks are not duplicated.

## Execution record

- Reviewer merge `9a0fd40`; batch merge resolved only the existing provenance manifest. All frozen v3 audio code matches the merged tools; v3 has two additional instruction paragraphs to port.
- Ruling: ordinary init discovers the existing SDK music configuration, while historical frozen copies remain pinned. No separate music workflow, service starter or implicit media generation is added.

- Task 1 complete: reviewer merge9a0fd40 and batch merge95365bb; provenance conflict resolved by retaining both sets of entries and actual merged hashes.
- Task 2 complete: four new regressions observed failing (missing mainline selection/music capability), then all four passed after the small entry/capability fix. Affected Python suite (starter/mainline/queue/audio/review/music/timeline/postmix/finish) exit0; Bun timeout unit and native wiring tests ALL PASS, with actual2s default clamp,119s wall clamp and fitting timeout unchanged.
- Real local init17:36:30: producer sees95 tracks from the existing SDK configuration and the existing resident audio URL; reviewer has exactly inspect/audio, merged question passthrough, no model/media requests, no run budget created. Evidence media/runs/mainline-integration-validation-20261105/init-verification.json.
- Ruling: plain pi_video_agent.py batch remains a producer launcher; the existing prepared-batch queue owns independent reviews/repairs. Documented both roles instead of introducing a new combined workflow or migrating current batch state.

- Final independent review: one Important defect in the newly merged batch task precheck; no Critical. All five Review Focus areas passed independent inspection.
- Final fix: test_batch_second_episode_task_and_task_file_conflict_has_no_side_effects observed RED (did not raise; init/run side effects), then GREEN after rejecting the two non-null options in the existing final manifest-precheck loop. No separate validator or new workflow. Final affected-suite run is recorded in media/runs/mainline-integration-validation-20261105/affected-regressions.xml.
- Full frozen SDK comparison: src/scripts/configs/skills have no unported running changes; the only differences from the initial frozen SDK are the integrated Pi template/starter improvements.

- Final affected Python suite: {'tests': 159, 'errors': 0, 'failures': 0, 'skipped': 0}; native Bun unit and timeout-wiring tests passed. The one Important review finding is fixed by RED→GREEN and this green suite; no deferred Important/Critical or Minor findings.
- GitHub visibility confirmed public with default main. Integration is ready for fast-forward and remote verification; final receipt lives in media/runs/mainline-integration-validation-20261105/integration-result.json. Existing Gen8 PID2047397 remains the production owner; no migration/restart requested or performed.
