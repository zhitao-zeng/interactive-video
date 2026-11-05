"""Creative-only intake and one persistent author; no media services."""
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from novel_manga.interactive import creation
from novel_manga.interactive.creative_contract import read_evidence


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def task(tmp_path, *, original=False):
    archive = tmp_path / 'original.zip'
    if not original:
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('试播集/原稿.md', '# 原稿\n入口有一块牌，旅客看完后停步。')
            z.writestr('角色/身份.png', b'supplied-image-bytes')
            z.writestr('__MACOSX/._原稿.md', b'metadata')
    path = tmp_path / 'task.json'
    write(path, {'mode': 'original' if original else 'adaptation', 'project': 'adventure',
                 'title': '测试', 'brief': '写完整一集，先不生成视频。',
                 **({} if original else {'source_archive': str(archive)}),
                 'target_seconds': [180, 300], 'voice_reference_policy': 'none',
                 'world_card_seconds': 3, 'end_card_seconds': 3})
    return path


def episode():
    shots = [{'index': i, 'scene_id': 'entry', 'location': '入口',
              'duration_seconds': 15, 'purpose': '核对入口规则',
              'start_state': '站在入口', 'event': '读牌后停步', 'end_state': '停在牌旁',
              'camera': '固定中景，牌和旅客同框', 'characters': ['旅客'], 'in_frame': ['旅客'],
              'turns': [], 'sfx': '风声', 'audio_plan': {'music_cue': '无配乐'},
              'source_quote': '入口有一块牌，旅客看完后停步。'} for i in range(1, 13)]
    return {'title': '测试', 'logline': '旅客核对入口规则。', 'adaptation_notes': ['保留原始事实'],
            'chapter_script': {'shots': shots},
            'clips': [{'shot_indexes': [i], 'request_seconds': 15} for i in range(1, 13)],
            'interaction': {'question': '下一步？', 'selected': None, 'options': [
                {'id': 'A', 'label': '查入口', 'reason': '入口有牌'},
                {'id': 'B', 'label': '看周围', 'reason': '周围还没核对'}]},
            'spatial_plan': {'scenes': [{'scene_id': 'entry', 'anchors': ['入口', '牌'],
                'placed': ['旅客'], 'shot_indexes': list(range(1, 13)),
                'assumptions': ['距离是调度方案，未经测量']}]},
            'self_review': {'source': '已核对', 'open_issues': []}}


def test_nested_existing_groups_report_the_exact_json_path_without_regrouping(tmp_path):
    import copy
    manifest=creation.prepare(task(tmp_path),tmp_path/'run');draft=episode()
    draft['chapter_script']['clips']=draft.pop('clips');original=copy.deepcopy(draft)
    errors=creation.validate(draft,manifest,tmp_path/'run/sandbox')
    assert any('chapter_script.clips' in error and 'top-level clips' in error for error in errors)
    assert draft==original


@pytest.mark.parametrize('value',[None,'旅客、背包',['旅客','滑向海面的深灰背包']])
def test_creator_catches_roster_prose_or_nonactor_before_material_handoff(tmp_path,value):
    manifest=creation.prepare(task(tmp_path),tmp_path/'run');draft=episode()
    draft['chapter_script']['shots'][0]['in_frame']=value
    errors=creation.validate(draft,manifest,tmp_path/'run/sandbox')
    assert any('shot 1' in e and 'in_frame' in e and 'actor' in e for e in errors)


