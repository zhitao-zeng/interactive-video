---
name: video-repair
description: Diagnose a failed video clip from source, requests, images, actual takes and review history, then propose a bounded model-specific request correction.
---

## Core repair principles

- **Replace superseded logic.** When correcting timing, actions or states, rewrite
  the affected description and remove the conflicting old instruction. Do not keep
  both and append a contrary correction. Superseded clauses must be absent from
  the final candidate: a later paragraph labelled "correction" does not remove
  them, and presumed precedence between paragraphs does not resolve the defect.
  Compatible new detail is allowed; read the complete repaired request as one plan.
- **Use one chronology.** Describe an opening state, ordered actions and an ending
  state. Within a continuous scene, carry that ending state into the next cut until
  an authored action changes it. Do not replay a completed action to establish its
  result again.
- **Repair the evidenced cause.** Compare source, selected references, actual request
  and current take. Correct the earliest supported defect. A bad take does not by
  itself prove its inputs wrong; an old review claim is not current-frame evidence.
  A filename or written observation does not prove that the model received an image.
  For temporal or spatial defects, compare chronological raw/final evidence and
  neighbouring cuts using fixed world landmarks; small contact sheets can hide
  high-frequency movement, and screen left/right does not establish world seats.
- **Distinguish validation from success.** Host `check` validates its reported
  contract and permissions; it does not establish semantic consistency or a better
  film. Before submission, judge whether the complete candidate follows these
  principles. A replacement still needs actual independent acceptance.

Apply these principles even when no matching case is retrieved. Individual cases
are optional evidence for a remedy, not a prerequisite for the general rules.

## Review and repair-decision phase

When input/primary.json has phase=review, independently review only target_clips.
Context clips establish source/state, not an extra repair list. Read one target's
actual frames with its inline source and submitted request before moving on; do
not read every whole-episode document. No old verdict is supplied as an answer.

Judge visible plausibility and causal understanding. Obvious proportions,
unmotivated split views, cloned actors, native dialogue captions, changed critical
clue text and already-completed state changes can require repair. Harmless camera,
ordinary hand/prop detail, small heading lettering or layout variation does not
alone require a retake when the action remains understandable. Physical writing
that carries an actual plot clue differs from incidental small sign lettering.

Read frames in time order. Describe what is visible at each cited time, including
later restoration when present. An object seen early does not prove it persists
through later shots; a skin/light patch is not automatically a device. Occlusion
means uncertain. Viewer-left/right is not anatomical left/right. Transcribe
current pixels cautiously, never supply missing characters from a correct script.

After each target, update output/review.json and its decision before continuing.
Review format is {"clips":[{"clip_id":"...","verdict":"pass|needs_repair|uncertain",
"finding":"observed facts and impact","evidence":[{"image":"current JPEG path",
"seconds":2.0,"observation":"visible fact"}],"findings":[{"kind":"state_continuity",
"must_fix":true,"start_seconds":2.0,"end_seconds":2.0,"evidence":["same JPEG path"],
"summary":"local defect"}],"notes":[],"decision_file":"output/<clip_id>/decision.json"}]}.
Use clue_text, state_continuity, native_subtitles, split_view, proportion,
body_motion, spatial, identity or other for kind. Only actual current-take movie
frames belong in timed evidence; mention reference support separately in notes.
Every cited JPEG must have been opened with Read. A finding's bounds must stay
within its positive observed evidence times, not expand to the full clip. Bounds
describe observed samples, not a claim that every intervening frame was viewed.
Use notes for nonblocking imperfections; pass has no must_fix findings.

Each decision retains action/reason/evidence. Content pass REQUIRES keep even at
zero remaining generations. needs_repair requires a concrete must-fix finding;
zero balance stops generation without turning the content into a pass. uncertain
uses stop with the precise missing evidence. No generation/publication is allowed
in this phase. For edit also save the complete protected request as
output/<clip_id>/prompt.txt; for input save the scoped source_patch.json in that
same directory, following the existing repair contract below. Unrendered proposals
are not successful repairs. Finish with output/review.md. The host may return structural
contradictions to this same session once under the ORIGINAL remaining deadline;
correct the files without changing a content verdict merely to fit a budget.

ASR is transcription evidence only. There is no hearing audit in this phase;
do not infer music, breathing, emotion, or sound quality from text or images.

## Clip repair workflow (repair phase)

The host decoded initial JPEGs in evidence.takes[].frames. Use `python3 tools/video_frames.py take-1` to list them; `--seconds 2.0` actually decodes a new source frame and reports its actual PTS. Batch takes use qualified clip/take names listed in primary. The decoder is staged by the host; do not install or prepare one. Read the returned JPEG, not MP4 with a text reader. Successful consecutive rereads of one unchanged image receive native feedback; use already-read evidence, another view or a precise missing-fact decision instead of retrying an identical Read. Check each take's own request and ASR; reference cards are not generated footage. If images cannot support a decision, output stop with the precise missing evidence. Produce output/decision.json within this attempt even when blocked.

Read input/primary.json first; input/evidence.json retains the full source and history. This task repairs one request within a 600-second execution window. Start with the current frames, actual request, selected reference and inline target/neighbouring shots with their source facts. Historical takes, whole-episode source documents, interaction/overlay files and asset profiles are available for specific unresolved contradictions, not a mandatory reading list. Do not re-read an archived copy of the current render as if it were a prior generation. Once primary evidence supports edit, retake, keep or stop, write the final output immediately; do not spend the window exhausting auxiliary files. Use the shared request_rules supplied by the production compiler. Compare the actual picture with the intended shot; do not merely repeat the reviewer. A positive reference check is not proof of correct plot or object counts. Treat proposed generation causes as hypotheses unless supported by a direct input contradiction or controlled evidence; an omitted descriptive phrase alone does not prove why the model erred. Category changes can describe the same visible fault.

