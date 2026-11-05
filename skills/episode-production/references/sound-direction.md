# Sound direction: author once, bind once

Read during creation and whenever production changes speech or sound. The task's
effective profile and approved source govern the mode. New interactive creation
defaults to `h3_speech_policy: no_reference_performance_first`. An explicit
`reference` task or an existing frozen contract retains its chosen mode; a voice
file merely being available never selects it.

## Spoken performance

For no-reference native H3 speech, write every turn's final English performance
in `emotion` while writing the screenplay. Tie useful pitch, pace, volume,
breath or emphasis to that line's event. Ordinary lines need only the detail that
matters; repeating age, gender and speaking rate is not a performance.
The response starts after its cause, with the character's established manner.

For example, a supplied warning after noticing a missing tile can use:

```json
{"emotion": "Her voice drops as she notices the missing tile, then the warning ends firmly."}
```

Keep the exact words in `text`. Put physical acting, camera and the resulting
observation in the shot. A meaningful pause or speech ending can show closed lips
and listening or continued action; do not add filler words or generic waiting.
No-reference mode keeps pictures and speaker IDs but sends no audio reference,
Audio definitions, audio retention or audio-reference summary label.

**Structured H3 requests:** author `h3.shots` with `<DIALOGUE_n>` markers at their
action positions. The host inserts each turn's `emotion` once immediately before
the unchanged utterance. Do not copy that vocal direction into `h3.shots`,
summary or soundscape. A resulting reaction belongs after the marker.

**Complete H3 prompt strings:** include that same bound `emotion` once before
each exact `<d>` utterance in `detailed_description`, outside `<d>`.
The complete-string path is checked, not silently rewritten.

If an adopted source has missing, generic or untranslated delivery, correct its
turns through source-backed input repair before filming. For explicit reference
or historical modes without automatic `emotion` insertion, vocal direction can
be written before the dialogue marker according to the selected contract.
Reference mode preserves the required timbre binding; it does not copy sample words.

The 2026-10-15 short pilot combined front-loaded performance and removal of audio
conditioning, and the user preferred it. This is a selected production convention,
not proof of universal naturalness or perfect cross-clip timbre consistency.

## Per-character voice definition

Give each speaking character one fixed English voice phrase, written once in that
character's `subject_definitions` entry. It covers pitch and resonance, age and
manner, and one stable speech habit. Do not repeat it on every line and do not use
it in place of the per-line `emotion`.

- `<Subject 1> ... Her voice is a warm, clear young adult Mandarin female voice, mid-low pitch, unhurried and conversational.`
- `<Subject 2> ... Her voice is a high-pitched, bubbly, childish girl voice that lands the last word softly.`

Keep the phrase identical in every shot of the episode. The voice phrase says who
is speaking; the per-line `emotion` says what this line is doing. Without an audio
reference this phrase is the only voice identity the model receives. With an
`<Audio>` reference the reference supplies timbre and prosody; the phrase still
keeps a new take from silently re-casting the character.

Evidence (2026-10-31, same service, same scene and line, P00 of the candy short):
adding only `Her voice is a high-pitched, bubbly, childish cute girl voice.` moved
our take from 254 Hz / 5.07 characters per second to 296 Hz / 4.67, and tightened
pitch spread from 8.5 to 8.0 semitones (SD 2.8 to 2.5). The reference take whose
prompt named the voice and an emotion measured 286 Hz / 3.65; that take had a
different line count, so the cross-project number is directional. The 254 to 296
shift is the controlled result: only the voice phrase changed.

## Physical sound and private thought

Write synchronized sounds beside their actual causes. `overall_soundscape`
summarizes environmental sound; vocal direction belongs with the spoken turn.
Speech, private thought and environment sound have separate owners.

For an explicitly chosen `inner_voice_delivery: postmix`, a pure thought clip
has one thinker. Keep its text in structured turns for separate recording;
the visual request has no native speech or Audio definitions. Retain the
recording's own selected voice contract. Do not silently split or demote mixed
private/native dialogue.

## Story music

For `music_mode: timeline`, creation writes `chapter_script.music_plan` with
musical queries, story reasons, shot start/end anchors, entries, withdrawals
and listening gaps. Production uses `select_music` to select real library tracks,
with accepted evidence, attribution, source intervals, gains and fades.
Candidates remain unreviewed regardless of search rank. Use `apply_timeline`
for these cues; reassembly resolves anchors against actual cut durations.

For this postmix music mode, native H3 `music`/`non_diegetic_music` is empty or
`None.`. The music plan is not an instruction to generate the music twice.
Gaps mute only added music, preserving native dialogue and environmental sound.
Use the existing assembly tools for a mixing problem; it does not itself justify
a new video take. Other explicitly native-music tasks retain their approved mode.

ASR checks spoken content; sampled library analysis describes only those samples.
Neither proves vocal naturalness, mix balance or full-film listening. Report that
scope accurately and retain actual listening feedback when supplied.