def test_cached_candidate_rechecks_current_contract_and_resumes_same_author(tmp_path,monkeypatch):
    source=task(tmp_path);run=tmp_path/'run';manifest=creation.prepare(source,run);work=run/'sandbox'
    draft=episode();draft['chapter_script']['shots'][0]['in_frame']='旅客、背包'
    write(work/'output/episode.json',draft);(work/'output/creative-notes.md').write_text('Original camera notes')
    write(run/'creator-state.json',{'status':'creative_candidate','seconds_used':20,'allowance_seconds':90})
    calls=[]
    def invoke(work,prompt,**kwargs):
        assert kwargs['resume'] is True and kwargs['timeout']==70
        feedback=json.loads((work/'input/creative-validation.json').read_text())
        assert any('in_frame' in e for e in feedback['issues'])
        calls.append(1);write(work/'output/episode.json',episode())
        attempt=work/'attempts/corrected';attempt.mkdir(parents=True)
        rows=[{'type':'tool_use','name':'Read','input':{'file_path':'/work/'+p}} for p in manifest['required_reads']]
        (attempt/'session.jsonl').write_text(json.dumps({'message':{'content':rows}}))
        return SimpleNamespace(ok=True,exit_code=0,seconds=1,directory=attempt,record={'session_id':'same-author','model':'configured'})
    monkeypatch.setattr(creation,'run_session',invoke)
    result=creation.run(source,run,resume=True,config={'timeout_seconds':999,'source_agent_slice_seconds':1200})
    assert result['status']=='creative_candidate' and result['seconds_used']==21 and calls==[1]


@pytest.mark.parametrize('placeholder',['__MORE_SHOTS__','__END__'])
def test_partial_creator_shots_report_an_issue_instead_of_crashing(tmp_path,placeholder):
    manifest=creation.prepare(task(tmp_path),tmp_path/'run');draft=episode()
    draft['chapter_script']['shots'].append(placeholder)
    errors=creation.validate(draft,manifest,tmp_path/'run/sandbox')
    assert any('placeholder'in error for error in errors)


def test_recorded_creator_time_survives_a_missing_state_after_validation_crash(tmp_path,monkeypatch):
    source=task(tmp_path);run=tmp_path/'run';creation.prepare(source,run)
    write(run/'sandbox/attempts/prior/attempt.json',{'seconds':1201})
    monkeypatch.setattr(creation,'run_session',lambda *args,**kwargs:pytest.fail('Do not renew the spent creator allowance'))
    result=creation.run(source,run,resume=True,config={'timeout_seconds':1200,'source_agent_slice_seconds':1200})
    assert result['status']=='time_budget_used'


def test_fast_style_schema_is_checked_before_a_completed_creator_is_adopted(tmp_path):
    source=task(tmp_path);value=json.loads(source.read_text());value['profile']={'sd_input_mode':'first-frame'};write(source,value)
    manifest=creation.prepare(source,tmp_path/'run');work=tmp_path/'run/sandbox';draft=episode()
    for shot in draft['chapter_script']['shots']:
        shot['opening_frame']={'image_prompt':'A static view of the person beside the sign.','visible_characters':['旅客']}
    write(work/'output/style.json',{'summary':'Photographic live action.'})
    assert any('style.json'in error for error in creation.validate(draft,manifest,work))
    write(work/'output/style.json',{'visual_style':'Photographic live action.','render_family':'photo'})
    assert creation.validate(draft,manifest,work)==[]


def test_first_frame_creation_requires_authored_static_opening_before_production(tmp_path):
    path=task(tmp_path);value=json.loads(path.read_text());value['h3_input_mode']='first-frame';write(path,value)
    manifest=creation.prepare(path,tmp_path/'run');draft=episode();work=tmp_path/'run/sandbox'
    assert any('opening_frame'in e for e in creation.validate(draft,manifest,work))
    for shot in draft['chapter_script']['shots']:
        shot['opening_frame']={'image_prompt':'One traveller stands beside the sign at the entrance before moving.',
            'visible_characters':['旅客'],'reference_subjects':['旅客','入口']}
    assert creation.validate(draft,manifest,work)==[]


