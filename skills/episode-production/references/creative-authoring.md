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
selected genre and six-method index. Read those required resources and all original
sources. The index offers the method choices; `manifest.method_library` points to
the complete library when a selected method needs its detailed procedure. These
are one author's tools, not six rewrites. Production/H3 and repair references are
available for their later phases or a concrete current ambiguity, not mandatory
creation reading.
Record chosen methods and their concrete purpose in `output/creative-notes.md`.
Before drafting each scene, decide what the audience learns, who knows it, and
which information remains hidden. In those same notes, explain the key viewing
choices using current shot numbers: why this position, move or cut serves that
change. Implement the choices in each shot's `purpose` and `camera`; notes alone
are not the design. During self-review, compare the actual camera text with the
intended reveal, reaction and spatial continuity, correct mismatches, and identify
the shots changed. Camera variety and director-name counts are not acceptance.
Read [shared dialogue craft](dialogue-craft.md) for every genre. Shape what the
character says in context before designing how the voice delivers it; do not use
performance adjectives to cover a lecture-like line.
Read [sound direction](sound-direction.md) and follow `manifest.profile`. New
interactive tasks default to no audio reference with English per-turn performance.
Write that performance in `emotion` now; the production compiler binds it once.
For shot design, start with [scene camera selection](camera-selection.md) and
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

Use the known manifest paths instead of repeated directory inventories. Read a
single short resource or a named section in each tool result. For a long document,
use small windows (about 2,000 characters or 40 lines); after truncation, continue
from the missing range instead of restarting. Reuse unchanged same-session reads.
Read original source documents completely, with continuation windows as needed;
shorter instructions never authorize dropping source events or evidence.

## Write the complete episode

Write `output/episode.json` as one object with:

- `title`, `logline`, `adaptation_notes`: the episode and meaningful author choices.
- `chapter_script.shots`: complete chronological screen order. Each shot has
  consecutive `index`, `scene_id`, `location`, `duration_seconds`, `purpose`,
  `characters`, `in_frame`, `start_state`, `event`, `end_state`, `camera`, `turns`,
  `sfx`, `audio_plan.music_cue`, and the adaptation's `source_quote`.
  `characters` and `in_frame` are JSON arrays of actor names. `in_frame` is a
  subset of that shot's `characters`; an empty/POV frame may use `[]`. Put prop and
  scenery names in `scene_objects`/`props`, and full descriptions in shot text, not the actor
  roster. A phrase such as “the falling backpack” is not a character name.
  `camera` must be one nonempty string, not an object with `start`, `trigger`,
  `actor_path`, `camera_path`, `focus`, `endpoint` or `hold` keys. Keep all authored
  execution details in that string, including the purpose, start, trigger, each
  path, focus, endpoint and hold/cut condition; changing the JSON shape must not
  drop those details. `props` and `scene_objects` are lists of name strings,
  such as `["backpack", "mooring post"]`, or `[]`; put full descriptions in shot
  text. Empty shots and end cards need no invented actors or placements.
