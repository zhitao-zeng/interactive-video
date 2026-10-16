import copy,json
from pathlib import Path
from novel_manga.application.review import confirmed
from novel_manga.application.review.store import import_confirmed_findings
from novel_manga.application.repair.managed import candidates
from support.managed_episode import fixture_episode


def test_confirmed_failure_enters_repair_and_cannot_be_cleared_by_broad_pass(tmp_path):
    d,clips,rows=fixture_episode(tmp_path)
    report=json.loads((d/'episode_review.json').read_text());report['feedback']={}
    for row in report['clips'].values():row.update(severity='pass',tier='ignore',verify={'verdict':'fine'})
    (d/'episode_review.json').write_text(json.dumps(report))
    finding={**{k:rows['a'][k] for k in ['video','take']},'issue':'前景和背景各有一个身体','instruction':'同框一个人'}
    old=import_confirmed_findings(d,{'a':finding})
    assert candidates(d)[0]==['a']
    fresh=copy.deepcopy(report)
    enforced=confirmed.enforce(d,fresh,old)
    assert enforced['feedback']['a']=='同框一个人'
    assert enforced['clips']['a']['severity']=='fail'


def test_replacement_needs_specific_confirmation_tied_to_its_take(tmp_path,monkeypatch):
    d,clips,rows=fixture_episode(tmp_path)
    claim={**{k:rows['a'][k] for k in ['video','take']},'issue':'重复','instruction':'一个人'}
    previous=import_confirmed_findings(d,{'a':claim})
    fresh=copy.deepcopy(previous);fresh['feedback']={}
    fresh['clips']['a'].update(take=[1,2,999],severity='pass',verify={'verdict':'fine'})
    assert 'a' in confirmed.enforce(d,fresh,previous)['feedback']
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a:{'result':'resolved','observations':['一人'], 'evidence':'只有一个身体'})
    checked=confirmed.enforce(d,fresh,previous,verify=True)
    assert not checked['feedback'] and checked['clips']['a']['confirmed']['check']['take']==[1,2,999]
    fresh['clips']['a']['take']=[4,5,6]
    assert 'a' in confirmed.enforce(d,fresh,checked)['feedback']


def test_review_uses_postmixed_take_for_binding(tmp_path):
    from novel_manga.application.review.store import current_takes
    d,clips,rows=fixture_episode(tmp_path)
    mixed=d/'mixed.mp4';mixed.write_bytes(b'mixed')
    media=json.loads((d/'thin_media_report.json').read_text());media['clips'][0]['selected']['postmix_video']=str(mixed)
    (d/'thin_media_report.json').write_text(json.dumps(media))
    assert current_takes(d,{'clips':clips},{})['a']['video']==str(mixed)


def test_positive_checks_have_one_unambiguous_aggregate_result():
    from novel_manga.application.review.confirmed import finding_result
    good={'observations':['两人正常站立，没有穿插'], 'checks':[
        {'criterion':'正常成年人体型','met':True,'evidence':'两人正常比例'},
        {'criterion':'独立空间','met':True,'evidence':'没有身体穿插'}]}
    assert finding_result(good)['result']=='resolved'
    assert finding_result({'checks':[{'met':False,'evidence':'开场面罩打开'}]})['result']=='unresolved'
    assert finding_result({'checks':[{'met':None,'evidence':'看不清'}]})['result']=='uncertain'


def test_visual_review_does_not_invent_a_speech_failure_from_silent_frames():
    from novel_manga.application.review.confirmed import finding_result
    answer=finding_result({'checks':[{'criterion':'两人各占独立空间','scope':'visual','met':True,'evidence':'画面符合'},
                                    {'criterion':'台词保持','scope':'audio','met':None,'evidence':'帧没有音轨'}]})
    assert answer['result']=='resolved' and answer['checked_scope']=='visual'
    assert answer['checks'][1]['met'] is None


def test_targeted_check_receives_named_current_character_references(tmp_path, monkeypatch):
    seen={}
    monkeypatch.setattr(confirmed,'clip_frames',lambda *a:[tmp_path/f'frame{i}.jpg' for i in range(6)])
    monkeypatch.setattr(confirmed,'image_part',lambda path,width:{'type':'image','file':str(path)})
    def ask(parts,*a,**kw):
        seen['parts']=parts
        return {'observations':['两人'],'checks':[{'criterion':'头盔闭合','scope':'visual','met':True,'evidence':'闭合'}]}
    monkeypatch.setattr(confirmed,'ask_json',ask)
    clip={'references':[{'role':'character','name':'医生','path':'doctor.jpg'},
                        {'role':'character','name':'穿甲人','path':'closed.jpg','view':'closed'}]}
    answer=confirmed.judge_finding(clip,tmp_path/'video.mp4',{'instruction':'面罩保持闭合'},tmp_path/'frames',tmp_path)
    assert seen['parts'][0]['text'].endswith('医生')
    assert seen['parts'][1]['file']==str(tmp_path/'doctor.jpg')
    assert '穿甲人' in seen['parts'][2]['text'] and '不是无人空甲' in seen['parts'][2]['text']
    assert answer['result']=='resolved'