def test_timeline_music_is_authored_with_story_and_source_shot_anchors(tmp_path):
    path=task(tmp_path);value=json.loads(path.read_text());value['music_mode']='timeline';write(path,value)
    manifest=creation.prepare(path,tmp_path/'run');draft=episode();work=tmp_path/'run/sandbox'
    assert any('music_plan'in e for e in creation.validate(draft,manifest,work))
    draft['chapter_script']['music_plan']={'cues':[{'id':'cue1','query':'restrained investigation',
        'source_quote':'入口有一块牌，旅客看完后停步。','reason':'slow observation',
        'start':{'shot':1,'edge':'start'},'end':{'shot':2,'edge':'end'}}],
        'silences':[]}
    assert creation.validate(draft,manifest,work)==[]
    draft['chapter_script']['music_plan']['cues'][0]['end']['shot']=999
    assert any('music_plan'in e for e in creation.validate(draft,manifest,work))


def test_package_intake_keeps_complete_sources_and_installs_one_integrated_skill(tmp_path):
    manifest = creation.prepare(task(tmp_path), tmp_path / 'run')
    work = tmp_path / 'run/sandbox'
    assert (work / 'input/package/试播集/原稿.md').read_text() == '# 原稿\n入口有一块牌，旅客看完后停步。'
    assert (work / 'input/package/角色/身份.png').read_bytes() == b'supplied-image-bytes'
    assert manifest['phase'] == 'creation_only' and manifest['media_generation_allowed'] is False
    assert set(manifest['skill_references']) >= {'camera_language', 'spatial_blockout',
        'authoring', 'repair_playbook', 'creative_authoring', 'travel_authoring'}
    for p in manifest['skill_references'].values():
        assert (work / p).is_file()
    assert len(json.loads((work / 'input/creation-methods.json').read_text())) == 6
    assert not (work / 'tools').exists()
    assert sorted(p.name for p in (work / '.claude/skills').iterdir()) == ['episode-production']


def test_prepare_preserves_candidate_and_refuses_changed_task(tmp_path):
    source = task(tmp_path); run = tmp_path / 'run'
    first = creation.prepare(source, run)
    candidate = run / 'sandbox/output/episode.json'; write(candidate, {'unfinished': True})
    assert creation.prepare(source, run) == first
    assert json.loads(candidate.read_text()) == {'unfinished': True}
    changed = json.loads(source.read_text()); changed['brief'] = '另一份创作'; write(source, changed)
    with pytest.raises(ValueError, match='new output directory'):
        creation.prepare(source, run)


@pytest.mark.parametrize('project', ['adventure', 'romance', 'suspense'])
def test_shared_dialogue_craft_is_installed_and_required_for_every_creator_genre(tmp_path, project):
    source = task(tmp_path)
    data = json.loads(source.read_text()); data['project'] = project; write(source, data)
    manifest = creation.prepare(source, tmp_path/'run')
    guide = manifest['skill_references']['dialogue_craft']
    assert guide in manifest['required_reads']
    assert (tmp_path/'run/sandbox'/guide).is_file()
    assert manifest['skill_references'][manifest['selected_genre']] in manifest['required_reads']


def test_creation_requires_short_camera_and_method_index_while_retaining_full_sources(tmp_path):
    source = task(tmp_path)
    archive = tmp_path/'original.zip'
    full_text = '旅客沿入口的水痕慢慢观察。' * 1500
    with zipfile.ZipFile(archive, 'a') as package:
        package.writestr('长原文.md', full_text)
    manifest = creation.prepare(source, tmp_path/'run')
    work = tmp_path/'run/sandbox'
    refs = manifest['skill_references']
    assert refs['camera_selection'] in manifest['required_reads']
    assert 'input/creation-method-index.json' in manifest['required_reads']
    assert manifest['method_library'] == 'input/creation-methods.json'
    for key in ('camera_language', 'output_contract', 'production_workflow', 'authoring', 'repair_playbook'):
        assert refs[key] not in manifest['required_reads']
        assert (work/refs[key]).is_file()
    assert 'input/package/长原文.md' in manifest['required_reads']
    assert (work/'input/package/长原文.md').read_text() == full_text
    index = json.loads((work/'input/creation-method-index.json').read_text())
    library = json.loads((work/'input/creation-methods.json').read_text())
    assert [m['key'] for m in index] == [m['key'] for m in library]


