# GPT6 Service Evaluation Implementation Plan

> Execution: inline with the existing execution adapters; one whole-change review at the end. User authorization covers this migration and bounded evaluation. Preserve evidence; no commits/pushes.

**Goal:** move new Luna defaults to6 without changing saved jobs, and compare existing free/subscription services on identical real-data tasks.
**Architecture:** versioned resource presets, existing native SDK/Codex adapters, existing task policy/slots and append-only records. No new production coordinator.
**Tech Stack:** Python/JSON/pytest, installed Codex0.164 and existing sandbox image.
**Spec:** docs/superpowers/specs/2026-10-23-gpt6-service-eval-design.md

## Global Constraints
- New source branch codex/llm-service-gpt6-20261023; exact main dirty-source baseline saved under owned runtime, no credentials/media copied.
- Preserve old gpt-5.6-luna routes and old codex-luna symbolic helper presets; no stored-session rewrite.
- Keep effective low/medium, output contracts, shared capacity2/reserved helper1 and production limits.
- No media generation, provider service restart, new quota grant or unknown media-credit consumption.
- No new model call after a failed case to make that case pass; retain failures and SDK technical events.

## Review Focus
- An old saved SECOND_REVIEW_JUDGE=codex-luna must stay5.6 while a new implicit task uses6.
- Adding a Codex resource name must not accidentally send codex://local through the native API SDK.
- An absent legacy Phanthy API model is not made available by renaming it6.
- Metadata list availability is not counted as a real model call passing.
- Case constraints, tool actions, actual files, session identity and comparative content evidence remain distinct.

### Task1: migration
Files: configs/model-resources.json, src/novel_manga/llm/catalog.py, configs/agent_codex_luna6.json, configs/agent_codex_sol6.json, tests/test_gpt6_service_migration.py, tests/test_model_resource_catalog.py.
- [x] RED: assert select_agent(default)['model']=='gpt-6-luna'; endpoint_settings('codex-luna').model=='gpt-5.6-luna'; endpoint_settings('codex-luna-6').model=='gpt-6-luna'; restored old review/author routes stay5.6.
- [x] Implement versioned descriptors and replace only default candidate names. Adapter condition is row.get('base_url')=='codex://local'. New explicit profiles keep existing saved login, budgets and slots.
- [x] GREEN: run migration/catalogue/reasoning/route/Codex adapter tests. No production config copy before real model checks.

### Task2: frozen evaluation
Owned files: .runtime/llm-service-gpt6-20261023/{cases,configs,runs,results.json}; an owner file accompanies runtime directories.
- [x] Freeze exact source excerpts and canonical approved fields from projects/adventure/laughing-country-visual-h3-v1, projects/suspense/materials/backrooms-ep01-v2 and projects/romance/xindongdaiding-v1/pilot-v2. Serialize normal fixtures as UTF8 JSON as production does.
- [x] Extraction contract: copy three approved shots' indices, speaker/text tuples, in-frame arrays and sum durations exactly.
- [x] Repair contract: replace only one faulty English opening-frame prompt; preserve shot identity, all dialogue and given counts; one continuation appends a supplied audit tag without changing the candidate.
- [x] Scene contract: four shots/32–40seconds, supplied dialogue anchors and source quotes retained, same two kitchen characters, one physical kitchen; validate with the existing creative_contract plus explicit case constraints.
- [x] Run each of four routes once per task,180seconds; repair continuation only if a file exists. Record all native events, time, token receipts and output checks. No API key values in outputs.
- [x] Run new6 Luna JSON and existing two-image fixture once each through the actual adapter.
- [x] Freeze anonymous candidate bundle; one independent6 Astra textual review at240seconds. Report evidence-backed winner/tie and task-specific limits.

### Task3: cold integration and handoff
Files: docs/model-resources.md, this spec/plan, own main active-sessions row; isolated before/after receipts.
- [x] Whole-change review of only owned delta against copied baseline; fix demonstrated issues with targeted tests.
- [x] At a proven idle main-source boundary, verify exact preimages and copy only owned files; protect other source WIP and all persisted jobs.
- [x] Run relevant integrated tests and read-only effective-resource report. Expected: new implicit Agent/vision6 Luna; free JSON unchanged; historical codex-luna remains5.6.
- [x] Record real evaluation results, unmet criteria and remaining opt-in candidates. No changing history or claiming a short text comparison proves production film quality.

## Final review ruling and fixes
- Main concurrent source refresh retained85 nonowned files and the original evaluation-code snapshot. The unregistered zhiyao entry is excluded from migration claims and its config stays unchanged.
- One Important image-route coupling finding: add review_defaults for known resource capabilities, wire the two actual entry paths, preserve explicit/saved overrides, keep native-video separate and reject video_url in the image-only Codex adapter. Focused tests observed RED before the fix.
- The old5.6 source_quote failures are included alongside the ambiguous in_frame type in final reporting; no candidate is rewritten.
- Direct Sol6 profile also supplies primary JSON; one separately declared JSON-helper check passed, so JSON qualification is recorded. It is not a retry of a failed content sample.
- Test isolation now also blocks unmocked native Agent subprocesses and uses a temporary Codex slot directory; a new default must never turn offline tests into real model calls.
