#!/usr/bin/env python
"""Produce the final H3 shot directions from the current episode's source, plan and references.

One model response owns visual direction, vocal performance, physical sound and current corrections.
The compiler binds protected dialogue and reference identifiers into the existing six-section format.
Malformed output uses the existing bounded retry; unresolved source conflicts return to preparation.
No new video is submitted here.

    build_h3_prompts.py <novel> [--novel-dir DIR] [--chapters 1-50,60] [--workers N] [--limit N] [--rebuild]
"""
import novel_manga.episodes as ep_names
from novel_manga.application.configuration import project_root
import argparse
import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = project_root()
from novel_manga.application.configuration import h3_translation_endpoint
from novel_manga.llm.client import ask_json, image_part
from novel_manga.application.profiles import h3_compile_inputs, h3_prompt_outdated, h3_source_digest, h3_stamp
from novel_manga.application.production.runs import corrections

from novel_manga.util import atomic_write_json, read_json  # noqa: E402
from novel_manga.story.h3 import request_issues, stages_of, subject_lines, asset_subjects, compose, tag_names, CJK, CHARACTER_TRAIT, REQUEST_CONTENT_RULES

ENGLISH_CHARACTERS = r'[^\u4e00-\u9fff]'
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots", "soundscape", "conflicts"],
          "properties": {"shots": {"type": "array", "items": {"type": "string", "minLength": 1, "description": "One chronological paragraph per shot, preserving dialogue markers.",
                         "pattern": '^' + ENGLISH_CHARACTERS + '*$'}},
                         "soundscape": {"type": "string", "pattern": '^' + ENGLISH_CHARACTERS + '*$'},
                         "conflicts": {"type": "array", "items": {"type": "string"}}}}
ASK = (
    "Write one concise chronological English paragraph per numbered shot. "
    "Every shot needs a nonempty paragraph, including an empty setting with no named character. "
    "Describe the framing and starting state, then unfold each action once. "
    "State the result only when it adds information; do not repeat the action as an ending. "
    "Do not copy input field labels, shot numbers or item headings. "
    "Place each supplied <DIALOGUE_N> marker verbatim, exactly once and in order, where the utterance occurs. "
    "The marker is replaced with the full bound utterance, speaker and voice reference. "
    "Write its vocal delivery once as a separate English sentence immediately before it, combining the supplied emotion "
    "with the dramatic intention; do not also narrate speaking/finishing or another vocal tone. "
    "A recording camera or phone is the viewpoint, not an automatically visible prop; preserve authored off-screen equipment state. "
    "A silent_action interval must explicitly contain no speech, even when another stage in the same request has dialogue. "
    "Express the specific spoken reaction: what word receives emphasis, how breath and pace change "
    "at the authored discovery, and how the sentence ends. Preserve the same vocal identity while "
    "following each line's supplied intention; do not turn observation, alarm and reflection into "
    "one uniform recital or add pauses between every word. "
    "Use only supplied Subject tags and preserve visible versus off-screen people, wearing versus "
    "independent objects, anatomy and individual body counts. "
    "Write physical audio only in soundscape: include every authored action sound with its shot number "
    "and onset/stop relative to the action, followed by continuous ambience. Do not repeat these sounds "
    "in the visual paragraph. The source sound field may contain a misfiled performance cue: "
    "put a pause on an emphasized word beside its dialogue marker, never convert it into an opening delay. "
    "If no physical sound is specified, soundscape is None.; do not invent city noise or room hum. "
    "Vocal pauses are not physical sound sources. Add no unplanned sound, action, person, effect or decorative style. "
    "All output is English; return dialogue markers instead of copying the spoken words.\n\n"
)

