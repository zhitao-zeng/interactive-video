# Authoring and reference scope

Read this for source-mode adaptation or a concrete ambiguity in an approved shot.
The manifest decides what can be authored. In script mode, preserve the saved
screenplay and clip plan and report an incompatible input through the host.

## Story and identity

Preserve the chain that makes the ending intelligible: the initiating question or
problem, the relevant answer or obstacle, the resulting decision and its consequence.
An important explanation needs its planned audible words or visible event; a
description such as “he explains it” is not dialogue. Keep who knows, hears, thinks
or speaks each fact consistent through the episode. Source mode may compress or
rewrite dialogue with the same meaning; script mode keeps the approved text exactly.

Use the original source together with supplied reading and confirmed aliases.
A name fragment, title or resemblance does not establish identity. If the source
and a supplied catalogue disagree, cite the source and current material for the
host rather than assigning the act to the nearest available character.
Select supplied segment IDs for traceability; source evidence describes the
original passage, while the screenplay owns the permitted filming decisions.

`characters` names the identified characters; `in_frame` names those actually visible
in the current crop, including visible listeners. A mention, same room, action
target or offscreen voice does not require an on-screen body. `extras` describes
unregistered people or animals, `props` registered objects, and `scene_objects`
temporary objects. Actions may target these, the actor themselves or an empty
target when no recipient is established. Preserve these meanings rather than
turning an animal, armor or environment object into an available human actor.

## Actual pictures, wearing and framing

Inspect the actual selected images. Ordinary character cards supply identity and
stable design; the authored shot supplies pose, camera, crop and current state.
Multiple views of the same person define one Subject: explain which picture
supplies face/hair and which supplies body proportions, clothing or another view.
Picture count and Subject count are not body counts.

`wears` attaches equipment to its wearer; the wearer and worn copy form one body.
`independent_props` identifies a separately present physical copy when the story
requires it. A card containing a face or wearer cannot be relabeled as an empty
suit merely by choosing an object role. State the actual faceplate/helmet state
and its authored change. A card's default open faceplate does not override a
planned closed state, and a cropped or obscured face is not an absent character.

Describe the intended visible contents, crop and eyeline affirmatively. Preserve
identity and material within the crop; details hidden by framing remain unobserved.
A shared scene-state image can also contain people and objects that this crop
does not show. An explicitly supplied scene-state image provides starting-state
evidence, while the current action describes subsequent changes. Ordinary Ref2VA
pictures remain references; concrete first-frame/keyframe conditioning requires
explicit host/provider support. Use the existing references and camera unless
the input authority permits another selection; per-shot keyframes are not a default.

## Events, sound and closure

`actions` and `event` describe the same event, not two performances. Write the
action once in causal order. A repair integrates the correction by replacing
affected conflicting instructions, with consistent sound and ending state.
Keep current arrivals/exits, visible bodies, wearing and persistent object state
coherent at the cut. A nearby location or reverse shot alone does not establish
continuous time or a new entrance. In source mode, author separate shots for a
long exchange or a delivery change; the host does not creatively split a shot.

Physical sound needs a current action or environment source. Place an action
sound's onset and ending beside that action in `detailed_description` and summarize
continuous ambience in `overall_soundscape`. Vocal emphasis or an emotional pause
belongs beside its dialogue. An action change also updates obsolete effects and
their sound cues. Use `None.` for an empty audio section. Invented equipment
abilities, electrical effects, machinery or speakers are not atmosphere.

Retain the current result through the cut rather than replaying the event as an
ending. Source-mode cuts fit the planned speech and physical acting together;
they need not count simultaneous action and dialogue twice or fill time with
unmotivated pauses. Existing split ranges in script mode remain authoritative.

## Native H3 speech performance

Describe the person's response to the current event. Establish the starting state
and let a discovered fact or another
person's words trigger the next change. Bind useful pitch, pace, volume, breath,
emphasis and facial changes to the relevant clause; ordinary lines need only the
detail that matters. Preserve each character's manner and the genre. A travel
update can move from easy sharing to restrained doubt without adopting an argument's
crying or shouting. Later reactions begin after their trigger.

Prefer this local order: English delivery and acting → the exact bound utterance
or `<DIALOGUE_N>` → the authored result. Put the delivery immediately before the
utterance, outside `<d>`. Where a pause or speech ending matters, describe closed
lips and the actual observation, listening or continuing action. Fit speech and
reaction into the supplied duration; add neither filler words nor generic waiting.
Move existing delivery into one coherent direction instead of appending a competing
second performance description.
For example, a supplied rule-reading shot can use:

```text
Her voice starts level and conversational. At the obligation she briefly hesitates,
gives the key word slightly more weight, and ends with a quieter questioning rise.
<DIALOGUE_1>
She closes her mouth and continues reading the sign.
```

The marker stands for the complete approved line, not permission to rewrite it.
Keep an authored POV or offscreen voice; this method does not require a new facial
close-up, a fixed shot length, or a dramatic change on every sentence. Use affirmative
actions instead of stacking prohibitions or putting performance tags inside dialogue.

Choose audio conditioning from the task, not from the mere existence of a voice
file. For native H3 conversation with no required locked voice, a sample without
audio reference is a useful option. When selected, the actual reference-audio list
and submitted audio conditions must both be empty: omit Audio definitions, retention
and the audio-reference summary label while keeping dialogue, pictures and speaker
IDs. Describe a consistent voice within the clip; timbre across clips can drift.
Retain a required locked voice or audio-first/postmix workflow unless a change is
authorized. If the host still fixes audio selection, use its supported correction
path; changing prose alone does not remove an actual audio input.

The 2026-10-15 nine-second H3 pilot combined front-loaded performance with removal
of audio conditioning, and the user preferred the result. This supports trying that
combination, not a universal ban on voice references or proof of either factor alone.
ASR measures spoken content; naturalness needs listening feedback. Preserve the
existing music/physical-sound plan and trial history when comparing performances.
