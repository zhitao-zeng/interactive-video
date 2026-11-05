import novel_manga.planning.preparation as preparation_rules
import novel_manga.application.preparation.actions as preparation_actions
import novel_manga.application.preparation.flow as preparation_flow
import novel_manga.application.preparation.store as preparation_store
import json
import copy
from pathlib import Path

import pytest



def episode(tmp_path):
    d = tmp_path / 'book/book_1'
    d.mkdir(parents=True)
    (d / 'chapter_script.json').write_text(json.dumps({'shots': [{'index': 1}]}))
    (d / 'segments.json').write_text(json.dumps([{'segment_id': 'seg_1', 'text': '李明转身告诉王刚，今天要下雨。'}]))
    (d / 'clip_plan.json').write_text(json.dumps({'clips': []}))
    return d


def test_existing_footage_is_not_rewritten(tmp_path, monkeypatch):
    d = episode(tmp_path)
    take = d / 'work/clips/clip_01/attempt_01/clip.mp4'
    take.parent.mkdir(parents=True)
    take.write_bytes(b'original footage')
    before = (d / 'chapter_script.json').read_bytes()
    monkeypatch.setattr(preparation_actions, 'audit', lambda *a: pytest.fail('must not audit production footage'))
    assert preparation_flow.prepare_one(d)['status'] == 'existing_video'
    assert (d / 'chapter_script.json').read_bytes() == before
    assert take.read_bytes() == b'original footage'


def test_production_admission_prevents_preparation_from_editing_queued_chapters(tmp_path,monkeypatch):
    from novel_manga.util import atomic_write_json
    d=episode(tmp_path)
    atomic_write_json(d.parent/'repair_manager/state.json',{'admitted_episodes':[1]})
    before=(d/'chapter_script.json').read_bytes()
    monkeypatch.setattr(preparation_actions,'audit',lambda *a:pytest.fail('production owns this episode'))
    assert preparation_flow.prepare_one(d)['status']=='production_owned'
    assert (d/'chapter_script.json').read_bytes()==before


def test_bad_source_is_held_without_model_calls(tmp_path, monkeypatch):
    d = episode(tmp_path)
    p = d.parent / 'h3_preparation/source_blocks.json'
    p.parent.mkdir()
    p.write_text(json.dumps({'1': 'source words are scrambled'}))
    monkeypatch.setattr(preparation_actions, 'audit', lambda *a: pytest.fail('must not invent source'))
    assert preparation_flow.prepare_one(d)['status'] == 'needs_source'


@pytest.mark.parametrize('quote,stage', [('这是模型编造出来的原文', 1), ('李明转身告诉王刚', 99)])
def test_ungrounded_audit_cannot_authorize_a_rewrite(quote, stage):
    answer = {'issues': [{'kind': 'speaker', 'source_quote': quote, 'stage': stage}]}
    with pytest.raises(ValueError):
        preparation_rules.grounded_issues(answer, {'shots': [{'index': 1}]}, [{'text': '李明转身告诉王刚，今天要下雨。'}])


def test_missing_event_can_reference_the_original_without_an_existing_stage():
    answer = {'issues': [{'kind': 'missing_event', 'source_quote': '李明转身告诉王刚', 'stage': 0}]}
    assert preparation_rules.grounded_issues(answer, {'shots': [{'index': 1}]}, [{'text': '李明转身告诉王刚，今天要下雨。'}]) == answer['issues']


def test_backup_is_the_original_not_the_latest_rewrite(tmp_path):
    d = episode(tmp_path)
    before = (d / 'chapter_script.json').read_bytes()
    preparation_store.backup(d)
    (d / 'chapter_script.json').write_text('{}')
    preparation_store.backup(d)
    assert (d.parent / 'h3_preparation/before/1/chapter_script.json').read_bytes() == before


def test_english_compilation_does_not_invalidate_source_audit(tmp_path):
    d = episode(tmp_path)
    plan = {'clips': [{'clip_id': 'clip_01', 'kind': 'video', 'prompt': '角色动作', 'references': []}]}
    (d / 'clip_plan.json').write_text(json.dumps(plan))
    before = preparation_store.inputs(d)
    plan['clips'][0]['prompt_h3'] = 'The subject walks.'
    (d / 'clip_plan.json').write_text(json.dumps(plan))
    assert preparation_store.inputs(d) == before


def test_known_identity_updates_invalidate_preparation(tmp_path):
    from novel_manga.util import atomic_write_json
    d = episode(tmp_path)
    before = preparation_store.inputs(d)
    atomic_write_json(d.parent / 'entity/claims.json', [{'status': 'accepted'}])
    assert preparation_store.inputs(d) != before


def test_audit_quote_is_taken_from_raw_source_instead_of_reading_annotations():
    source = [{'segment_id': 'seg_1', 'text': '旧名转身告诉同伴，今天要下雨。'}]
    answer = {'issues': [{'kind': 'speaker', 'stage': 1, 'source_segment': 'seg_1', 'source_quote': ''}]}
    preparation_rules.grounded_issues(answer, {'shots': [{'index': 1}]}, source)
    assert answer['issues'][0]['source_quote'] == source[0]['text']


def test_issue_label_cannot_expand_a_local_fix_to_the_whole_chapter():
    assert not preparation_rules.needs_full_replan([{'stage':3,'kind':'missing_event'}, {'stage':4,'kind':'location'}])
    assert preparation_rules.needs_full_replan([{'stage':0,'kind':'missing_event'}])