def test_confirmed_visual_judge_does_not_follow_preparation_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_H3_TRANSLATION_ENDPOINT', 'flashnext')
    monkeypatch.setenv('NOVEL_H3_REQUEST_CHECK_ENDPOINT', 'flashnext')
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', 'deepseek')
    monkeypatch.setattr(confirmed, 'clip_frames', lambda *a: [tmp_path / 'frame.jpg'])
    monkeypatch.setattr(confirmed, 'image_part', lambda *a: {'type': 'image'})
    seen = {}

    def ask(parts, schema, **kwargs):
        seen['settings'] = kwargs['settings']
        return {'observations': ['一个人'], 'checks': [
            {'criterion': '一个人', 'scope': 'visual', 'met': True, 'evidence': '同框一个人'}]}

    monkeypatch.setattr(confirmed, 'ask_json', ask)
    answer = confirmed.judge_finding({}, tmp_path / 'video.mp4',
                                    {'instruction': '同框一个人'}, tmp_path / 'frames')
    assert answer['result'] == 'resolved'
    assert seen['settings'].model == 'DeepSeek-V4.1-Flash'
    assert seen['settings'].endpoints == ('http://172.28.4.52:8831/v1',)


def test_a_finding_is_not_rejudged_on_the_take_it_was_confirmed_on(tmp_path, monkeypatch):
    """ch12-1 clip_19/20 (2026-09-26): re-judging the confirmed frames cleared two human-confirmed findings."""
    d,clips,rows=fixture_episode(tmp_path)
    claim={**{k:rows['a'][k] for k in ['video','take']},'issue':'悬空机械臂','instruction':'没有分离的装甲部件'}
    previous=import_confirmed_findings(d,{'a':claim})
    fresh=copy.deepcopy(previous);fresh['feedback']={}
    fresh['clips']['a'].update(severity='pass',verify={'verdict':'fine'})
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a,**k:(_ for _ in ()).throw(AssertionError('re-judged')))
    enforced=confirmed.enforce(d,fresh,previous,verify=True)
    assert enforced['feedback']['a']=='没有分离的装甲部件' and enforced['clips']['a']['severity']=='fail'
    # a check an earlier run wrote on that same take does not clear it either
    previous['clips']['a']['confirmed']['check']={'result':'resolved','video':rows['a']['video'],'take':rows['a']['take']}
    assert 'a' in confirmed.enforce(d,copy.deepcopy(fresh),previous,verify=True)['feedback']



def test_targeted_judge_receives_actual_source_instead_of_accepting_old_claim(tmp_path, monkeypatch):
    from novel_manga.util import atomic_write_json
    episode = tmp_path/'book_1'
    atomic_write_json(episode/'segments.json', [{'segment_id':'s1','text':'三个男人同时走入大厅。'}])
    work = episode/'work/confirmed_review/clip_01'
    seen = {}
    monkeypatch.setattr(confirmed, 'clip_frames', lambda *a: [tmp_path/'frame.jpg'])
    monkeypatch.setattr(confirmed, 'image_part', lambda *a: {'type':'image'})
    def ask(parts, *a, **kw):
        seen['text'] = '\n'.join(p.get('text','') for p in parts)
        return {'observations':['三人'], 'checks':[{'scope':'visual','met':True,'evidence':'三人'}]}
    monkeypatch.setattr(confirmed, 'ask_json', ask)
    confirmed.judge_finding({'segment_ids':['s1']},tmp_path/'video.mp4',
        {'instruction':'一个女人走入大厅'},work,tmp_path)
    assert '三个男人同时走入大厅' in seen['text']
    assert '不是事实' in seen['text']


def test_false_review_premise_stays_unaccepted_and_routes_to_input_evidence():
    result = confirmed.finding_result({'premise_valid':False,'premise_evidence':'原文三男，卡面是女',
        'observations':['卡面与原文矛盾'], 'checks':[{'scope':'visual','met':False,'evidence':'三人'}]})
    assert result['result'] == 'uncertain'
    assert result['error_kind'] == 'review_premise_conflict'


def test_positive_take_evidence_does_not_become_a_failure_claim(tmp_path, monkeypatch):
    d, clips, rows = fixture_episode(tmp_path)
    previous = json.loads((d/'episode_review.json').read_text())
    previous['clips']['a']['confirmed'] = {'check': {
        **{k: rows['a'][k] for k in ['video', 'take']},
        'result': 'resolved', 'checked_scope': 'visual', 'observations': ['一个人正常站立']}}
    fresh = copy.deepcopy(previous)
    fresh['clips']['a'].update(severity='fail', defect_issue='当前评审发现穿模')
    fresh['feedback'] = {'a': '修正当前穿模'}
    monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **kw:
        (_ for _ in ()).throw(AssertionError('positive evidence is not a failure target')))
    result = confirmed.enforce(d, fresh, previous, verify=True)
    assert result['clips']['a']['severity'] == 'fail'
    assert result['feedback']['a'] == '修正当前穿模'


