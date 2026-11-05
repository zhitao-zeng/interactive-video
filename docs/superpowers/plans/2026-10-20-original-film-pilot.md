# Original Creative-to-Film Pilot Implementation Plan

> Execute inline using the existing production flow and one final code review.

**Goal:** Run the user's authorized independent original short-film pilot without a hand-built material package.
**Spec:** User selected “从零创作一段新短剧，再自动做成片”; 45–60s paper suspense is the stated pilot assumption.
**Architecture:** The creative Agent supplies production design alongside its screenplay. A deterministic adapter preserves authored content and emits the existing material contract. A durable CLI coordinator invokes creation, assets and the existing reviewed persistent production Agent.
**Tech stack:** Existing Python environment, image API, DeepSeek1017 sandbox, H100 H3 eight-step service, local music runtime and existing review.

## Constraints

- New episode/run only; preserve old projects, histories and budgets.
- No coordinator-authored plot, manual shot repair, substituted verdicts or extra take grants.
- Default no-reference native speech; fresh first frames and needed-only extra cards.
- Keep native dialogue/environment/action audio; soundtrack follows the authored postmix timeline.
- New images through the existing API, no local model deployment or service changes.
- Preserve failed attempts; use normal resume, never recreate a spent edition.
- No commits or pushes. Integrate code through a minimal diff over captured main WIP.

## Work

- [ ] Add production-design validation and deterministic creative-material adapter; test exact dialogue/camera/opening preservation, missing design feedback and source quotes.
- [ ] Add the opt-in `produce` CLI coordinator with durable stage state; test that a failed creator never starts assets and that retries retain an existing edition.
- [ ] Wire the creative handoff schema/instructions into the existing author prompt and correction loop.
- [ ] Run targeted/full code checks and one independent review; integrate tested code.
- [ ] Freeze a task/code snapshot, run the original paper pilot through the real automatic stages, retain all model/asset/video/review results.
- [ ] Report the actual accepted/candidate/blocked result with viewable media when available; distinguish audio-rule checks from listening.