def test_original_brief_is_a_distinct_authority_without_a_fake_archive(tmp_path):
    manifest = creation.prepare(task(tmp_path, original=True), tmp_path / 'run')
    assert manifest['mode'] == 'original' and manifest['source_documents'] == []
    assert manifest['source_archive'] is None


def test_contract_checks_source_grouping_duration_and_unselected_interaction(tmp_path):
    manifest = creation.prepare(task(tmp_path), tmp_path / 'run')
    assert creation.validate(episode(), manifest, tmp_path / 'run/sandbox') == []
    draft = episode(); draft['chapter_script']['shots'][0]['source_quote'] = '原文没有的袭击。'
    draft['clips'][0]['shot_indexes'] = [2]
    draft['interaction']['selected'] = 'A'
    problems = creation.validate(draft, manifest, tmp_path / 'run/sandbox')
    assert any('source_quote' in x for x in problems)
    assert any('cover' in x for x in problems)
    assert any('selected' in x for x in problems)


def test_read_evidence_keeps_successful_cat_before_an_unrelated_command_failure(tmp_path):
    manifest = creation.prepare(task(tmp_path), tmp_path/'run')
    work = tmp_path/'run/sandbox'; attempt = work/'attempts/native'; attempt.mkdir(parents=True)
    source = manifest['source_documents'][0]
    events = [{'type':'item.completed','item':{'type':'command_execution','exit_code':127,
        'command':f'cat /work/{source}; file missing-command',
        'aggregated_output':(work/source).read_text()+'\nfile: not found'}}]
    (attempt/'codex-events.jsonl').write_text('\n'.join(json.dumps(x) for x in events))
    assert source in {r['path'] for r in read_evidence(work)}
    events[0]['item']['aggregated_output'] = 'cat failed, no source content'
    (attempt/'codex-events.jsonl').write_text(json.dumps(events[0]))
    assert source not in {r['path'] for r in read_evidence(work)}


def test_native_read_evidence_records_declared_optional_skill_reference(tmp_path):
    manifest = creation.prepare(task(tmp_path), tmp_path/'run')
    work = tmp_path/'run/sandbox'
    guide = manifest['skill_references']['director_camera_styles']
    required = list(manifest['required_reads'])
    assert guide not in required
    attempt = work/'attempts/native'; attempt.mkdir(parents=True)
    event = {'type':'item.completed','item':{'type':'command_execution','exit_code':0,
        'command':f"sed -n '43,58p' /work/{guide}",
        'aggregated_output':'## 2. 库布里克式低位稳定跟拍'}}
    (attempt/'codex-events.jsonl').write_text(json.dumps(event))
    assert guide in {r['path'] for r in read_evidence(work)}
    assert json.loads((work/'input/manifest.json').read_text())['required_reads'] == required