def test_failures_retry_with_a_bound_and_do_not_keep_stale_audits(tmp_path):
    d=episode(tmp_path)
    preparation_store.record(d,'starting',attempts=1)
    row=preparation_store.record(d,'error',reason='ReadTimeout',audit={'old':True})
    assert preparation_store.eligible(row,d) and row['retry_after']>0
    clean=preparation_store.record(d,'starting',attempts=2)
    assert 'audit' not in clean and 'reason' not in clean
    row=preparation_store.record(d,'needs_repair',attempts=3)
    assert not preparation_store.eligible(row,d) and 'retry_after' not in row
    row=preparation_store.record(d,'needs_source',attempts=1)
    assert not preparation_store.eligible(row,d)


def test_preparation_repair_requires_explicit_legacy_opt_in(tmp_path, monkeypatch):
    d = episode(tmp_path)
    answers = iter([
        {'source_readable':True,'issues':[{'kind':'missing_event','stage':0}]},
        {'source_readable':True,'issues':[]},
    ])
    monkeypatch.setattr(preparation_actions, 'audit', lambda *a, **k: next(answers))
    calls = []
    monkeypatch.setattr(preparation_actions, 'replan', lambda *a: calls.append('legacy-replan'))
    answer, blocked = preparation_actions.check_script(d, {'clips':[]})
    assert answer is None and blocked['status'] == 'needs_replan' and calls == []
    monkeypatch.setenv('NOVEL_LEGACY_PREPARATION_REPAIR', '1')
    answer, blocked = preparation_actions.check_script(d, {'clips':[]})
    assert answer == {'source_readable':True,'issues':[]} and blocked is None
    assert calls == ['legacy-replan']


def _authored_review_episode():
    shots = [{'index': i, 'start_state': f'镜头{i}实际起点',
              'event': f'镜头{i}实际事件', 'end_state': f'镜头{i}实际终点',
              'camera': f'镜头{i}实际机位'} for i in range(1, 20)]
    shots[2]['end_state'] = '第三镜实际结束：红牌仍在打捞台'
    shots[3].update(
        location='打捞台', start_state='红牌已经亮起；背包在打捞台。',
        event='红牌在镜内缓慢离开打捞台。', end_state='红牌已经离开。',
        camera='认领区三盏灯沿潮线排开，摄影机跟随新灯。',
        opening_frame={'image_prompt': '时间零点：红牌已经亮起；人物仍在打捞台。'},
        storyboard={'frame': '时间零点：红牌已经亮起。',
                    'action_beats': ['红牌在镜内离开。']})
    shots[4]['start_state'] = '第五镜实际开始：切到登记棚'
    shots[17]['end_state'] = '第十八镜实际结束：知遥举起两张卡'
    shots[18].update(
        start_state='A、B两张卡并排；C卡尚未加入。',
        event='镜内加入C卡，随后A、B、C三张目的地卡并排。',
        end_state='A、B、C三张目的地卡并排。',
        opening_frame={'image_prompt': '时间零点场景写A、B、C三张目的地卡并排；静态构图又写C卡尚未加入。'},
        storyboard={'frame': '时间零点：A、B、C三张目的地卡并排。',
                    'action_beats': ['镜内加入C卡。']})
    return {'title': '制作稿审核夹具', 'chapter_script': {'shots': shots}}


def _field_issue(stage, basis, observed, expected, observed_time='t=0', expected_time='t=0'):
    return {'kind': 'action', 'stage': stage, 'relation': 'contradiction',
            'evidence_origin': 'authored_field',
            'basis': basis, 'observed': observed, 'expected': expected,
            'observed_time': observed_time, 'expected_time': expected_time,
            'source_segment': '', 'source_quote': '',
            'problem': '两个制作字段不能同时成立', 'correction': '统一对应时间的实际描述'}


