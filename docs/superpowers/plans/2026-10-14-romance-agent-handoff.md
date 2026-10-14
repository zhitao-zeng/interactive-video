# Romance existing Agent repair handoff implementation plan

Goal: Bring the adventure worktree's validated same-take frame handoff into the existing romance H3 repair entrypoint, so failed review can produce a grounded correction and bounded rerender.

Spec: User asks to inspect another conversation's Agent integration while the authorized romance task requires failed review → repair → regenerate → review → Mac delivery.

Architecture: Reuse application.repair.agent, managed repair routing, existing sandbox executor and current episode profile. Port only frame export, the standard-library frame locator and associated repair instructions. Keep romance conversation packing and all take/history/budget data. Main novel Agent lifecycle changes and adventure creative content are read-only references.

Constraints: Current worktree only; no reset, commit, push, shared service/environment change or automatic budget replenishment. Generated assets and runtime evidence stay ignored. A real proposal is not a rendered or accepted episode.

- [x] Add the real sandbox-export regression from the adventure worktree, observe it fail here because actual take JPEGs are not handed off.
- [x] Port the validated export and locator into the existing H3-only entry; include the handoff policy in repair input state. Run focused regressions and full suite.
- [x] One independent review, then enable the existing sandbox_agent option for this romance edition only. Run a candidate-only real evidence handoff if the existing service is available, preserving all production state.
- [ ] Record exact integration and production status. Continue the original romance repair after the candidate reference conflict is corrected; deliver only an actually accepted result.

Review focus: actual current vs archived take frame provenance; timestamps and neighbouring state; speaker/audio/duration preservation for multi-turn conversation shots; no silent resetting of exhausted limits; enabling must select the normal managed Agent route, not a separate manual loop.

Validation: frame export test RED (missing frames) → GREEN; romance source-doc export RED (missing original_package) → GREEN; 42 focused and 3202 full tests passed. Independent review fixes complete. Current edition enables existing sandbox_agent option. Real GLM invocation read four actual current JPEGs, then context-compacted and timed out at601s without decision; no production inputs or counts changed. This verifies actual image handoff, not a successful romance correction. No commits/pushes.