Return output/decision.json with action (edit, input, retake, keep, stop), reason, and evidence (a list of input file paths plus concise observations). Read provider and request_field in input/primary.json. For edit also write output/prompt.txt: the complete final H3 English request for h3, or the existing compiled Chinese request for sd25. Never transfer H3 tags or section grammar into SD. Replace conflicting text rather than appending the error description. Describe the target visible state concisely instead of listing every prior defect as a prohibition. Organize each shot as setup, all ordered bound utterances and intervening actions, and the meaningful result; preserve every turn in a continuous conversation; do not narrate finishing speech before the dialogue or add a generic hold to fill time. Correct wearing versus independent objects, visibility and physically observable states using the source and actual references; do not invent a new scene. Unsupported source changes still require stop.

For H3 only, input can correct a proven source prop/wearing binding mismatch instead of disguising it as a prompt edit. Write output/source_patch.json as {"shots":[{"index":<current stage index>,"props":[<registered prop name>],"wears":{<existing wearer>:<registered prop name>}}]}; omit unchanged fields. Select only current failed-clip stages and existing story_bible prop names/aliases, using source facts and actual references as evidence. The host uses the existing source compiler, preserves cut timing, voices and dialogue, checks the new request and publishes a normal RepairProposal. This contract changes only props/ wear relationships, not cast, scene, events, dialogue or duration. It does not generate or redraw assets.

For H3 edit, preserve the six H3 sections, subject/audio labels and their reference mappings, retention labels/modes, exact dialogue and speaker/audio bindings, shot count and timing. All directions outside the existing bound <d> dialogue remain English, including native-sign directions: do not paste Chinese sign text into them or add dialogue to show a sign. Use actual supplied visual references, English directions and reasonable source-preserving framing instead. You may clarify identity/clothing/state descriptions in subject_definitions and retention_analysis using the reference evidence, and revise shot directions and audiovisual corrections, but not add dialogue, change story events or change the scene's factual order. Speech directions can specify when the existing utterance starts/stops and who stays silent; they are not new dialogue or a reason to invent visual content.

For SD2.5, preserve every non-stage line exactly, including reference definitions, total duration and style. Preserve each stage header, its entire 声音： clause, all dialogue in braces and all @图片/@音频 tags. Only clarify visible actions within the existing stage lines using retained source and image evidence. If the necessary fix requires changing a protected binding or source fact, stop and identify it; do not disguise a source correction as a prompt edit.

For H3 input, an offered reference_choices entry can also replace that same location's picture with an already supplied detail view. Use the existing locations contract in output/source_patch.json: {"locations":{"<current location name>":{"reuse_asset_id":"<offered asset_id>"}}}. Select only choices supplied for this finding; their paths identify existing frame-derived assets, not permission to generate images. The host keeps scene identity, picture numbering, speech and timing, then recompiles against the selected detail picture. Use the offered scope: a reliable cropped upper sign panel does not prove an unreliable omitted lower row has become legible. Existing later narration may carry that source fact when the approved repair scope allows it.

An exact camera distance/path, harmless prop-design difference or ordinary background person is not alone a retake reason when the scene, people and causal story remain clear. If a failure flag is paired with observations describing a natural image, propose keep/recheck unless you independently observe another concrete defect in current frames. Cite that actual defect instead of inventing a reason to reproduce every storyboard detail.

Preserve clause-specific H3 vocal performance immediately before its corresponding bound utterance, including the original intention, breath, emphasis and meaningful speech ending. An explicitly empty audio-reference selection stays empty in the actual references and request; repairing a picture does not authorize adding a voice sample or flattening the supplied delivery.

Remove native floating dialogue captions that would duplicate the host's subtitle track. Preserve approved physical signs, paper notes, archives and phone text; caption removal does not mean deleting all scene writing. For a confirmed critical clue-text defect, read the current pixels literally and keep its source meaning without adding a meeting, punishment or conclusion. Use supplied reliable views and source-preserving framing; never claim an omitted or unreadable row has become correct, or add postproduction masks/lettering.

retake means the input is already correct and one new seed is justified. keep proposes a false-positive recheck; the pipeline must independently verify it. stop means there is no supported repair. Do not force an edit for every clip.

Only work on /work copies. Do not call generation services, modify budgets, publish or attempt to access production paths. The pipeline owns actual generation and its existing total-attempt cap. No automatic extra retry follows a rejected candidate. Do not infer electrical sound causes visible arcs: that hypothesis was not supported by the controlled examples.

For romance_conversation_v1 SD requests, preserve all nonvisual lines exactly (including references, speakers, dialogue, performance and sound). Only the four existing 【开始状态】/【本段机位】/【动作与交流】/【结束状态】 lines may change; retain their headers and order.

## Shared stability experience

In the recorded Guangming754 repair, the street reference was replaced by the
existing rooftop reference and an over-shoulder/frontal conflict was rewritten
as a same-camera, single-person three-quarter front shot. One new take was then
checked for native-frame motion, visible bodies and the remaining media gates.
This composite result does not establish that one camera phrase cures shaking.
In722 and752, fixed framing with localized subject movement yielded steadier
takes;722 also required a further repair after a steady take introduced a cloned
companion.752's formal targeted review remained incomplete, and722 retained an
unresolved background-prop finding. Recorded improvement is not universal or
whole-episode acceptance. Choose the camera from the current task and evidence;
historical examples do not override an approved moving shot or grant a retake.