@pytest.mark.parametrize('first_issue', ['selected_route', 'truncated_json', 'missing_notes'])
def test_run_uses_file_tools_and_one_existing_session_for_correction(tmp_path, monkeypatch, first_issue):
    source = task(tmp_path); run = tmp_path / 'run'; calls = []
    manifest = creation.prepare(source, run)

    def invoke(work, prompt, **kwargs):
        calls.append(kwargs)
        actual_manifest = json.loads((work/'input/manifest.json').read_text())
        assert actual_manifest['skill_references']['dialogue_craft'] in actual_manifest['required_reads']
        attempt = work / 'attempts' / str(len(calls)); attempt.mkdir(parents=True)
        draft = episode()
        if len(calls) == 1 and first_issue != 'missing_notes':
            draft['interaction']['selected'] = 'A'
        write(work / 'output/episode.json', draft)
        if len(calls) > 1 or first_issue != 'missing_notes':
            (work/'output/creative-notes.md').write_text('镜1–12保持旅客与规则牌同框，让核对动作可见。')
        if len(calls) == 1 and first_issue == 'truncated_json':
            (work / 'output/episode.json').write_text('{"title": "partial"')
        write(work / 'episode-session.json', {'session_id': 'same-creator'})
        rows = [{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/work/' + p}}
            for p in manifest['required_reads']]}}]
        (attempt / 'session.jsonl').write_text('\n'.join(json.dumps(x) for x in rows))
        return SimpleNamespace(ok=True, exit_code=0, seconds=1, directory=attempt,
                               record={'session_id': 'same-creator', 'model': 'configured-model'})

    monkeypatch.setattr(creation, 'run_session', invoke)
    result = creation.run(source, run, config={'source_agent_slice_seconds': 1200, 'timeout_seconds': 5400})
    assert result['status'] == 'creative_candidate' and result['session_id'] == 'same-creator'
    assert len(calls) == 2 and calls[0]['resume'] is False and calls[1]['resume'] is True
    assert all(x['enable_tools'] is False and x['expected_output'] == 'episode.json' for x in calls)
    assert all(x['timeout'] <= 1200 for x in calls)
    assert result['allowance_seconds'] == 5400 and result['slice_seconds'] == 1200
    assert (run / 'delivery/剧本.md').is_file() and (run / 'delivery/白模空间设计.md').is_file()
    assert result['new_video_generations'] == 0
    assert creation.run(source, run, config={'source_agent_slice_seconds': 1200}) == result
    assert len(calls) == 2
    legacy = json.loads((run/'sandbox/input/manifest.json').read_text())
    camera = run/'sandbox'/legacy['skill_references']['camera_language']
    camera.write_text('Earlier camera guidance')
    # A completed no-op must leave even an older mounted guide untouched.
    assert creation.run(source, run, config={'source_agent_slice_seconds': 1200}) == result
    assert camera.read_text() == 'Earlier camera guidance' and len(calls) == 2
    guide = legacy['skill_references'].pop('dialogue_craft')
    legacy['required_reads'].remove(guide)
    write(run/'sandbox/input/manifest.json', legacy)
    feedback = tmp_path/'review.md'; feedback.write_text('明确夜间光源，保留原故事。')
    revised = creation.run(source, run, config={'source_agent_slice_seconds': 1200},
                           resume=True, feedback=feedback)
    assert revised['session_id'] == 'same-creator' and len(calls) == 3
    from novel_manga.application.agents.episode.session import SKILL_SOURCE
    assert camera.read_bytes() == (SKILL_SOURCE/'references/camera-language.md').read_bytes()
    assert calls[-1]['resume'] is True
    archived = json.loads((run/'manifest-before-dialogue-craft.json').read_text())
    assert archived == legacy and guide not in archived['required_reads']
    assert json.loads((run/'sandbox/input/manifest.json').read_text())['task'] == legacy['task']
    assert (run/'sandbox/input/feedback/revision-01.md').read_text() == feedback.read_text()


def test_repeated_validation_error_stops_bounded_correction_in_the_same_session(tmp_path, monkeypatch):
    source = task(tmp_path); run = tmp_path/'run'; manifest = creation.prepare(source, run)
    calls = []; config = {'source_agent_slice_seconds':1200, 'timeout_seconds':5400}

    def invoke(work, prompt, **kwargs):
        calls.append((prompt, kwargs))
        attempt = work/'attempts'/str(len(calls)); attempt.mkdir(parents=True)
        draft = episode(); draft['interaction']['selected'] = 'A'
        write(work/'output/episode.json', draft)
        (work/'output/creative-notes.md').write_text('原稿选择与镜头解释保留。')
        rows = [{'type':'assistant','message':{'content':[
            {'type':'tool_use','name':'Read','input':{'file_path':'/work/'+p}}
            for p in manifest['required_reads']]}}]
        (attempt/'session.jsonl').write_text(json.dumps(rows[0]))
        return SimpleNamespace(ok=True, exit_code=0, seconds=1, directory=attempt,
            record={'session_id':'same-creator','model':'configured-model'})

    monkeypatch.setattr(creation, 'run_session', invoke)
    result = creation.run(source, run, config=config)
    assert result['status'] == 'revision_stalled' and len(calls) == 2
    assert result['issues'] == ['interaction.selected must remain null']
    assert result['session_id'] == 'same-creator' and result['allowance_seconds'] == 5400
    assert calls[1][1]['resume'] is True and calls[1][1]['config']['max_turns'] == 16
    assert 'Choose coverage' not in calls[1][0] and 'repair only' in calls[1][0]
    assert 'max_turns' not in config and not (run/'delivery').exists()


