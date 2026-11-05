"""Separate the video model's speech from a thought mixed in after generation."""
from __future__ import annotations
import copy


def speech_sequence(stages):
    """Ordered authored words and delivery, allowing only adjacent same-owner text to be split/joined."""
    result = []
    for stage in stages:
        for turn in stage.get('turns') or []:
            if turn.get('delivery_mode') not in {'visible_dialogue', 'offscreen_dialogue'} or not turn.get('text'):
                continue
            key = (turn.get('speaker_name'), turn['delivery_mode'], bool(turn.get('inner_monologue')))
            if result and result[-1][0] == key:
                result[-1] = (key, result[-1][1] + turn['text'])
            else:
                result.append((key, turn['text']))
    return result


def postmixed(clip: dict) -> bool:
    return clip.get('audio_delivery') == 'postmix'


def visual_request(clip: dict) -> dict:
    if not postmixed(clip):
        return clip
    visual = copy.deepcopy(clip)
    visual.update(audio_delivery='silent_visual', spoken_text='', lines=[], dialogue_bindings=[])
    visual['references'] = [ref for ref in visual.get('references', []) if ref.get('role') != 'voice']
    return visual


NATIVE_SPEECH_POLICY = 'no_reference_performance_first'


def native_speech_selected(profile):
    policy=(profile or {}).get('h3_speech_policy')
    if policy not in (None,'','reference',NATIVE_SPEECH_POLICY):
        raise ValueError('Unknown h3_speech_policy; choose reference or '+NATIVE_SPEECH_POLICY)
    return policy==NATIVE_SPEECH_POLICY


def performance_error(cue):
    """The same authoring boundary applies before creation export and H3 compilation."""
    import re
    cue=str(cue or '').strip()
    if (not re.search('[A-Za-z]',cue) or re.search('[\u4e00-\u9fff]',cue)
            or cue.lower() in {'none','none.'} or re.search(r'<[/\w]|\(S\d+\)|\[Shot ',cue)):
        return 'supply an English event-specific performance in emotion before compiling speech'
    return None


def sd_performance_errors(bindings,prompt):
    """Bind the SD speaker/words/cue line without interpreting it as H3 markup."""
    import re
    problems=[];cursor=0
    for i,row in enumerate(bindings,1):
        cue=str(row.get('emotion')or'').strip();words=str(row.get('text')or'')
        who=re.escape(str(row.get('speaker_name')or''));text=re.escape(words);performance=re.escape(cue)
        conversation=who+r'(?:（[^\n]*?）)?[：:]\s*'+text+r'\s*[（(]'+performance+r'[）)]'
        classic=performance+r'[^{}\n]*?说[：:]\s*\{'+text+r'\}'
        matches=[m for pattern in(conversation,classic)if(m:=re.search(pattern,prompt[cursor:]))]
        if not cue or not matches:
            problems.append(f'line {i}: SD dialogue must retain its bound English performance and source words')
            continue
        match=min(matches,key=lambda m:m.start());cursor+=match.end()
        if re.match(r'\s*[（(]'+performance+r'[）)]',prompt[cursor:]):
            problems.append('bound speech performance is duplicated; keep its single host-supplied cue')
    return problems


def native_speech_errors(clip, *, prompt='', audios=None, syntax='h3'):
    """Check the selected contract, not subjective delivery quality."""
    import re
    if not native_speech_selected(clip):return []
    visual=visual_request(clip);problems=[]
    if any(r.get('role')=='voice'for r in visual.get('references',[]))or audios:
        problems.append('native speech policy forbids an audio reference in the actual request')
    bindings=[r for r in visual.get('dialogue_bindings',[])if r.get('text')and
              r.get('delivery_mode')in {'visible_dialogue','offscreen_dialogue'}]
    for i,row in enumerate(bindings,1):
        if error:=performance_error(row.get('emotion')):
            problems.append(f'line {i}: {error}')
    if prompt:
        if re.search(r'<\s*Audio\s+\d+\s*>|audio reference|voice(?:-timbre)? reference',prompt,re.I):
            problems.append('native speech policy forbids audio reference definitions or labels')
        if syntax=='sd':
            return problems+sd_performance_errors(bindings,prompt)
        body=prompt.split('detailed_description:',1)[-1].split('overall_soundscape:',1)[0]
        stages={int(m[1]):m[2]for m in re.finditer(r'\[Shot (\d+)\](.*?)(?=\[Shot \d+\]|\Z)',body,re.S)}
        for stage in {r.get('stage',1)for r in bindings}:
            text=stages.get(stage,'');utterances=list(re.finditer(r'<d>.*?</d>',text,re.S));cursor=0
            turns=[r for r in bindings if r.get('stage',1)==stage]
            for i,row in enumerate(turns):
                cue=str(row.get('emotion')or'').strip()
                if i>=len(utterances)or not cue or cue not in text[cursor:utterances[i].start()]:
                    problems.append(f'stage {stage} line {i+1}: bound performance must appear before its dialogue')
                if i<len(utterances):
                    if cue and text[cursor:utterances[i].start()].count(cue)>1:
                        problems.append('bound speech performance is duplicated; keep its single host-supplied cue')
                    cursor=utterances[i].end()
    return problems