INPUT_RULES = REQUEST_CONTENT_RULES + (
    "source_quote and source_excerpt are selected excerpts, not an exhaustive dialogue transcript. "
    "Use the supplied source stage turns as that stage's dialogue evidence, and match every native slot "
    "to its supplied speaker, text and delivery; a slot alone is not proof of source support. "
    "Keep real disagreements in conflicts without inventing, dropping or reassigning dialogue. "
    "If sources leave an unresolved factual conflict, return it in conflicts rather than choosing an identity, "
    "body count or new event. Otherwise conflicts is empty. "
    "For audio_delivery=silent_visual, thoughts or speech in the source and action metadata are mixed "
    "after rendering: no dialogue markers or speech lip-sync in the visual take. "
    "Keep the planned camera and physical acting. Native dialogue slots are the only utterances generated here. "
    "Keep emotional intention and voice behavior distinct: a controlled threat can stay quiet. "
    "A neutral default emotion does not erase an explicit dramatic intention. "
    "Do not add crying, laughter or shouting unless explicitly requested. "
    "Do not discuss translation, the task or missing information in shots or soundscape.\n"
)

POST_OVERLAY_RULES = (
    "Text listed in postproduction_overlays is added by the delivery compositor, even if a source action "
    "mentions that subtitle. It is not generated text in the photographed scene. Preserve the underlying "
    "action and native dialogue, but omit these overlay captions from shot paragraphs rather than copying "
    "their Chinese strings or asking the video model to draw subtitles. "
)


TRIES = 3  # translations per clip before it is left without an English prompt


def warn(clip: dict, message: str) -> None:
    print(f"  {clip.get('clip_id', '?')}: H3 prompt {message}", file=sys.stderr, flush=True)


def request_context(clip: dict, episode_dir: Path | None, *, source_script=None) -> tuple[dict, list]:
    """Read only the source/stages and exact reference images owned by this episode."""
    if episode_dir is None:
        return {}, []
    from novel_manga.util import read_json
    directory = Path(episode_dir)
    ids = {str(i) for i in clip.get('segment_ids', [])}
    rows = read_json(directory / 'segments.json', [])
    source = [r for r in rows if str(r.get('segment_id')) in ids]
    script = source_script if source_script is not None else read_json(directory / 'chapter_script.json', {})
    indexes = set(clip.get('shot_indexes', []))
    fields = ('origin_index', 'wears', 'props', 'scene_objects', 'source_quote', 'turns', 'camera', 'sfx', 'start_state', 'end_state', 'avoid')
    if 'action_bindings' not in clip:
        fields += ('actions',)
    # The plan addresses prepared stage indexes, not origin_index retained from an older split.
    stages = [{'stage_index': s.get('index', i),
               **{k: s[k] for k in fields if k in s}}
              for i, s in enumerate(script.get('shots', []), 1) if s.get('index', i) in indexes]
    refs = [r for r in clip.get('references', []) if r.get('role') != 'voice']
    images = [image_part(directory.parent / r['path'], 1024) for r in refs]
    context={'source_excerpt': source, 'source_stage_bindings': stages,
            'current_stage_parts': clip.get('shot_parts', []),
            'pictures': [{k: r[k] for k in ('name', 'role', 'view', 'wearers', 'aliases') if k in r}
                         for r in refs]}
    overlays=[row for row in read_json(directory/'overlays.json',[]) if row.get('index') in indexes]
    if overlays:context['postproduction_overlays']=overlays
    return context,images