def test_first_pass_timeout_with_complete_validated_draft_is_a_candidate(tmp_path, monkeypatch):
    """Zero-intervention diagnostic 2026-10-22: the author finished writing a complete,
    fully validated draft and only the sandbox wrapper hit its slice edge."""
    source = task(tmp_path); run = tmp_path/'run'; manifest = creation.prepare(source, run)
    work = run/'sandbox'

    def invoke(work, prompt, **kwargs):
        write(work/'output/episode.json', episode())
        (work/'output/creative-notes.md').write_text('完整绑定。')
        attempt = work/'attempts/first'; attempt.mkdir(parents=True)
        rows = [{'type':'tool_use','name':'Read','input':{'file_path':'/work/'+p}}
                for p in manifest['required_reads']]
        (attempt/'session.jsonl').write_text(json.dumps({'message':{'content':rows}}))
        return SimpleNamespace(ok=False, exit_code=124, seconds=1, directory=attempt,
            record={'session_id':'first-creator','model':'configured-model'})

    monkeypatch.setattr(creation, 'run_session', invoke)
    result = creation.run(source, run, config={'timeout_seconds':5400}, resume=False)
    assert result['status'] == 'creative_candidate', result
    assert result['agent_exit'] == 124 and result['accepted_despite_agent_exit'] is True


def test_resumed_correction_that_rewrote_the_draft_and_validates_clean_is_a_candidate(tmp_path, monkeypatch):
    """Verification run 2026-10-22: the revision author finished the correction, the
    process wrapper still died, and the validator passes — accept the artifact."""
    source = task(tmp_path); run = tmp_path/'run'; manifest = creation.prepare(source, run)
    work = run/'sandbox'
    old_draft = episode(); old_draft['chapter_script']['shots'][0]['event'] = '旧稿未改'
    write(work/'output/episode.json', old_draft)
    (work/'output/creative-notes.md').write_text('修订说明。')
    write(run/'creator-state.json', {'seconds_used': 77})

    def invoke(work, prompt, **kwargs):
        write(work/'output/episode.json', episode())  # the correction lands on disk
        attempt = work/'attempts/rev'; attempt.mkdir(parents=True)
        rows = [{'type':'tool_use','name':'Read','input':{'file_path':'/work/'+p}}
                for p in manifest['required_reads']]
        (attempt/'session.jsonl').write_text(json.dumps({'message':{'content':rows}}))
        return SimpleNamespace(ok=False, exit_code=1, seconds=1, directory=attempt,
            record={'session_id':'rev-creator','model':'configured-model'})

    monkeypatch.setattr(creation, 'run_session', invoke)
    result = creation.run(source, run, config={'timeout_seconds':5400}, resume=True)
    assert result['status'] == 'creative_candidate', result
    assert result['accepted_despite_agent_exit'] is True


def test_resumed_correction_has_a_turn_bound_but_timeout_is_not_success(tmp_path, monkeypatch):
    source = task(tmp_path); run = tmp_path/'run'; manifest = creation.prepare(source, run)
    work = run/'sandbox'; write(work/'output/episode.json', episode())
    (work/'output/creative-notes.md').write_text('保留原稿。')
    write(run/'creator-state.json', {'seconds_used':77})

    def invoke(work, prompt, **kwargs):
        assert kwargs['resume'] is True and kwargs['config']['max_turns'] == 16
        assert 'Choose coverage' not in prompt and 'do not reopen unrelated' in prompt
        attempt = work/'attempts/checkpoint'; attempt.mkdir(parents=True)
        rows = [{'type':'tool_use','name':'Read','input':{'file_path':'/work/'+p}}
                for p in manifest['required_reads']]
        (attempt/'session.jsonl').write_text(json.dumps({'message':{'content':rows}}))
        return SimpleNamespace(ok=False, exit_code=124, seconds=1, directory=attempt,
            record={'session_id':'original-creator','model':'configured-model'})

    monkeypatch.setattr(creation, 'run_session', invoke)
    result = creation.run(source, run, config={'source_agent_slice_seconds':1200,'timeout_seconds':5400}, resume=True)
    assert result['status'] == 'agent_failed' and result['issues'] == []
    assert result['seconds_used'] >= 77 and result['allowance_seconds'] == 5400
    assert result['session_id'] == 'original-creator' and not (run/'delivery').exists()