def test_resolved_old_finding_does_not_replace_new_failure_instructions(tmp_path):
    d, clips, rows = fixture_episode(tmp_path)
    previous = json.loads((d/'episode_review.json').read_text())
    current = {k: rows['a'][k] for k in ['video', 'take']}
    previous['clips']['a']['confirmed'] = {
        'video': current['video'], 'take': [0, 0, 0],
        'issue': '旧镜头复制人物', 'instruction': '只保留一个人',
        'check': {**current, 'result': 'resolved'}}
    fresh = copy.deepcopy(previous)
    fresh['clips']['a'].update(severity='fail', defect_issue='当前镜头穿模')
    fresh['clips']['a'].pop('feedback', None)
    fresh['feedback'] = {'a': '修正当前穿模'}
    result = confirmed.enforce(d, fresh, previous)
    assert result['feedback']['a'] == '修正当前穿模'


def test_current_speech_instructions_survive_enforcing_a_picture_finding(tmp_path):
    d, clips, rows = fixture_episode(tmp_path)
    previous = json.loads((d/'episode_review.json').read_text())
    current = {k: rows['a'][k] for k in ['video', 'take']}
    claim = {'video': current['video'], 'take': [0, 0, 0],
        'issue': '旧镜头复制人物', 'instruction': '只保留一个人',
        'check': {**current, 'result': 'resolved'}}
    previous['clips']['a']['confirmed'] = claim
    fresh = copy.deepcopy(previous)
    fresh['clips']['a'].update(severity='fail', feedback='修正当前穿模', speech_gate={'passed':False})
    fresh['feedback'] = {'a':'修正当前穿模\n删除多说的台词'}
    assert confirmed.enforce(d, fresh, previous)['feedback']['a'] == fresh['feedback']['a']
    previous['clips']['a']['confirmed']['check']['result'] = 'unresolved'
    result = confirmed.enforce(d, fresh, previous)
    for instruction in ['修正当前穿模', '删除多说的台词', '只保留一个人']:
        assert instruction in result['feedback']['a']


def test_scoped_criteria_recheck_only_matching_clip_and_cache_resolved_result(tmp_path, monkeypatch):
    from novel_manga.util import atomic_write_json
    d, clips, rows = fixture_episode(tmp_path)
    criteria = ['换机位本身可接受；核对人物、座位、行李和花的空间关系。']
    atomic_write_json(d / 'review_criteria.json', {'clips': {'a': criteria}})
    old_criteria = ['旧标准：不允许换机位。']
    previous = json.loads((d / 'episode_review.json').read_text())
    for cid in ('a', 'b'):
        current = {k: rows[cid][k] for k in ('video', 'take')}
        previous['clips'][cid]['confirmed'] = {
            'video': current['video'] if cid == 'a' else 'older.mp4',
            'take': current['take'] if cid == 'a' else [0, 0, 0],
            'issue': '旧判词', 'instruction': '核实空间关系',
            'check': {**current, 'result': 'resolved' if cid == 'a' else 'unresolved',
                      'review_criteria': old_criteria}}
    fresh = copy.deepcopy(previous)
    for row in fresh['clips'].values():
        row.update(severity='pass', tier='optional')
    calls = []

    def judge(clip, video, claim, work, novel_dir=None, review_criteria=()):
        calls.append((clip.get('clip_id'), list(review_criteria)))
        return {'result': 'resolved' if review_criteria else 'unresolved', 'observations': ['当前take'],
                'evidence': '当前帧已核对'}

    monkeypatch.setattr(confirmed, 'judge_finding', judge)
    checked = confirmed.enforce(d, fresh, previous, verify=True)
    assert calls == [('a', criteria)]
    assert checked['clips']['a']['severity'] in {'pass', 'minor'}
    assert checked['clips']['a']['confirmed']['check']['review_criteria'] == criteria
    assert checked['clips']['b']['severity'] == 'fail'

    calls.clear()
    again = confirmed.enforce(d, checked, checked, verify=True)
    assert calls == []
    assert again['clips']['a']['severity'] in {'pass', 'minor'}


def test_scoped_criteria_do_not_clear_a_real_spatial_defect(tmp_path, monkeypatch):
    from novel_manga.util import atomic_write_json
    d, clips, rows = fixture_episode(tmp_path)
    criteria = ['允许镜头切换和轻微碰花。']
    atomic_write_json(d / 'review_criteria.json', {'clips': {'a': criteria}})
    previous = json.loads((d / 'episode_review.json').read_text())
    current = {k: rows['a'][k] for k in ('video', 'take')}
    previous['clips']['a']['confirmed'] = {
        'video': 'older.mp4', 'take': [0, 0, 0], 'issue': '座位与行李空间关系混乱',
        'instruction': '核对座位和行李', 'check': {**current, 'result': 'unresolved'}}
    fresh = copy.deepcopy(previous)
    monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **kw: {
        'result': 'unresolved', 'observations': ['行李挡住错误座位'], 'evidence': '行李与座位位置仍混乱'})
    result = confirmed.enforce(d, fresh, previous, verify=True)
    assert result['clips']['a']['severity'] == 'fail'
    assert 'a' in result['feedback']