- For a task selecting `h3_input_mode: first-frame` or `sd_input_mode: first-frame`, author each shot's
  `opening_frame: {image_prompt, visible_characters, reference_subjects}` now.
  Describe one static composition at time zero: camera position, visible stable
  identity/clothing, spatial anchors, light and the exact opening prop state.
  Keep subsequent movement and arrivals in the shot's event/video direction.
  Do not copy the complete video `camera` sentence into `image_prompt`: state
  only the camera position and framing at time zero. Construct `start_state`
  from what is true before the first action, rather than copying its completed
  outcome or `end_state` into the opening.
  POV can have `visible_characters: []`. Name the logical scene/character sources;
  apply the selected style to the composition, materials and light. Production
  binds actual card paths in upload order and labels each image's name and scope.
  Its image API suffix contains appearance cues; the complete frozen style remains
  available to the author and review. The compiler does not rewrite your facts. Do not
  defer this creative prompt to production. Opening repairs update this source
  field through the existing repair tool; their previous images/history remain.
  Follow the [official GPT Image prompting principles](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide#2-prompting-fundamentals):
  use short sections for the scene, visible subjects/current state, camera and
  essential constraints. Keep whole-cast seating maps in `spatial_plan`, not in
  a two-person image description; keep later events and camera movement in video
  direction. Before simplifying, identify the existing static continuity facts:
  clothing, ownership, object positions, fixed attachments, repaired/damaged states
  and established geometry. Carry every relevant fact into the revised prompt;
  avoid inventing a new layout or deleting a cloth, fastener or support merely to
  save words. Details outside the crop retain their state without needing to be
  forced into view. Name logical `reference_subjects`; production supplies final
  image numbers, so do not guess numbering from a book-wide asset list.
- Each turn has `speaker_name`, exact `text`, `delivery_mode` (`visible_dialogue`
  or `offscreen_dialogue`) and a specific `emotion`/delivery description, in
  English for `no_reference_performance_first`. Use
  `turns: []` for a genuinely silent shot. Design speech and its observable reaction
  together; the later H3 compiler places the English performance before `<d>`.
- `clips`: contiguous generation groups, each with `shot_indexes` and
  `request_seconds` equal to those shots' total. H3 groups contain 1–6 adjacent
  shots and total 4–15 seconds, also recorded in `generation_group_limits` for new
  inputs. An individual shot can be shorter than four seconds; the group minimum
  does not turn every insert or reaction into a long take. Group suitable short
  cuts together and use long takes when their ongoing action earns the time.
- For `music_mode: timeline`, also author `chapter_script.music_plan` with
  `cues` and `silences`. A cue contains `id`, a musical `query`, `reason`, exact
  `source_quote`, and `start`/`end` anchors such as
  `{"shot":3,"edge":"start","offset_seconds":0}`. Plan entries, withdrawals,
  fades and listening gaps with the drama now; production chooses actual tracks
  from the library and binds anchors to measured cuts. Record silence windows
  with the same anchors/reason/source evidence. They mute only added music.
  This is postmix music: video prompts keep non_diegetic_music=None and preserve
  native dialogue/environment sound. Existing source-required listening gaps
  remain protected; revising those gaps requires revising the source music plan.
  These are future groups,
  not generated videos; final H3 prompts and asset binding are not required here.
- `interaction`: `question`, `options` and `selected: null`. Each option has `id`,
  full `label`, an already-established `reason`, and its `next_step`. Keep required
  choices and the world's unresolved answers. The audience's choice is still open.
- `spatial_plan.scenes`: locations where blockout helps. Each has `scene_id`,
  `anchors`, `placed`, `shot_indexes`, `assumptions`, and a concrete camera/visibility
  plan. `placed` means bodies present in the space; `in_frame` is per-shot visibility.
  A genuinely unoccupied insert or flat card may use `placed: []`; offscreen speech
  alone does not put a body in frame. Do not invent occupants or text placements to
  satisfy a nonempty-list check. If visible bodies are present, provide their blockout.
- `self_review`: source/continuity/performance/interaction review, actual corrections,
  and `open_issues`. This is a script review, not a claim to have heard or seen a film.

When `task.production_handoff` is true, also provide `production_design`:
`palette`, `characters` (name/appearance/wardrobe, plus useful role/gender/age),
`locations` (name/description), `props` (name/appearance/material/owner), and
`continuity_rules`. Cover each named visible or speaking character, shot location
and registered prop. Design concrete stable appearances and readable geometry;
the host does not invent these from names. Use exactly the screenplay's names.
Each shot includes `shot_scale` matching its camera. Keep each generation group
in one location. For original work, music source quotes
come verbatim from the authored events, dialogue or sounds. The host converts
this approved creative candidate into the existing material structure, preserving
words, cuts, camera, openings and music, then runs assets and production separately.

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
Repair the reported fields and necessary dependencies, not another full editorial
pass. Reuse already-read sources and consult only the relevant ranges. When a shot
is split or renumbered, update clips and music anchors in the same revision. Record
actual fixes in self_review and finish; repeated unresolved errors stop automatic
continuation and retain the draft and remaining allowance.

When the task selects `dialogue_source`, `input/protected-dialogue.json` contains
every marked source speaker, including temporary/offscreen roles outside the cast
cards. Preserve words, owners and order. A long source utterance may span shots;
the host compares character ownership against the original document before handoff.
