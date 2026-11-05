# Unified creator authoring contract

Use this contract when `input/manifest.json` has `phase: creation_only`. One
persistent author owns the adaptation or original work. This phase provides file
tools only. Write creative candidates in `/work/output`; media production and
adoption are later operations. Read this contract before the ordinary episode
render contract.

## Read the actual input

Read the manifest, `input/brief.md`, every original text document, and the supplied
character/design images in `asset_catalog`. The manifest's `required_reads` also
identifies the current authoring/voice guidance, camera library, spatial blockout,
selected genre, repair playbook and six-method library. Read those resources, then
choose useful methods for this story. They are one author's tools, not six rewrites.
Record chosen methods and their concrete purpose in `output/creative-notes.md`.
Read [shared dialogue craft](dialogue-craft.md) for every genre. Shape what the
character says in context before designing how the voice delivers it; do not use
performance adjectives to cover a lecture-like line.
For shot design, use [camera language](camera-language.md) and
[spatial blockout](spatial-blockout.md). Select the current genre's guidance:
[travel](genres/travel.md), [romance](genres/romance.md), or
[suspense](genres/suspense.md); their linked resources are for relevant cases,
not an instruction to read every genre.

For adaptation, retain the package's identities, causal events, established
knowledge, key objects and available interactive destinations. Task instructions
decide whether wording, order or duration can change. Separate original facts,
the character's inference, staging choices and unknown answers. Every filmed shot
has a short, exact `source_quote` found in an original text document; the quote
locates its basis and does not prove an invented interpretation. Retain complete
coverage of essential facts without mechanically copying the original shot count.
For an original brief, build an explicitly original setting and causal story;
record assumptions and use them consistently. Do not invent an external source.

Use the supplied medium, length and interaction policy. A package's old timing or
burn-in instructions do not override the current task. Preserve original assets.
Where dimensions or off-camera geometry are unknown, mark a staging assumption.

## Write the complete episode

Write `output/episode.json` as one object with:

- `title`, `logline`, `adaptation_notes`: the episode and meaningful author choices.
- `chapter_script.shots`: complete chronological screen order. Each shot has
  consecutive `index`, `scene_id`, `location`, `duration_seconds`, `purpose`,
  `characters`, `in_frame`, `start_state`, `event`, `end_state`, `camera`, `turns`,
  `sfx`, `audio_plan.music_cue`, and the adaptation's `source_quote`.
- Each turn has `speaker_name`, exact `text`, `delivery_mode` (`visible_dialogue`
  or `offscreen_dialogue`) and a specific `emotion`/delivery description. Use
  `turns: []` for a genuinely silent shot. Design speech and its observable reaction
  together; the later H3 compiler places the English performance before `<d>`.
- `clips`: contiguous generation groups, each with `shot_indexes` and
  `request_seconds` equal to those shots' total. H3 groups contain 1–6 adjacent
  shots and total 4–15 seconds, also recorded in `generation_group_limits` for new
  inputs. An individual shot can be shorter than four seconds; the group minimum
  does not turn every insert or reaction into a long take. Group suitable short
  cuts together and use long takes when their ongoing action earns the time.
  These are future groups,
  not generated videos; final H3 prompts and asset binding are not required here.
- `interaction`: `question`, `options` and `selected: null`. Each option has `id`,
  full `label`, an already-established `reason`, and its `next_step`. Keep required
  choices and the world's unresolved answers. The audience's choice is still open.
- `spatial_plan.scenes`: locations where blockout helps. Each has `scene_id`,
  `anchors`, `placed`, `shot_indexes`, `assumptions`, and a concrete camera/visibility
  plan. `placed` means bodies present in the space; `in_frame` is per-shot visibility.
- `self_review`: source/continuity/performance/interaction review, actual corrections,
  and `open_issues`. This is a script review, not a claim to have heard or seen a film.

World/choice cards are specified by the manifest and are outside video shot totals.
Keep all options readable for the complete specified ending-card duration. Opening,
action and reaction timing serve discoveries and decisions; avoid equal-length
introductions, repeated explanations or padding to meet the length target.

Use camera, acting, physical sound and music to express different information.
Motivate a move with a change in attention, distinguish camera from actor movement,
and carry prop/geography/light state across cuts. Respect the selected native-voice
policy: no reference audio is used merely because an old voice sample exists.
Closed-mouth observation follows the utterance; no dialogue is added to fill it.
Blockout plans assist physical continuity; they are not rendered or approved images.

## Finish within this creation task

Before finishing, read the complete draft against the original and current brief.
Correct causal gaps, early revelations, repeated actions, missing choices, implausible
travel, inconsistent object state and speech that cannot fit. Keep the story complete
and the exact source quotes useful. Record remaining production risks explicitly.
Write the whole JSON rather than a proposal to continue; it may be assembled through
several file edits. The host will validate and format readable script/storyboard,
choice and spatial-plan documents from this single source of truth.

On a returned validation issue, continue this same author's draft and session.
Use the current output and named errors, preserving valid choices and all previous
attempts. This phase has no render/apply/media tool and grants no production take.