def _request_syntax(profile,clip,request=None,*,task=None):
    """Use the saved take's model when present, then the prior profile contract.

    A mixed episode may retain prompt_h3 metadata for a take generated by SD;
    current runtime/profile flags cannot change that take's submitted grammar.
    """
    from novel_manga.config import LOCAL_VIDEO_MODELS,PHANROUTER_VIDEO_MODELS
    model=(task or{}).get('model')
    if model in LOCAL_VIDEO_MODELS or model=='MiniMax-H3':
        return 'h3'
    if model in PHANROUTER_VIDEO_MODELS:
        return 'sd'
    if profile.get('sd_input_mode')=='first-frame':
        return 'sd'
    if profile.get('h3_input_mode')=='first-frame' or clip.get('prompt_h3'):
        return 'h3'
    prompt=str((request or {}).get('prompt') or '')
    if 'detailed_description:' in prompt and 'overall_soundscape:' in prompt and '[Shot ' in prompt:
        return 'h3'
    return 'sd'


def require_native_speech(ctx,clip,request=None):
    """Guard profile/clip agreement and real audio inputs before cache or submission."""
    from novel_manga.util import read_json
    profile=read_json(ctx.novel_dir/'profile.json',{})
    selected=native_speech_selected(profile);compiled=native_speech_selected(clip)
    if selected!=compiled:
        raise ValueError('H3 speech policy changed or was dropped; repack this clip before using its request/cache')
    errors=native_speech_errors(clip,prompt=(request or {}).get('prompt',clip.get('prompt_h3','')),
                                audios=(request or {}).get('reference_audios'),
                                syntax=_request_syntax(profile,clip,request))
    if errors:raise ValueError('; '.join(errors))


def episode_native_speech_errors(directory,media):
    """A final picture/ASR pass cannot certify a different native-speech input."""
    from pathlib import Path
    from novel_manga.util import read_json
    directory=Path(directory);profile=read_json(directory.parent/'profile.json',{})
    plan=read_json(directory/'clip_plan.json',{})
    selected=native_speech_selected(profile)
    if not selected and not any(native_speech_selected(c)for c in plan.get('clips',[])):return {}
    results={};takes={r['clip_id']:r.get('selected')or{}for r in media.get('clips',[])}
    for clip in plan.get('clips',[]):
        if clip.get('kind')!='video':continue
        cid=clip['clip_id'];errors=[]
        if selected!=native_speech_selected(clip):
            errors.append('speech policy differs from the task profile; repack before accepting this movie')
        errors.extend(native_speech_errors(clip))
        chosen=takes.get(cid)or{};video=chosen.get('video')
        request=read_json(Path(video).parent/'request.json',{})if video else{}
        if not request.get('prompt'):
            if video or (media.get('assembly')or{}).get('final_video'):
                errors.append('selected native take has no preserved request to verify speech policy')
        else:
            # A finished episode can contain local H3 and a newer Fast take.
            # Its preserved provider task owns the grammar, not the finish
            # process's current lane or an unused prompt_h3 still in the plan.
            task=read_json(Path(video).with_suffix(Path(video).suffix+'.task.json'),{})
            errors.extend(native_speech_errors(clip,prompt=request.get('prompt',''),audios=request.get('reference_audios'),
                syntax=_request_syntax(profile,clip,request,task=task)))
        if errors:results[cid]=list(dict.fromkeys(errors))
    if not plan.get('clips'):results['episode']=['speech policy requires a current clip plan']
    return results