def convert(clip: dict, tries: int = TRIES, note: str = "", *, episode_dir: Path | None = None,
            problems: list | None = None, source_script=None, source_conflicts: list | None = None) -> bool:
    """Generate one authoritative shot/sound/delivery response, then bind protected text and references.

    Incomplete output is retried within the existing budget; source conflicts return to the caller. Never
    patched with a second independently written correction or vocal-performance request.
    """
    from novel_manga.application.profiles import h3_correction_parts
    prompt, existing_note = h3_correction_parts(clip)
    note = str(note or "").strip() or existing_note

    def reject(message):
        if problems is not None:
            problems.append(message)
        warn(clip, message)
        return False

    def merge_correction_into_prompt() -> None:
        """The correction stops being a parallel truth (audit #5): once its English is in, the
        Chinese prompt carries it too, and the note is spent - the plan is the one source, the
        cache key moves with it, and no stage of the pipeline keeps quoting an old picture."""
        if not note:
            return
        merge = f"\n【导演修正】{note}"
        clip["prompt_before_correction"] = prompt
        clip["prompt"] = prompt + merge
        clip["prompt_correction_merged"] = True

    # The stamp this conversion writes is versioned ("2:<digest>") and covers the reference
    # seating; a bare stamp from before is compared under the old formula instead.
    from novel_manga.story.voice_delivery import native_speech_errors, native_speech_selected
    if speech_errors := native_speech_errors(clip):
        return reject('; '.join(speech_errors))
    from novel_manga.application.profiles import h3_stamp
    stamp = h3_stamp(clip, note)
    reference_rejection = ''
    if episode_dir is not None and clip.get('prompt_h3'):
        saved = read_json(episode_dir/'work/request_checks'/f"{clip['clip_id']}.json", {})
        answer = saved.get('answer') or {}
        if (saved.get('inputs', {}).get('english') == clip['prompt_h3']
                and (answer.get('consistent') is not True or answer.get('problems'))):
            from novel_manga.application.preparation.request_check import current_request
            checked = current_request(episode_dir, clip)
            if checked.get('consistent') is not True or checked.get('problems'):
                reference_rejection = 'Request/reference validation rejected the previous English: ' + '; '.join(
                    checked.get('problems') or ['Request and references are not consistent'])
    if (clip.get("prompt_h3_of") == stamp and clip.get("prompt_h3") and not request_issues(clip)
            and not reference_rejection):
        return False
    stages = stages_of(prompt)
    if not stages and clip.get('prompt_compiler') == 'romance_conversation_v1':
        bindings = clip.get('dialogue_bindings', [])
        if any(row.get('stage') != 1 for row in bindings):
            return reject('FAILED: conversation dialogue must belong to its single authored take')
        stages = [(prompt, [(row['speaker_name'], row['text'],
                             row.get('delivery_mode') == 'offscreen_dialogue', row.get('emotion', ''))
                            for row in bindings])]
    if not stages:
        return reject("FAILED: the Chinese prompt has no 【阶段】 block to translate")
    _, subject_of = subject_lines(clip)
    # The name alone does not say what the character IS.  In a book where most of the cast are
    # dragons, a translator given only "奥尔德 = <Subject 2>" has to guess, and it guessed wrong:
    # a human was written with claws, a tail and dragon scales.  The Chinese prompt already carries
    # each character's 辨识特征 in its 【人物】 block, which the 【阶段】 slice never passed along.
    traits = dict(CHARACTER_TRAIT.findall(prompt))
    naming = "".join(
        f"{name} = <Subject {n}>" + (f" ({traits[name]})" if traits.get(name) else "") + "\n"
        for name, n in subject_of.items())
    for n, _, ref in asset_subjects(clip):
        if ref.get('role') in {'location', 'prop'} and ref.get('name') not in subject_of:
            naming += f"{ref['name']} = <Subject {n}>\n"
            naming += ''.join(f'{alias} = <Subject {n}>\n' for alias in ref.get('aliases', []) if alias not in subject_of)
    picture=0
    for ref in clip.get('references',[]):
        if ref.get('role') == 'prop' and ref.get('wearers'):
            wearer_tags = [f'<Subject {subject_of[n]}>' for n in ref['wearers'] if n in subject_of]
            if wearer_tags:
                naming += (f"Wearable {ref['name']}: an item worn by {', '.join(wearer_tags)}. "
                           "A standing or moving description of this worn item refers to its wearer, not an empty suit. "
                           "Distinguish a separate explicitly unoccupied copy only when the shot calls for one.\n")
        if ref.get('role') in {'character','location'}:
            picture+=1
        crowd=clip.get('crowd_roles',{}).get(ref.get('name'))
        if crowd and ref.get('role')=='character':
            naming+=f"{ref['name']} = the {crowd['count'] or 'several'} distinct unnamed supporting people wearing the clothing from <Picture {picture}>\n"
    visuals = [tag_names(visual, naming) for visual, _ in stages]
    has_sound = 'shot_sound' in clip
    if has_sound:
        # Unlike spoken lines, authored physical sound cues must survive the
        # SOUND removal above and reach H3 in English, in their own shot.
        if len(clip.get('shot_sound', [])) != len(stages):
            return reject('FAILED: authored sound does not match shot count')
        visuals = [visual + (' 同期声：' + tag_names(clip['shot_sound'][i], naming) if clip['shot_sound'][i] else '')
                   for i, visual in enumerate(visuals)]
    from novel_manga.story.voice_delivery import visual_request
    spoken = visual_request(clip)
    slots = []
    for i, (_, turns) in enumerate(stages, 1):
        rows = ([r for r in spoken['dialogue_bindings'] if r['stage'] == i]
                if 'dialogue_bindings' in spoken else turns)
        markers = [f'<DIALOGUE_{j}>' for j in range(1, len(rows)+1)]
        slots.append(markers)
        details = []
        for marker, row in zip(markers, rows):
            who = row['speaker_name'] if isinstance(row, dict) else row[0]
            words = row['text'] if isinstance(row, dict) else row[1]
            mode = row.get('delivery_mode', '') if isinstance(row, dict) else ('offscreen_dialogue' if row[2] else 'visible_dialogue')
            details.append({'marker': marker, 'speaker': f'<Subject {subject_of[who]}>' if who in subject_of else 'unseen voice',
                            'delivery': mode, 'emotion': row.get('emotion', '') if isinstance(row, dict) else (row[3] if len(row)>3 else ''), 'words_for_timing_only': words})
        visuals[i-1] += "\nNative dialogue slots in playback order (empty means no generated speech; output markers, not these words): " + json.dumps(details, ensure_ascii=False)
    try:
        context, images = request_context(clip, episode_dir, source_script=source_script)
    except (OSError, ValueError) as error:
        return reject(f'FAILED to load current source/reference context: {error}')
    definitions, _ = subject_lines(clip)
    context.update(reference_bindings=definitions, current_correction=tag_names(note, naming) if note else '',
                   packed_action_bindings=clip.get('action_bindings', []),
                   audio_delivery=spoken.get('audio_delivery', 'native'),
                   request_seconds=clip.get('request_seconds'),
                   shot_timing=clip.get('shot_timing', []))
    schema = copy.deepcopy(SCHEMA)
    music_rules = ''
    if clip.get('music_delivery')=='postmix':
        context['music_delivery']='postmix; external story music is assembled after native video'
        music_rules='Native non_diegetic_music is None. External music is assembled later; keep it out of shot text and physical soundscape. Preserve actual dialogue and source physical sounds.\n'
    if clip.get('music_cues'):
        context['music_cues'] = clip['music_cues']
        schema['required'].append('music')
        schema['properties']['music'] = {'type': 'string', 'minLength': 1,
                                         'pattern': '^' + ENGLISH_CHARACTERS + '*$'}
        music_rules = ('Translate every authored music cue into the music field, preserving its stage, '
                       'entry, withdrawal and relationship to dialogue. Music stays below speech and '
                       'does not mask physical sound. Explicit no-music stages remain unscored. '
                       'Keep music out of soundscape; that field contains physical sound only.\n')
    schema['properties']['shots'].update(minItems=len(visuals), maxItems=len(visuals))
    directions = []
    for markers in slots:
        contract = {'type': 'string', 'minLength': 1,
                    'pattern': '^' + (ENGLISH_CHARACTERS + '*').join(
                        ['', *[re.escape(m) for m in markers], '']) + '$'}
        directions.append(contract)
    schema['properties']['shots'].update(prefixItems=directions, items=False)
    problem = reference_rejection
    endpoint = h3_translation_endpoint()
    run_id = time.strftime('%Y%m%d-%H%M%S')
    for attempt in range(1, tries + 1):
        answer = None
        try:
            feedback = ('\nPrevious response was rejected: ' + problem + '\n') if problem else ''
            overlay_rules=POST_OVERLAY_RULES if context.get('postproduction_overlays') else ''
            response_rules = (f'Return a JSON object with shots containing exactly {len(visuals)} items, one per numbered input shot. '
                'Keep the complete starting state, action and ending of each shot in that single item. '
                'Put physical sound in the separate top-level soundscape field, never as another shots item. '
                'Return source conflicts in the top-level conflicts array.\n')
            acting_rules=ASK
            if native_speech_selected(clip):
                start=acting_rules.index('Write its vocal delivery once')
                end=acting_rules.index('Use only supplied Subject tags')
                acting_rules=acting_rules[:start]+('The host supplies each bound emotion as English performance immediately before its dialogue slot. '
                    'Keep only the physical acting and dialogue marker in shots; do not repeat or paraphrase that vocal direction or move it to soundscape. ')+acting_rules[end:]
            text = (acting_rules + INPUT_RULES + music_rules + overlay_rules + response_rules + feedback + naming + '\nInput context:\n'
                    + json.dumps(context, ensure_ascii=False) + '\nShots:\n'
                    + '\n'.join(f'{i}. {visual}' for i, visual in enumerate(visuals, 1)))
            print(f"{clip.get('clip_id', 'clip')}: h3prompt model={endpoint.model}", file=sys.stderr, flush=True)
            answer = ask_json([{'type': 'text', 'text': text}, *images], schema, name='h3prompt',
                              max_tokens=700 + 450 * len(visuals), settings=endpoint, json_object=True)
            if not isinstance(answer, dict) or not isinstance(answer.get('conflicts', []), list):
                raise ValueError('H3 translation requires a JSON object with a conflicts array')
            if answer.get('conflicts'):
                if problems is not None:
                    problems.extend(answer['conflicts'])
                if source_conflicts is not None:
                    source_conflicts.extend(answer['conflicts'])
                warn(clip, 'source/storyboard conflict: ' + '; '.join(answer['conflicts']))
                return False
            directions = answer.get('shots')
            if not isinstance(directions, list) or any(not isinstance(v, str) for v in directions):
                raise ValueError('H3 shots must be an array of English strings')
            if not isinstance(answer.get('soundscape') or '', str):
                raise ValueError('H3 soundscape must be an English string')
            english = [v.strip() for v in directions]
            soundscape = (answer.get('soundscape') or '').strip()
            music = answer.get('music', '') if clip.get('music_cues') else ''
            if clip.get('music_cues') and (not isinstance(music, str) or not music.strip()):
                raise ValueError('authored music requires English music directions')
            if len(english) != len(visuals):
                raise ValueError(f'shots requires exactly {len(visuals)} items; received {len(english)}. '
                    'Keep an ending in its shot paragraph and sound in the top-level soundscape field.')
            if not all(english):
                raise ValueError('one nonempty direction is required for each shot')
            for i, sentence in enumerate(english):
                if re.findall(r'<DIALOGUE_\d+>', sentence) != slots[i]:
                    raise ValueError(f'shot {i+1} lost or reordered dialogue slots: {slots[i]}')
            from novel_manga.story.h3 import META
            for field, value in [(f'shot {i}', v) for i, v in enumerate(english, 1)] + [('soundscape', soundscape), ('music', music)]:
                match = CJK.search(value) or META.search(value)
                if match:
                    excerpt = value[max(0, match.start() - 40):match.end() + 80]
                    raise ValueError(f'{field} contains Chinese or task commentary: {excerpt!r}; '
                                     'write only English scene behavior and physical sound')
            candidate = compose(clip, english, stages, soundscape=soundscape, music=music)
            conflicts = request_issues({**clip, 'prompt_h3': candidate})
            if conflicts:
                raise ValueError('; '.join(conflicts))
            if reference_rejection:
                checked = current_request(episode_dir, {**clip, 'prompt_h3': candidate})
                if checked.get('consistent') is not True or checked.get('problems'):
                    raise ValueError('Request/reference validation: ' + '; '.join(
                        checked.get('problems') or ['Request and references are not consistent']))
            clip['prompt_h3'] = candidate
            clip['prompt_h3_of'] = stamp
            merge_correction_into_prompt()
            return True
        except Exception as error:  # noqa: BLE001 - bounded generation, never a semantic fallback
            problem = f'{type(error).__name__}: {str(error)[:350]}'
            if episode_dir is not None and answer is not None:
                atomic_write_json(episode_dir / 'h3_translation_failures' / clip['clip_id']
                                  / f'{run_id}-{attempt:02d}.json', {'answer': answer, 'error': problem})
    warn(clip, f"FAILED after {tries} tries: {problem}")
    if problems is not None and problem:
        problems.append(problem)
    return False


