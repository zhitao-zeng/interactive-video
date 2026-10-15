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

## Clip repair workflow

The host already decoded the videos. Start by reading the current take's JPEGs in evidence.takes[].frames; seconds are sample times within that take. Use `python3 tools/video_frames.py take-1` or `--seconds 2.0` to locate supplied frames. The time selector returns the nearest supplied sample, not a new exact-time extraction. Check each take's own request and ASR; reference cards are not actual generated footage. Do not use Read on MP4, install packages, probe system video libraries, or spend the repair window preparing tools. If images cannot support a decision, output stop with the precise missing evidence. Produce output/decision.json within this attempt even when blocked.

Read input/primary.json first; input/evidence.json retains the full source and history. This task repairs one request within a 600-second execution window. Start with the current frames, actual request, selected reference and inline target/neighbouring shots with their source facts. Historical takes, whole-episode source documents, interaction/overlay files and asset profiles are available for specific unresolved contradictions, not a mandatory reading list. Do not re-read an archived copy of the current render as if it were a prior generation. Once primary evidence supports edit, retake, keep or stop, write the final output immediately; do not spend the window exhausting auxiliary files. Use the shared request_rules supplied by the production compiler. Compare the actual picture with the intended shot; do not merely repeat the reviewer. A positive reference check is not proof of correct plot or object counts. Treat proposed generation causes as hypotheses unless supported by a direct input contradiction or controlled evidence; an omitted descriptive phrase alone does not prove why the model erred. Category changes can describe the same visible fault.

Return output/decision.json with action (edit, retake, keep, stop), reason, and evidence (a list of input file paths plus concise observations). Read provider and request_field in input/evidence.json. For edit also write output/prompt.txt: the complete final H3 English request for h3, or the existing compiled Chinese request for sd25. Never transfer H3 tags or section grammar into SD. Replace conflicting text rather than appending the error description. Describe the target visible state concisely instead of listing every prior defect as a prohibition. Organize each shot as setup, all ordered bound utterances and intervening actions, and the meaningful result; preserve every turn in a continuous conversation; do not narrate finishing speech before the dialogue or add a generic hold to fill time. Correct wearing versus independent objects, visibility and physically observable states using the source and actual references; do not invent a new scene. If the source itself is wrong and cannot be faithfully expressed, choose stop and explain the required source change.

For H3, preserve the six H3 sections, subject/audio labels and their reference mappings, retention labels/modes, exact dialogue and speaker/audio bindings, shot count and timing. You may clarify identity/clothing/state descriptions in subject_definitions and retention_analysis using the reference evidence, and revise shot directions and audiovisual corrections, but not add dialogue, change story events or change the scene's factual order. Speech directions can specify when the existing utterance starts/stops and who stays silent; they are not new dialogue or a reason to invent visual content.

For SD2.5, preserve every non-stage line exactly, including reference definitions, total duration and style. Preserve each stage header, its entire 声音： clause, all dialogue in braces and all @图片/@音频 tags. Only clarify visible actions within the existing stage lines using retained source and image evidence. If the necessary fix requires changing a protected binding or source fact, stop and identify it; do not disguise a source correction as a prompt edit.

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
