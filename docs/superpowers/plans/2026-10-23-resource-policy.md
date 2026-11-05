# Resource Policy Implementation Plan

> Execution: inline in the existing isolated worktree. The user's explicit request authorizes implementation of the approved in-chat design. Preserve all unrelated WIP and do not commit/push.

**Goal:** unify current resource inventory, shared quota groups and per-task reasoning including Luna without changing running tasks.
**Architecture:** add a small resource catalogue and pure policy helpers; connect existing SDK/JSON entry points and existing quota stops; reuse existing model slots and usage logs.
**Tech Stack:** Python, JSON, pytest; read-only authenticated metadata probes.
**Spec:** docs/superpowers/specs/2026-10-23-resource-policy-design.md

## Global constraints
- Current main code baseline c698b64; source worktree codex/resource-policy-20261023.
- Never log credentials or private credential identifiers. Raw equality stays in memory. One private token namespace is permitted under HERO because it replaces repeated provider identity queries and determines which quota stop applies; no file/media hashes.
- Do not change live service processes, tasks, receipts or generation counts.
- Explicit and saved task routes win; old sessions without policy stamps retain their settings.
- Unknown billing stays unknown; recent HTTP success is not semantic qualification.

## Review focus
- A zero-rate model may be unavailable because its gateway blocks exhausted paid pools.
- Different variables can reference one credential, but different credentials need not have different account balances.
- Luna fallback must not inherit DS disabled-thinking settings; unsupported off resolves visibly to low.
- Generic 403 and model/content rejection must not exhaust a quota group.
- New defaults must not replace saved routes or add media retries.

### Task 1: inventory and catalogue
Files: configs/model-resources.json; src/novel_manga/llm/catalog.py; scripts/model_resources.py; tests/test_model_resource_catalog.py.
1. Retain authenticated metadata and credential equality report under the owned runtime.
2. RED: write tests for qualified-free preference, unknown billing, explicit routing and confirmed quota groups.
3. Implement catalogue lookup and read-only report using existing presets; no credential values.
4. GREEN: run the new tests and existing endpoint/resource tests.

### Task 2: per-task reasoning
Files: src/novel_manga/llm/policy.py; agents sandbox/episode session/codex_session; llm client/transport/codex; repair/agent; tests/test_task_reasoning_policy.py.
1. RED: tests for author medium, mechanical off, Luna off->low, DS-to-Luna cleanup, legacy resume preservation and explicit overrides.
2. Implement pure policy application and snapshot handling at existing new-session boundaries.
3. Apply task roles to existing structured requests and preserve existing explicit endpoint routes.
4. GREEN: run policy/session/Codex/repair/client suites.

### Task 3: shared quota and cold adoption
Files: llm quota helper; providers/phanrouter_tasks; interactive/runtime; application resolved settings; relevant tests.
1. RED: tests for confirmed same-group stops, unrelated groups unaffected, non-quota errors ignored, no added take budgets.
2. Connect new routes to existing stop mechanism and resource diagnostics; keep old receipts/stop files.
3. Record pending gateway correction/cold activation separately; no service restart.
4. GREEN: run impacted offline suites, inspect an owned incremental diff against current main, and record activation status. Integrate only at a proven safe boundary; otherwise deliver tested branch and exact pending activation.

## Ruling after independent review
Two supported project roots may bind the same variable to different keys, and keys may rotate. Global stops keyed only by resource names are incorrect. Use a private credential-derived namespace for the stop path, never for logs/config/report fields. This is the narrow workspace HERO exception: it replaces a materially slower provider identity lookup and changes admission. Preserve every older stop record.