def episode_numbers(spec: str) -> set[int]:
    numbers: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if "-" in part:
            start, end = part.split("-", 1)
            numbers.update(range(int(start), int(end) + 1))
        elif part:
            numbers.add(int(part))
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel")
    parser.add_argument("--novel-dir", type=Path, help="the novel's output directory (default outputs/<novel>)")
    parser.add_argument("--chapters", help='only these episodes, e.g. "1597" or "1-50,60"')
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir or ROOT / "outputs" / args.novel
    episodes = sorted(d for d in novel_dir.glob(f"{args.novel}_*") if (d / "clip_plan.json").is_file())
    if args.chapters:
        wanted = episode_numbers(args.chapters)
        episodes = [d for d in episodes if ep_names.is_episode(d.name) and ep_names.chapter_of(d.name) in wanted]
    if args.limit:
        episodes = episodes[: args.limit]
    print(f"{args.novel}: {len(episodes)} 集，{args.workers} 路并行", flush=True)

    def one(episode: Path) -> tuple[int, int, int]:
        path = episode / "clip_plan.json"
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0, 0, 0
        from novel_manga.application.preparation.readiness import inspect_episode
        _, blocked = inspect_episode(episode)
        video = [c for c in plan["clips"] if c.get("kind") == "video" and c["clip_id"] not in blocked]
        notes = {key: str(value) for key, value in corrections(episode).items()}  # director corrections, per clip
        # Every clip whose English prompt is due - all of them with --rebuild - and does not get one is a failure:
        # an older English prompt left in place is not a rebuild (it used to pass as one, exit code 0).
        # Strict: conversion itself requires the versioned stamp, so a pre-v2 or unstamped English
        # prompt is recompiled once here - the seating becomes provable, the videos stay untouched.
        due = [clip for clip in video if args.rebuild or h3_prompt_outdated(clip, notes.get(clip["clip_id"], ""), strict=True)]
        if args.rebuild:
            for clip in due:
                clip.pop("prompt_h3_of", None)
        made = {clip["clip_id"]: clip for clip in due if convert(clip, note=notes.get(clip["clip_id"], ""), episode_dir=episode)}
        if made:
            # The translations take minutes: write them into the plan as it is now, and only onto clips
            # whose Chinese prompt is still the one they were made from.
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = plan
            now = {key: str(value) for key, value in corrections(episode).items()}
            for clip in current.get("clips", []):
                new = made.get(clip.get("clip_id"))
                if new and new["prompt_h3_of"] == h3_stamp(clip, now.get(clip.get("clip_id"), "")):
                    for field in ('prompt', 'prompt_before_correction', 'prompt_correction_merged',
                                  'prompt_h3', 'prompt_h3_of'):
                        if field in new:
                            clip[field] = new[field]
            atomic_write_json(path, current)
            plan = current
        return len(made), len(video), len(due) - len(made)

    done = total = missing = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, (changed, clips, left) in enumerate(pool.map(one, episodes), 1):
            done += changed
            total += clips
            missing += left
            if n % 100 == 0:
                print(f"  {n}/{len(episodes)} 集，已转 {done} 段", flush=True)
    print(f"\n转好 {done} 段（共 {total} 段视频片段）" + (f"；{missing} 段没转成" if missing else ""))
    return 1 if missing else 0
