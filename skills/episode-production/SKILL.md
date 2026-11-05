---
name: episode-production
description: Create an interactive short episode from an original brief or source package, or produce and repair approved shots using actual assets and the supplied host tools. Follow the current phase's contract.
---

# Episode production

Read the current manifest and task; they define the phase and authority.

- For `phase: creation_only`, read [the creator contract](references/creative-authoring.md), its required short indexes and original sources. Write the complete screenplay/storyboard/interaction/spatial candidate. Production/H3 and repair references are available for later phases or current ambiguities.
- For `phase: script_preparation`, read [the output contract](references/output-contract.md). Compile approved shots into `output/requests.json`, preserving camera, words, ownership, timing, groups and reference order. This phase has file tools only.
- For production or repair, read [the production workflow and full repair rules](references/production-workflow.md) before using host operations. It contains the complete source/script authority, asset, sound, async handoff, review, budget and finish contract.

## Read efficiently

With shell tools, use [the bounded reader](scripts/read-text.py):
`python3 .claude/skills/episode-production/scripts/read-text.py PATH`.
One result contains at most 6,000 UTF-8 text bytes plus character-offset metadata. Continue with
`--start END` until `complete` is true. For details, use `--section "Exact heading"`.
Do not concatenate page outputs. With Read tools, use small windows and continue
only the missing portion. Use the listed paths and reuse unchanged same-session
reads. Original source text must still be read completely; it is never shortened.

## Shared principles

- Replace contradictory old instructions when repairing; an appended correction does not remove obsolete clauses.
- Use one chronology. Carry established actor, object and location states across cuts; completed actions happen once.
- Diagnose from actual source, supplied references, submitted requests and current takes. A prior claim or filename does not prove what the model saw. Use world landmarks for movement and position.
- A host format check does not establish artistic success. Missing/failed evidence remains unresolved; keep real budgets and history. The host owns generation and independent acceptance.

For scene-driven coverage, start with [camera selection](references/camera-selection.md).
Use [dialogue craft](references/dialogue-craft.md) for dialogue creation or permitted
wording revision and [sound direction](references/sound-direction.md) for performance,
native speech and postmix music. Protected script words remain unchanged. Use the phase contract
and relevant details rather than loading every reference.