from novel_manga.interactive.creative_contract import export
from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY
from novel_manga.util import atomic_write_json, read_json
import copy


def test_new_creator_defaults_speech_and_exports_it_without_changing_original_task(tmp_path):
    source = task(tmp_path, original=True)
    original = source.read_bytes()
    manifest = creation.prepare(source, tmp_path/'run')
    assert manifest['profile']['h3_speech_policy'] == NATIVE_SPEECH_POLICY
    assert manifest['skill_references']['sound_direction'] in manifest['required_reads']
    export(episode(), manifest, tmp_path/'delivery')
    assert read_json(tmp_path/'delivery/profile.json')['h3_speech_policy'] == NATIVE_SPEECH_POLICY
    assert read_json(tmp_path/'delivery/chapter_script.json')['profile']['h3_speech_policy'] == NATIVE_SPEECH_POLICY
    assert source.read_bytes() == original


def test_creator_rejects_unusable_performance_before_production(tmp_path):
    manifest = creation.prepare(task(tmp_path, original=True), tmp_path/'run')
    draft = episode()
    turn = {'speaker_name':'旅客', 'text':'先等等。', 'delivery_mode':'visible_dialogue',
            'emotion':'紧张，小声'}
    draft['chapter_script']['shots'][0]['turns'] = [turn]
    assert any('English' in e for e in creation.validate(draft, manifest, tmp_path/'run/sandbox'))
    turn['emotion'] = 'Her voice drops as she notices the missing tile.'
    assert creation.validate(draft, manifest, tmp_path/'run/sandbox') == []


def test_explicit_reference_mode_and_frozen_legacy_manifest_keep_their_contract(tmp_path):
    source = task(tmp_path, original=True)
    data = read_json(source); data.pop('voice_reference_policy')
    data['profile'] = {'h3_speech_policy':'reference'}
    atomic_write_json(source, data)
    manifest = creation.prepare(source, tmp_path/'run')
    assert manifest['profile']['h3_speech_policy'] == 'reference'
    frozen = copy.deepcopy(manifest); frozen.pop('profile')
    atomic_write_json(tmp_path/'run/sandbox/input/manifest.json', frozen)
    assert creation.prepare(source, tmp_path/'run') == frozen


def test_conflicting_none_and_reference_policies_fail_at_intake(tmp_path):
    source = task(tmp_path, original=True)
    data = read_json(source); data['profile'] = {'h3_speech_policy':'reference'}
    atomic_write_json(source, data)
    with pytest.raises(ValueError, match='conflict'):
        creation.prepare(source, tmp_path/'run')


def test_creator_cannot_override_effective_policy_in_its_script(tmp_path):
    manifest = creation.prepare(task(tmp_path, original=True), tmp_path/'run')
    draft = episode(); draft['chapter_script']['profile'] = {'h3_speech_policy':'reference'}
    assert any('policy' in e for e in creation.validate(draft, manifest, tmp_path/'run/sandbox'))


def test_null_creative_screenplay_returns_correction_instead_of_crashing(tmp_path):
    manifest = creation.prepare(task(tmp_path, original=True), tmp_path/'run')
    assert creation.validate({'chapter_script':None},manifest,tmp_path/'run/sandbox') == [
        'chapter_script.shots must contain the complete episode']