def test_creative_grounding_reads_storyboard_and_rejects_invented_evidence():
    episode = _authored_review_episode()
    finding = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    assert preparation_rules.ground_creative_finding(finding, episode) is None

    internal = dict(finding, basis=[
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].opening_frame.image_prompt'])
    assert preparation_rules.ground_creative_finding(internal, episode) is None

    bad_path = dict(finding, basis=[finding['basis'][0], 'chapter_script.shots[18].missing.field'])
    with pytest.raises(ValueError, match='evidence path'):
        preparation_rules.ground_creative_finding(bad_path, episode)
    derived=dict(finding,evidence_origin='original_text',observed='编造的制作字段引文')
    preparation_rules.ground_creative_finding(derived,episode)
    assert derived['observed']==episode['chapter_script']['shots'][18]['opening_frame']['image_prompt']
    with pytest.raises(ValueError, match='stage'):
        preparation_rules.ground_creative_finding(dict(finding, stage=18), episode)


def test_creative_audit_corrects_once_then_confirms_independent_findings(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原包要求保留打捞台和镜内加入第三张卡。'}
    false_start = _field_issue(4, [
        'chapter_script.shots[3].start_state',
        'chapter_script.shots[3].opening_frame.image_prompt'],
        '红牌已经亮起', '红牌已经亮起')
    camera = _field_issue(4, [
        'chapter_script.shots[3].camera', 'chapter_script.shots[3].location'],
        '认领区三盏灯', '打捞台', 'whole_shot', 'whole_shot')
    camera['kind'] = 'location'
    storyboard = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    during = _field_issue(19, [
        'chapter_script.shots[18].start_state', 'chapter_script.shots[18].event'],
        'C卡尚未加入', '镜内加入C卡', 't=0', 'during')
    valid = [false_start, camera, storyboard, during]
    calls = []

    def ask(parts, schema, **kwargs):
        calls.append({'text': parts[0]['text'], 'schema': schema, 'options': kwargs})
        name = kwargs['name']
        if name == 'pre_render_story_audit':
            return {'source_readable': True, 'source_problem': '',
                    'issues': [false_start,dict(camera, basis=['chapter_script.shots[3].missing.field']),storyboard,during]}
        if name == 'pre_render_audit_evidence':
            return {'issues':[{**camera,'issue_index':1}]}
        assert name == 'pre_render_audit_confirmation'
        return {'confirmed': [1, 2],
                'reason': '两个起始字段均已亮且镜内加入C合法；摄像机和storyboard是独立问题'}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'episode' / 'pre_render_story_audit.json'
    result = creative_audit.creative_audit(
        episode, sources, cache_path=cache_path, settings='review-settings')

    assert [call['options']['name'] for call in calls] == [
        'pre_render_story_audit', 'pre_render_audit_evidence', 'pre_render_audit_confirmation']
    assert all(call['options']['settings'] == 'review-settings' for call in calls)
    for call in calls:
        assert creative_audit.CREATIVE_TIME_CONTRACT in call['text']
        assert 'silences优先覆盖' in call['text']
        assert '原对白、原生环境声与动作音效' in call['text']
        assert '至少一条' in call['text'] and '当前stage' in call['text']
    grounded=copy.deepcopy(valid)
    for issue in grounded:preparation_rules.ground_creative_finding(issue,episode)
    assert result['answer']['issues'] == grounded[1:3]
    assert result['raw']['issues'] == grounded
    assert result['confirmation']['confirmed'] == [1, 2]
    assert [row['name'] for row in result['raw_response_history']]==[
        'pre_render_story_audit','pre_render_audit_evidence']
    assert result['raw_response_history'][0]['response']['issues'][1]['basis']==[
        'chapter_script.shots[3].missing.field']
    assert result['fingerprint'] == creative_audit.creative_fingerprint(episode, sources)
    assert json.loads(cache_path.read_text()) == result

    prompt = calls[0]['text']
    assert 'start_state/opening_frame/storyboard.frame=t=0' in prompt
    assert 'event/action_beats=during' in prompt and 'end_state=t=end' in prompt
    assert '允许跳切' in prompt
    payload = json.loads(prompt.rsplit('\n', 1)[1])
    adjacent = next(row for row in payload['adjacent_shots'] if row['stage'] == 4)
    assert adjacent['previous']['end_state'] == '第三镜实际结束：红牌仍在打捞台'
    assert adjacent['next']['start_state'] == '第五镜实际开始：切到登记棚'
    confirmation_prompt = calls[-1]['text']
    assert confirmation_prompt.count('红牌已经亮起') >= 2
    assert '认领区三盏灯' in confirmation_prompt and '逐项独立' in confirmation_prompt
    issue_schemas = calls[0]['schema']['properties']['issues']['items']['anyOf']
    by_origin = {item['properties']['evidence_origin']['enum'][0]:item for item in issue_schemas}
    assert set(by_origin) == {'original_text', 'authored_field'}
    assert 'basis' in by_origin['authored_field']['required']
    original_branches=[item['properties'] for item in issue_schemas
                       if item['properties']['evidence_origin']['enum']==['original_text']]
    field_branches=[item for item in original_branches if item['basis'].get('type')=='array']
    source_only=[item for item in original_branches if item['basis']=={'type':'null'}]
    assert field_branches and source_only
    from novel_manga.planning.preparation import CREATIVE_TIMES
    for item in field_branches:
        for name in ('observed_time','expected_time'):
            assert item[name]=={'type':'string','enum':sorted(CREATIVE_TIMES)}
    for item in source_only:
        for name in ('observed_time','expected_time'):
            assert item[name]=={'anyOf':[{'type':'string','enum':sorted(CREATIVE_TIMES)},{'type':'null'}]}

    cached = creative_audit.creative_audit(
        episode, sources, cache_path=cache_path, settings='review-settings')
    assert cached == result and len(calls) == 3


def test_creative_audit_schema_is_valid_for_the_real_strict_json_endpoint():
    schema = preparation_rules.audit_schema(creative=True)

    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert set(node['required']) == set(node['properties'])
                assert node['additionalProperties'] is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


def test_creative_wire_schema_separates_authored_fields_from_original_text():
    schema = preparation_rules.audit_schema(creative=True)
    alternatives = schema['properties']['issues']['items'].get('anyOf', [])
    branches = {item['properties']['evidence_origin']['enum'][0]:item for item in alternatives}
    assert set(branches) == {'authored_field', 'original_text'}
    authored = branches['authored_field']['properties']
    original = branches['original_text']['properties']
    assert authored['source_segment']['enum'] == ['']
    assert authored['basis']['type'] == 'array' and authored['basis']['minItems'] == 2
    assert original['source_segment']['minLength'] == 1


def test_creative_confirmation_keeps_verdict_and_reason_on_the_same_issue():
    from novel_manga.application.preparation import audit as creative_audit
    confirmation = {'decisions':[
        {'issue_index':1, 'decision':'keep', 'reason':'两个明确零点字段相反'},
        {'issue_index':0, 'decision':'withdraw', 'reason':'该句有依次和最终，属于镜内变化'},
    ]}
    assert creative_audit._confirmation_indexes(confirmation, 2) == {1}
    for rows in [confirmation['decisions'][:1], [confirmation['decisions'][0]]*2]:
        with pytest.raises(ValueError, match='confirmation'):
            creative_audit._confirmation_indexes({'decisions':rows}, 2)


def test_nullable_field_evidence_does_not_invent_a_basis_for_original_text():
    from novel_manga.application.preparation import audit as creative_audit
    issue = {'kind':'location', 'stage':4, 'source_quote':'', 'source_segment':'package',
             'evidence_origin':'original_text', 'problem':'地点与原文明示不符',
             'correction':'按原文明示地点修正',
             **{key:None for key in ('basis','observed','expected','observed_time','expected_time','relation')}}
    answer = {'source_readable':True, 'source_problem':'', 'issues':[issue]}
    creative_audit._ground_creative_answer(answer, _authored_review_episode(),
                                          {'package':'原文明确要求本镜位于打捞台。'})
    assert 'basis' not in issue and 'relation' not in issue
    assert issue['source_quote'] == '原文明确要求本镜位于打捞台。'


def test_original_text_findings_derive_episode_fields_and_still_bind_the_original_source():
    from novel_manga.application.preparation import audit as creative_audit
    issues = [
        _field_issue(4, ['chapter_script.shots[3].camera', 'chapter_script.shots[3].location'],
                     '合并而非逐字的机位描述', '打捞台', 'whole_shot', 'whole_shot'),
        _field_issue(19, ['chapter_script.shots[18].opening_frame.image_prompt',
                          'chapter_script.shots[18].storyboard.frame'],
                     '另一段合并原句', 'A、B、C三张目的地卡并排'),
    ]
    for issue in issues:issue.update(evidence_origin='original_text',source_segment='package')
    episode=_authored_review_episode()
    creative_audit._ground_creative_answer(
        {'source_readable':True,'source_problem':'','issues':issues},episode,{'package':'真实原包文本'})
    assert issues[0]['observed']==episode['chapter_script']['shots'][3]['camera']
    assert issues[1]['observed']==episode['chapter_script']['shots'][18]['opening_frame']['image_prompt']
    assert all(issue['source_quote']=='真实原包文本' for issue in issues)


def test_creative_evidence_correction_cannot_silently_clear_all_findings(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    invalid = _field_issue(4, ['chapter_script.shots[3].missing', 'chapter_script.shots[3].location'],
                           '错误引文', '打捞台')
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        return {'source_readable':True, 'source_problem':'',
                'issues':[invalid] if len(calls)==1 else []}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache = tmp_path/'audit.json'
    with pytest.raises(ValueError, match='cover every invalid issue_index'):
        creative_audit.creative_audit(_authored_review_episode(), {'package':'原始素材内容'}, cache_path=cache)
    assert not cache.exists()


def test_creative_field_pair_derives_evidence_in_the_selected_path_order():
    episode = _authored_review_episode()
    issue = _field_issue(4, ['chapter_script.shots[3].location', 'chapter_script.shots[3].camera'],
                         '认领区三盏灯', '打捞台', 'during', 'whole_shot')
    preparation_rules.ground_creative_finding(issue, episode)
    assert issue['basis'] == ['chapter_script.shots[3].location', 'chapter_script.shots[3].camera']
    assert issue['observed'] == episode['chapter_script']['shots'][3]['location']
    assert issue['expected'] == episode['chapter_script']['shots'][3]['camera']


@pytest.mark.parametrize('model_quote',['','camera path: move along the dock'])
@pytest.mark.parametrize('origin',['authored_field','original_text'])
def test_basis_evidence_comes_from_actual_leaf_before_independent_withdrawal(tmp_path,monkeypatch,model_quote,origin):
    import copy
    from novel_manga.application.preparation import audit as creative_audit
    episode=_authored_review_episode();shot=episode['chapter_script']['shots'][3]
    shot.update(camera='Toolbox medium. Camera path: locked observation.',location='旧灯塔门外')
    issue=_field_issue(4,['chapter_script.shots[3].camera','chapter_script.shots[3].location'],
        model_quote,'','during','whole_shot')
    issue['evidence_origin']=origin
    if origin=='original_text':issue['source_segment']='package'
    issue.update(problem='This camera moves along the dock in a tower shot.',correction='Use the tower wall camera route.')
    original=copy.deepcopy(issue);calls=[]
    def ask(parts,schema,**kwargs):
        calls.append(kwargs['name'])
        if kwargs['name']=='pre_render_story_audit':
            variants=schema['properties']['issues']['items']['anyOf']
            fields=next(item['properties'] for item in variants if item['properties']['evidence_origin']['enum']==[origin])
            for name in ('observed','expected'):
                if origin=='original_text':assert {'type':'string','enum':['']} in fields[name]['anyOf']
                else:assert fields[name]['enum']==['']
            return {'source_readable':True,'source_problem':'','issues':[copy.deepcopy(original)]}
        assert kwargs['name']=='pre_render_audit_confirmation'
        payload=json.loads(parts[0]['text'].rsplit('\n',1)[1]);actual=payload['issues'][0]
        assert actual['observed']==shot['camera'] and actual['expected']==shot['location']
        assert actual['problem']==original['problem'] and actual['correction']==original['correction']
        assert actual['relation']==original['relation'] and actual['basis']==original['basis']
        return {'decisions':[{'issue_index':0,'decision':'withdraw','reason':'Actual camera is locked; the alleged dock movement is absent.'}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=creative_audit.creative_audit(episode,{'package':'原包中真实完整的证据文字。'},cache_path=tmp_path/'audit.json')
    assert calls==['pre_render_story_audit','pre_render_audit_confirmation']
    assert result['answer']['issues']==[]
    assert result['raw']['issues'][0]['observed']==shot['camera']
    assert result['raw_response_history'][0]['response']['issues']==[original]
    if origin=='original_text':assert result['raw']['issues'][0]['source_quote']=='原包中真实完整的证据文字。'


def test_same_field_internal_contradiction_uses_full_leaf_and_still_needs_confirmation(tmp_path,monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode=_authored_review_episode();path='chapter_script.shots[18].opening_frame.image_prompt'
    issue=_field_issue(19,[path,path],'','','t=0','t=0')
    issue.update(problem='The same opening says three cards and also C not yet present.',correction='Make the opening count consistent.')
    def ask(parts,schema,**kwargs):
        if kwargs['name']=='pre_render_story_audit':return {'source_readable':True,'source_problem':'','issues':[issue]}
        assert kwargs['name']=='pre_render_audit_confirmation'
        actual=json.loads(parts[0]['text'].rsplit('\n',1)[1])['issues'][0]
        field=episode['chapter_script']['shots'][18]['opening_frame']['image_prompt']
        assert actual['observed']==actual['expected']==field
        return {'decisions':[{'issue_index':0,'decision':'keep','reason':'The complete same-time field contains both mutually exclusive counts.'}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=creative_audit.creative_audit(episode,{'package':'原文'},cache_path=tmp_path/'audit.json')
    assert len(result['answer']['issues'])==1
    assert result['raw_response_history'][0]['response']['issues'][0]['observed']==''


def test_creative_evidence_accepts_the_actual_episode_payload_envelope():
    issue = _field_issue(19, [
        'episode.chapter_script.shots[18].storyboard.frame',
        'episode.chapter_script.shots[18].start_state'],
        'A、B、C三张目的地卡并排', 'C卡尚未加入')
    preparation_rules.ground_creative_finding(issue, _authored_review_episode())
    assert issue['basis'] == ['chapter_script.shots[18].storyboard.frame',
                              'chapter_script.shots[18].start_state']


def test_confirmation_references_original_source_without_copying_it_per_finding(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    source = '原包明确写着完整事实和说话人。' * 100
    issue = _legacy_original_issue('speaker', '')
    def ask(parts, schema, **kwargs):
        if kwargs['name'] == 'pre_render_story_audit':
            return {'source_readable':True, 'source_problem':'', 'issues':[issue]}
        payload = json.loads(parts[0]['text'].rsplit('\n', 1)[1])
        assert payload['sources'] == {'package':source}
        assert payload['issues'][0]['source_quote'] == ''
        assert payload['issues'][0]['source_segment'] == 'package'
        return {'confirmed':[0], 'reason':'原文问题成立'}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = creative_audit.creative_audit(_authored_review_episode(), {'package':source},
                                          cache_path=tmp_path/'audit.json')
    assert result['raw']['issues'][0]['source_quote'] == source


def test_creative_fingerprint_ignores_derived_plan_but_tracks_storyboard():
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '相同原包文本'}
    before = creative_audit.creative_fingerprint(episode, sources)
    episode['clip_plan'] = {'clips': [{'clip_id': 'derived'}]}
    assert creative_audit.creative_fingerprint(episode, sources) == before
    episode['chapter_script']['shots'][18]['storyboard']['frame'] += 'C卡仍在零点加入。'
    assert creative_audit.creative_fingerprint(episode, sources) != before


def test_creative_fingerprint_does_not_bind_absolute_source_paths():
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    first = creative_audit.creative_fingerprint(episode, {'/old/root/package/source.md': '相同原文'})
    second = creative_audit.creative_fingerprint(episode, {'/new/root/package/source.md': '相同原文'})
    assert first == second


def test_invalid_creative_finding_is_not_cached_after_evidence_retry(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, ['chapter_script.shots[18].missing.field',
                                'chapter_script.shots[18].storyboard.frame'],
                           'C卡尚未加入', 'A、B、C三张目的地卡并排')
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        return {'source_readable': True, 'source_problem': '', 'issues': [{**invalid, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='evidence path'):
        creative_audit.creative_audit(episode, {'package': '原文'}, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


def test_valid_field_paths_cannot_hide_an_invented_source_quote(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    invalid['evidence_origin'] = 'original_text'
    invalid['source_quote'] = '这是原包中不存在的原句'
    def ask(parts, schema, **kwargs):
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '字段有效也不能替伪造原文开脱'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**invalid, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='ungrounded source quotation'):
        creative_audit.creative_audit(episode, {'package': '真实原包文本'}, cache_path=cache_path)
    assert not cache_path.exists()


def test_creative_audit_service_failure_is_not_cached_as_a_verdict(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    cache_path = tmp_path / 'pre_render_story_audit.json'
    def fail(*args, **kwargs):
        raise RuntimeError('review service 503')
    monkeypatch.setattr('novel_manga.llm.client.ask_json', fail)
    with pytest.raises(RuntimeError, match='503'):
        creative_audit.creative_audit(
            _authored_review_episode(), {'package': '原文'}, cache_path=cache_path)
    assert not cache_path.exists()


@pytest.mark.parametrize('kind', ['speaker', 'identity', 'missing_event', 'action', 'location'])
def test_source_dependent_finding_requires_original_evidence_even_with_basis(tmp_path, monkeypatch, kind):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    invalid['kind'] = kind
    invalid['evidence_origin'] = 'original_text'
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '制作字段不能替代原文证据'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**invalid, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='ungrounded source quotation'):
        creative_audit.creative_audit(episode, {'package': '真实原包文本'}, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


@pytest.mark.parametrize('suffix,quote,required_time', [
    ('start_state', 'C卡尚未加入', 't=0'),
    ('opening_frame.image_prompt', 'C卡尚未加入', 't=0'),
    ('storyboard.frame', 'A、B、C三张目的地卡并排', 't=0'),
    ('event', '镜内加入C卡', 'during'),
    ('action_beats[0]', '镜内加入C卡', 'during'),
    ('storyboard.action_beats[0]', '镜内加入C卡', 'during'),
    ('end_state', 'A、B、C三张目的地卡并排', 't=end'),
])
def test_creative_grounding_binds_time_to_actual_evidence_path(suffix, quote, required_time):
    episode = _authored_review_episode()
    episode['chapter_script']['shots'][18]['action_beats'] = ['镜内加入C卡。']
    path = 'chapter_script.shots[18].' + suffix
    wrong_time = 'during' if required_time == 't=0' else 't=0'
    finding = _field_issue(19, [path, path], quote, quote, wrong_time, wrong_time)
    with pytest.raises(ValueError, match='evidence time'):
        preparation_rules.ground_creative_finding(finding, episode)
    finding['observed_time'] = finding['expected_time'] = required_time
    assert preparation_rules.ground_creative_finding(finding, episode) is None


def test_invalid_evidence_time_is_corrected_before_confirmation(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, [
        'chapter_script.shots[18].start_state', 'chapter_script.shots[18].event'],
        'C卡尚未加入', '镜内加入C卡', 't=0', 't=0')
    corrected = dict(invalid, expected_time='during',
                     observed=episode['chapter_script']['shots'][18]['start_state'],
                     expected=episode['chapter_script']['shots'][18]['event'])
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_story_audit':
            return {'source_readable': True, 'source_problem': '', 'issues': [{**invalid, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
        assert kwargs['name'] == 'pre_render_audit_confirmation'
        payload = json.loads(parts[0]['text'].rsplit('\n', 1)[1])
        assert payload['issues'][0]['observed_time'] == 't=0'
        assert payload['issues'][0]['expected_time'] == 'during'
        return {'confirmed': [], 'reason': '镜内加入C是合法过程'}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = creative_audit.creative_audit(
        episode, {'package': '原文'}, cache_path=tmp_path / 'pre_render_story_audit.json')
    assert calls == ['pre_render_story_audit', 'pre_render_audit_confirmation']
    assert result['raw']['issues'] == [corrected]
    assert result['raw_response_history'][0]['response']['issues'][0]['expected_time'] == 't=0'
    assert result['answer']['issues'] == []


def test_creative_grounding_rejects_a_shot_container_as_evidence():
    episode = _authored_review_episode()
    finding = _field_issue(19, [
        'chapter_script.shots[18]',
        'chapter_script.shots[18].opening_frame.image_prompt'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    with pytest.raises(ValueError, match='leaf'):
        preparation_rules.ground_creative_finding(finding, episode)


def test_creative_grounding_rejects_a_third_path_that_only_anchors_stage():
    episode = _authored_review_episode()
    finding = _field_issue(4, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame',
        'chapter_script.shots[3].camera'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    with pytest.raises(ValueError, match='two evidence paths'):
        preparation_rules.ground_creative_finding(finding, episode)


@pytest.mark.parametrize('stage',[4,19])
def test_global_music_reason_pair_does_not_authorize_an_arbitrary_stage(stage):
    episode=_authored_review_episode()
    episode['chapter_script']['music_plan']={'cues':[{'reason':'保持脉冲','start':{'shot':4,'edge':'start'},
        'end':{'shot':19,'edge':'end'}}], 'silences':[{'reason':'保留现场声','start':{'shot':4,'edge':'start'},
        'end':{'shot':19,'edge':'end'}}]}
    issue=_field_issue(stage,['chapter_script.music_plan.cues[0].reason',
        'chapter_script.music_plan.silences[0].reason'],'保持脉冲','保留现场声','whole_shot','whole_shot')
    with pytest.raises(ValueError,match='stage does not match'):
        preparation_rules.ground_creative_finding(issue,episode)


def test_final_issues_contain_only_confirmed_contradictions(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    contradiction = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    compatible = dict(contradiction, relation='compatible')
    underspecified = dict(contradiction, relation='underspecified')
    candidates = [compatible, underspecified, contradiction]
    def ask(parts, schema, **kwargs):
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0, 1, 2], 'reason': '即使确认候选，也不能消费非矛盾项'}
        return {'source_readable': True, 'source_problem': '', 'issues': candidates}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = creative_audit.creative_audit(
        episode, {'package': '原文'}, cache_path=tmp_path / 'pre_render_story_audit.json')
    assert result['raw']['issues'] == candidates
    assert result['answer']['issues'] == [contradiction]


def test_unreadable_source_with_issues_is_invalid_and_not_cached(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        return {'source_readable': False, 'source_problem': '原包不可读', 'issues': [invalid]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='source_readable|unreadable'):
        creative_audit.creative_audit(episode, {'package': '原文'}, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


def test_creative_fingerprint_preserves_same_named_logical_sources():
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    before = {'/old/root/a/source.md': '甲来源正文A', '/old/root/b/source.md': '乙来源正文B'}
    moved = {'/new/root/a/source.md': '甲来源正文A', '/new/root/b/source.md': '乙来源正文B'}
    swapped = {'/new/root/a/source.md': '乙来源正文B', '/new/root/b/source.md': '甲来源正文A'}
    assert creative_audit.creative_fingerprint(episode, before) == creative_audit.creative_fingerprint(episode, moved)
    assert creative_audit.creative_fingerprint(episode, before) != creative_audit.creative_fingerprint(episode, swapped)
    with pytest.raises(ValueError, match='collision'):
        creative_audit.creative_fingerprint(
            episode, {'/old/root/source.md': '甲来源正文A', 'source.md': '乙来源正文B'})


def test_cached_source_quote_rebinds_to_current_logical_segment(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    old_sources = {'/old/root/a/source.md': '甲在打捞台说：保留真实台词。',
                   '/old/root/b/source.md': '乙在登记棚说：另一句台词。'}
    new_sources = {'/new/root/a/source.md': '甲在打捞台说：保留真实台词。',
                   '/new/root/b/source.md': '乙在登记棚说：另一句台词。'}
    issue = {'kind': 'speaker', 'stage': 19, 'relation': 'contradiction',
             'evidence_origin': 'original_text',
             'source_segment': '/old/root/a/source.md', 'source_quote': '',
             'problem': '台词归属与原文不符', 'correction': '按所引原文修正说话人'}
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '所引区段支持该问题'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**issue, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    result = creative_audit.creative_audit(episode, old_sources, cache_path=cache_path)
    cached = creative_audit.creative_audit(episode, new_sources, cache_path=cache_path)
    assert cached == result and len(calls) == 2
    assert cached['raw']['issues'][0]['source_segment'] == 'a/source.md'


def test_cached_quote_must_exist_in_current_cited_segment(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'/root/a/source.md': '当前甲段已经换成全新文本。',
               '/root/b/source.md': '甲在打捞台说：旧引文现在只在乙段。'}
    issue = {'kind': 'speaker', 'stage': 19, 'relation': 'contradiction',
             'evidence_origin': 'original_text',
             'source_segment': 'a/source.md', 'source_quote': '甲在打捞台说：旧引文现在只在乙段。',
             'problem': '台词归属与原文不符', 'correction': '按所引原文修正说话人'}
    raw = {'source_readable': True, 'source_problem': '', 'issues': [issue]}
    saved = {'fingerprint': creative_audit.creative_fingerprint(episode, sources),
             'answer': raw, 'raw': raw,
             'confirmation': {'confirmed': [0], 'reason': '旧确认不能跨区段挂靠'}}
    cache_path = tmp_path / 'pre_render_story_audit.json'
    cache_path.write_text(json.dumps(saved, ensure_ascii=False))
    monkeypatch.setattr('novel_manga.llm.client.ask_json',
                        lambda *a, **k: pytest.fail('invalid cached citation must not trigger a new review'))
    with pytest.raises(ValueError, match='invalid cached creative audit'):
        creative_audit.creative_audit(episode, sources, cache_path=cache_path)


@pytest.mark.parametrize('origin', [None, 'problem_text'])
def test_creative_issue_requires_explicit_evidence_origin(tmp_path, monkeypatch, origin):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    invalid = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    if origin is None:
        invalid.pop('evidence_origin')
    else:
        invalid['evidence_origin'] = origin
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '来源声明不能省略或猜写'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**invalid, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='evidence_origin'):
        creative_audit.creative_audit(episode, {'package': '原文'}, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


def _legacy_original_issue(kind, quote):
    return {'kind': kind, 'stage': 0 if kind == 'missing_event' else 19,
            'evidence_origin': 'original_text',
            'source_segment': 'package', 'source_quote': quote,
            'problem': '原文事实未被正确表达', 'correction': '按所引原文修正'}


@pytest.mark.parametrize('kind', ['speaker', 'missing_event'])
def test_legacy_original_issue_without_relation_survives_fresh_review(tmp_path, monkeypatch, kind):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原包明确写着完整事实和说话人。'}
    issue = _legacy_original_issue(kind, '')
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '旧式原文问题仍成立'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**issue, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = creative_audit.creative_audit(
        episode, sources, cache_path=tmp_path / 'pre_render_story_audit.json')
    assert calls == ['pre_render_story_audit', 'pre_render_audit_confirmation']
    assert result['answer']['issues'] == [{**issue,'source_quote':sources['package']}]
    assert 'basis' not in issue and 'relation' not in issue


@pytest.mark.parametrize('kind', ['speaker', 'missing_event'])
def test_legacy_original_issue_without_relation_survives_cache_reuse(tmp_path, monkeypatch, kind):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原包明确写着完整事实和说话人。'}
    issue = _legacy_original_issue(kind, sources['package'])
    raw = {'source_readable': True, 'source_problem': '', 'issues': [issue]}
    saved = {'fingerprint': creative_audit.creative_fingerprint(episode, sources),
             'answer': raw, 'raw': raw,
             'confirmation': {'confirmed': [0], 'reason': '旧式原文问题仍成立'}}
    cache_path = tmp_path / 'pre_render_story_audit.json'
    cache_path.write_text(json.dumps(saved, ensure_ascii=False))
    monkeypatch.setattr('novel_manga.llm.client.ask_json',
                        lambda *a, **k: pytest.fail('valid old-style cache must be reused'))
    result = creative_audit.creative_audit(episode, sources, cache_path=cache_path)
    assert result == saved
    assert result['answer']['issues'] == [issue]
    assert 'basis' not in issue and 'relation' not in issue


@pytest.mark.parametrize('relation', ['', 'not-a-relation'])
def test_invalid_original_relation_is_corrected_once_and_not_cached(tmp_path, monkeypatch, relation):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原包明确写着完整事实和说话人。'}
    issue = _legacy_original_issue('speaker', '')
    issue['relation'] = relation
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '非法relation不得靠确认放行'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**issue, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='relation'):
        creative_audit.creative_audit(episode, sources, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


@pytest.mark.parametrize('relation', [None, 'not-a-relation'])
def test_invalid_original_relation_is_rejected_on_cache_hit(tmp_path, monkeypatch, relation):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原包明确写着完整事实和说话人。'}
    issue = _legacy_original_issue('speaker', sources['package'])
    issue['relation'] = relation
    raw = {'source_readable': True, 'source_problem': '', 'issues': [issue]}
    saved = {'fingerprint': creative_audit.creative_fingerprint(episode, sources),
             'answer': raw, 'raw': raw,
             'confirmation': {'confirmed': [0], 'reason': '非法relation不得靠旧确认放行'}}
    cache_path = tmp_path / 'pre_render_story_audit.json'
    cache_path.write_text(json.dumps(saved, ensure_ascii=False))
    monkeypatch.setattr('novel_manga.llm.client.ask_json',
                        lambda *a, **k: pytest.fail('invalid cached relation must not trigger a new review'))
    with pytest.raises(ValueError, match='invalid cached creative audit'):
        creative_audit.creative_audit(episode, sources, cache_path=cache_path)
    assert json.loads(cache_path.read_text()) == saved


def test_creative_audit_excludes_derived_prompts_from_prompt_fingerprint_and_cache(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    shot = episode['chapter_script']['shots'][0]
    shot.update(visual_prompt='STALE_VISUAL_MUST_NOT_BE_REVIEWED',
                motion_prompt='STALE_MOTION_MUST_NOT_BE_REVIEWED')
    sources = {'package': '原文'}
    before = creative_audit.creative_fingerprint(episode, sources)
    calls = []

    def ask(parts, schema, **kwargs):
        calls.append(parts[0]['text'])
        return {'source_readable': True, 'source_problem': '', 'issues': []}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    result = creative_audit.creative_audit(episode, sources, cache_path=cache_path)
    payload = json.loads(calls[0].rsplit('\n', 1)[1])
    audited_shot = payload['episode']['chapter_script']['shots'][0]
    assert 'STALE_' not in calls[0]
    assert 'visual_prompt' not in audited_shot and 'motion_prompt' not in audited_shot
    assert result['fingerprint'] == before

    shot.update(visual_prompt='ANOTHER_STALE_VISUAL', motion_prompt='ANOTHER_STALE_MOTION')
    assert creative_audit.creative_fingerprint(episode, sources) == before
    assert creative_audit.creative_audit(episode, sources, cache_path=cache_path) == result
    assert len(calls) == 1


def test_creative_grounding_rejects_derived_prompt_as_an_author_fact():
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    episode['chapter_script']['shots'][0]['visual_prompt'] = '旧派生画面'
    finding = _field_issue(1, [
        'chapter_script.shots[0].visual_prompt',
        'chapter_script.shots[0].start_state'],
        '旧派生画面', '镜头1实际起点')
    answer = {'source_readable': True, 'source_problem': '', 'issues': [finding]}
    with pytest.raises(ValueError, match='evidence path'):
        creative_audit._ground_creative_answer(answer, episode, {'package': '原文'})


@pytest.mark.parametrize('relation', ['missing', None, 'not-a-relation'])
def test_authored_field_relation_must_be_explicit_and_valid(tmp_path, monkeypatch, relation):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    issue = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    if relation == 'missing':
        issue.pop('relation')
    else:
        issue['relation'] = relation
    calls = []

    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'pre_render_audit_confirmation':
            return {'confirmed': [0], 'reason': '非法relation不得靠确认放行'}
        return {'source_readable': True, 'source_problem': '', 'issues': [{**issue, **({'issue_index':0} if kwargs['name']=='pre_render_audit_evidence' else {})}]}

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    cache_path = tmp_path / 'pre_render_story_audit.json'
    with pytest.raises(ValueError, match='relation'):
        creative_audit.creative_audit(episode, {'package': '原文'}, cache_path=cache_path)
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not cache_path.exists()


def test_authored_field_without_relation_is_rejected_on_cache_hit(tmp_path, monkeypatch):
    from novel_manga.application.preparation import audit as creative_audit
    episode = _authored_review_episode()
    sources = {'package': '原文'}
    issue = _field_issue(19, [
        'chapter_script.shots[18].opening_frame.image_prompt',
        'chapter_script.shots[18].storyboard.frame'],
        'C卡尚未加入', 'A、B、C三张目的地卡并排')
    issue.pop('relation')
    raw = {'source_readable': True, 'source_problem': '', 'issues': [issue]}
    saved = {'fingerprint': creative_audit.creative_fingerprint(episode, sources),
             'answer': raw, 'raw': raw,
             'confirmation': {'confirmed': [0], 'reason': '旧确认不能补写缺失的relation'}}
    cache_path = tmp_path / 'pre_render_story_audit.json'
    cache_path.write_text(json.dumps(saved, ensure_ascii=False))
    monkeypatch.setattr('novel_manga.llm.client.ask_json',
                        lambda *a, **k: pytest.fail('invalid cached relation must not trigger a new review'))
    with pytest.raises(ValueError, match='invalid cached creative audit'):
        creative_audit.creative_audit(episode, sources, cache_path=cache_path)
    assert json.loads(cache_path.read_text()) == saved
