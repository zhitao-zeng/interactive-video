import copy,json
import pytest
from pathlib import Path
from novel_manga.application.review import confirmed
from novel_manga.application.review.store import import_confirmed_findings
from novel_manga.application.repair.managed import candidates
from support.managed_episode import fixture_episode


def test_policy_refresh_checkpoint_keeps_unreviewed_findings_without_reusing_old_verdict(tmp_path,monkeypatch):
    from novel_manga.application.review import episode,cast_video
    d,clips,rows=fixture_episode(tmp_path)
    (d.parent/'story_bible.json').write_text(json.dumps({'novel_title':'test','genre':'travel',
        'visual_style':'live','palette':'green','style_fingerprint':'f','characters':[]}))
    report=json.loads((d/'episode_review.json').read_text());report.update(policy='old-review',review_mode='classic')
    (d/'episode_review.json').write_text(json.dumps(report))
    import_confirmed_findings(d,{'b':{**{k:rows['b'][k] for k in ['video','take']},
        'issue':'raw contains dialogue captions','instruction':'clean image without native captions'}})
    monkeypatch.setattr(episode.review_evidence,'load_review_rules',lambda *a:{})
    monkeypatch.setattr(episode.review_evidence,'review_mode',lambda *a:'classic')
    monkeypatch.setattr(cast_video,'enabled',lambda:False)
    called=[]
    def interrupted(clip,*a,**k):
        called.append(clip['clip_id'])
        if clip['clip_id']=='b':raise KeyboardInterrupt()
        return {'severity':'pass','identity_ok':True}
    monkeypatch.setattr(episode.review_judges,'judge_clip',interrupted)
    with pytest.raises(KeyboardInterrupt):episode.review_episode(d,fresh=False)
    saved=json.loads((d/'episode_review.json').read_text())
    assert saved['clips']['b']['confirmed']['issue']=='raw contains dialogue captions'
    assert saved['clips']['b']['policy']=='old-review'
    called.clear()
    monkeypatch.setattr(episode.review_judges,'judge_clip',lambda clip,*a,**k:
        called.append(clip['clip_id']) or {'severity':'pass','identity_ok':True})
    final=episode.review_episode(d,fresh=False)
    assert called==['b'] and final['clips']['b']['confirmed']


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


def test_resolved_claim_restores_broad_pass_and_preserves_unrelated_failure():
    from novel_manga.application.review.confirmed import apply_claim
    video='current.mp4';take=[1,2,3]
    claim={'video':'old.mp4','take':[0,0,0],'issue':'old eye box','instruction':'visible eyes'}
    for severity in ['pass','fail']:
        base={'severity':severity,'story_ok':severity=='pass','story_issue':'different failure' if severity=='fail' else ''}
        unresolved=apply_claim(base,claim,video,take)
        assert unresolved['severity']=='fail'
        resolved={**claim,'check':{'video':video,'take':take,'result':'resolved'}}
        result=apply_claim(unresolved,resolved,video,take)
        assert result['severity']==severity
        assert result['story_issue']==base['story_issue']


@pytest.mark.parametrize('severity,tier', [('pass',None),('minor',None),('fail','optional'),('fail','must_fix')])
def test_resolved_overlay_clears_its_stale_retake_feedback(tmp_path,severity,tier):
    d,clips,rows=fixture_episode(tmp_path)
    video=rows['a']['video'];take=rows['a']['take']
    claim={'video':'old.mp4','take':[0,0,0],'issue':'old eye box','instruction':'visible eyes',
           'check':{'video':video,'take':take,'result':'resolved'}}
    row=confirmed.apply_claim({'video':video,'take':take,'severity':severity,'tier':tier,
                               'identity_ok':False,'feedback':'keep each person distinct'},
                              {k:v for k,v in claim.items() if k!='check'},video,take)
    report={'clips':{'a':row},'feedback':{'a':'visible eyes'}}
    previous={'clips':{'a':{'confirmed':claim}}}
    result=confirmed.enforce(d,report,previous)
    assert result['clips']['a']['severity']==severity
    assert ('a' in result['feedback']) is (tier=='must_fix')
    if tier=='must_fix':assert result['feedback']['a']=='keep each person distinct'
    assert ('a' in candidates(d,result)[0]) is (tier=='must_fix')


def test_resolved_claim_preserves_an_independent_current_repair_instruction(tmp_path):
    d,clips,rows=fixture_episode(tmp_path)
    video=rows['a']['video'];take=rows['a']['take']
    claim={'video':'old.mp4','take':[0,0,0],'issue':'old captions','instruction':'remove native captions',
           'check':{'video':video,'take':take,'result':'resolved'}}
    base={'video':video,'take':take,'severity':'fail','tier':'must_fix','visual_defects':True,
          'visual_defect_kind':'spatial','feedback':'two bodies occupy separate space'}
    row=confirmed.apply_claim(base,{k:v for k,v in claim.items() if k!='check'},video,take)
    report={'clips':{'a':row},'feedback':{'a':'two bodies occupy separate space'}}
    result=confirmed.enforce(d,report,{'clips':{'a':{'confirmed':claim}}})
    assert result['feedback']['a']=='two bodies occupy separate space'
    assert result['clips']['a']['severity']=='fail' and candidates(d,result)[0]==['a']


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
        return {'observations':['两人'],'checks':[{'criterion':'面罩保持闭合','scope':'visual','met':True,'evidence':'闭合'}]}
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


def test_real_clip23_scope_drift_is_corrected_and_clip20_phone_stays_a_failure(tmp_path, monkeypatch):
    # Extracted from the real clip_23/clip_20 confirmed checks: clip_23 added
    # unchanged device states; clip_20 tried to explain away a visible phone.
    target23 = ('保持G15-02拆机前设备状态：后置画面不再出现从握柄到可见空端的完整独立已拆空杆。'
                '拍摄手机仍在镜头后方画外；自身手脚或部分杆柄可自然出现，连接处不入画不判缺失，'
                '不要求手机自身可见，也不将此条件套到G16-02。')
    target20 = ('保持G13-01拆机前设备状态：后置画面不再出现从握柄到可见空端的完整独立已拆空杆。'
                '自己的拍摄手机和屏幕保持画外；自身手脚、部分杆柄可自然入画，连接处在画外不因未显示判失败，'
                '不要求拍摄手机出现在自身画面。')
    first23 = {'observations':['六帧无杆，脚停浅台'], 'checks':[
        {'criterion':'头灯仍藏原背包内未开启，手机与背包干燥','scope':'visual','met':None,'evidence':'未入画'},
        {'criterion':'后置画面不再出现从握柄到可见空端的完整独立已拆空杆','scope':'visual','met':True,'evidence':'无杆'}]}
    corrected23 = {'observations':['六帧无杆，脚停浅台'], 'checks':[
        {'criterion':'后置画面不再出现从握柄到可见空端的完整独立已拆空杆','scope':'visual','met':True,'evidence':'无杆'},
        {'criterion':'拍摄手机仍在镜头后方画外','scope':'visual','met':True,'evidence':'无手机入画'}]}
    monkeypatch.setattr(confirmed, 'clip_frames', lambda *a: [tmp_path/'frame.jpg'])
    monkeypatch.setattr(confirmed, 'image_part', lambda *a: {'type':'image'})
    answers = iter([first23, corrected23])
    monkeypatch.setattr(confirmed, 'ask_json', lambda *a, **k: copy.deepcopy(next(answers)))
    result23 = confirmed.judge_finding({}, tmp_path/'v.mp4', {'instruction':target23}, tmp_path/'frames')
    assert result23['result'] == 'resolved'
    assert result23['correction_history'][0] == first23
    assert {k:v for k,v in result23['correction_history'][1].items() if k!='correction_history'} == corrected23
    json.dumps(result23, ensure_ascii=False)

    phone_in_frame = {'observations':['手机连在夹具和杆上'], 'checks':[
        {'criterion':'自己的拍摄手机和屏幕保持画外','scope':'visual','met':False,'evidence':'手机、夹具和杆同时入画'}]}
    monkeypatch.setattr(confirmed, 'ask_json', lambda *a, **k: phone_in_frame)
    result20 = confirmed.judge_finding({}, tmp_path/'v.mp4', {'instruction':target20}, tmp_path/'frames')
    assert result20['result'] == 'unresolved'


def test_scope_correction_failure_is_uncertain_and_cached_scope_error_recovers_once(tmp_path, monkeypatch):
    d, clips, rows = fixture_episode(tmp_path)
    claim = {'video':'old.mp4','take':[0,0,0],'issue':'old empty rod','instruction':'当前目标'}
    previous = json.loads((d/'episode_review.json').read_text())
    previous['clips']['a']['confirmed'] = {**claim, 'check': {
        'video':rows['a']['video'],'take':rows['a']['take'],'result':'uncertain',
        'checks':[{'criterion':'当前目标','scope':'visual','met':True,'evidence':'符合'},
                  {'criterion':'头灯仍在背包且手机干燥','scope':'visual','met':None,'evidence':'未入画'}],
        'evidence':'未入画'}}
    previous['clips']['b']['confirmed'] = {
        'video':'older.mp4','take':[0,0,0],'issue':'other','instruction':'other target',
        'check':{'video':rows['b']['video'],'take':rows['b']['take'],'result':'resolved'}}
    fresh = copy.deepcopy(previous)
    for cid in ('a','b'):
        fresh['clips'][cid].update(video=rows[cid]['video'],take=rows[cid]['take'],severity='pass',tier='ignore')
    calls=[]
    real_judge = confirmed.judge_finding
    monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **k:
        calls.append('a') or {'result':'resolved','observations':['目标通过'],'evidence':'当前帧符合'})
    recovered = confirmed.enforce(d, fresh, previous, verify=True)
    assert calls == ['a']
    c = recovered['clips']['a']['confirmed']['check']
    assert c['result']=='resolved' and c['scope_recovery_attempted'] is True
    assert c['check_history'][0]['result']=='uncertain'
    assert c['check_history'][0]['checks'][1]['criterion']=='头灯仍在背包且手机干燥'
    assert recovered['clips']['b']['confirmed']['check']['result']=='resolved'
    calls.clear()
    again=confirmed.enforce(d, recovered, recovered, verify=True)
    assert calls == [] and again['clips']['a']['confirmed']['check']['check_history']==c['check_history']

    errored = copy.deepcopy(previous)
    errored['clips']['a']['confirmed']['check']['scope_recovery_attempted'] = False
    errors=[]
    monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **k:
        errors.append(1) or (_ for _ in ()).throw(RuntimeError('offline')))
    failed_recovery = confirmed.enforce(d, fresh, errored, verify=True)
    confirmed.enforce(d, failed_recovery, failed_recovery, verify=True)
    assert len(errors)==1
    assert failed_recovery['clips']['a']['confirmed']['check']['scope_recovery_attempted'] is True

    bad={'observations':['仍越界'], 'checks':[{'criterion':'头灯干燥','scope':'visual','met':None,'evidence':'未知'}]}
    monkeypatch.setattr(confirmed, 'judge_finding', real_judge)
    monkeypatch.setattr(confirmed, 'clip_frames', lambda *a:[tmp_path/'f.jpg'])
    monkeypatch.setattr(confirmed, 'image_part', lambda *a:{'type':'image'})
    monkeypatch.setattr(confirmed, 'ask_json', lambda *a, **k:bad)
    uncertain=confirmed.judge_finding({},tmp_path/'v.mp4',{'instruction':'目标'},tmp_path/'frames')
    assert uncertain['result']=='uncertain' and uncertain['error_kind']=='review_scope_violation'
    assert len(uncertain['correction_history']) == 2


def test_unreviewed_new_take_labels_the_confirmed_defect_as_old_take_evidence():
    row=confirmed.apply_claim({'video':'new.mp4','take':[4,5,6]},
        {'video':'old.mp4','take':[1,2,3],'issue':'旧片出现完整空杆','instruction':'当前空杆目标'},
        'new.mp4',[4,5,6])
    assert row['severity']=='fail'
    assert '旧take确认的问题' in row['defect_issue']
    assert row['verify']['verdict']=='uncertain'
    assert '当前take尚未通过目标复核' in row['verify']['evidence']


def test_current_take_unresolved_check_reports_its_own_failure_evidence():
    row=confirmed.apply_claim({'video':'new.mp4','take':[4,5,6]},
        {'video':'old.mp4','take':[1,2,3],'issue':'旧片出现完整空杆','instruction':'拍摄手机保持画外',
         'check':{'video':'new.mp4','take':[4,5,6],'result':'unresolved',
                  'evidence':'当前帧手机连夹具和杆同时入画'}},
        'new.mp4',[4,5,6])
    assert row['severity']=='fail' and row['verify']['verdict']=='obvious'
    assert row['defect_issue']=='当前帧手机连夹具和杆同时入画'
    assert row['verify']['evidence']==row['defect_issue']


def test_imported_current_take_finding_keeps_observed_issue_as_defect_text(tmp_path):
    d, clips, rows = fixture_episode(tmp_path)
    issue='当前take画面可见完整空杆'
    instruction='只保留目标要求的手机画外状态'
    imported=import_confirmed_findings(d, {'a':{
        'video':rows['a']['video'],'take':rows['a']['take'],
        'issue':issue,'instruction':instruction}})
    row=imported['clips']['a']
    assert row['confirmed']['check']['result']=='unresolved'
    assert 'evidence' not in row['confirmed']['check']
    assert row['defect_issue']==issue
    assert row['verify']['evidence']==issue


def test_host_review_criterion_is_a_valid_visual_target(tmp_path, monkeypatch):
    criterion='允许镜头切换，但人物和座位关系必须连续。'
    monkeypatch.setattr(confirmed, 'clip_frames', lambda *a:[tmp_path/'f.jpg'])
    monkeypatch.setattr(confirmed, 'image_part', lambda *a:{'type':'image'})
    monkeypatch.setattr(confirmed, 'ask_json', lambda *a, **k:{
        'observations':['座位关系连续'], 'checks':[{'criterion':criterion,'scope':'visual','met':True,'evidence':'连续'}]})
    result=confirmed.judge_finding({},tmp_path/'v.mp4',{'instruction':'按当前镜头构图验收'},
                                   tmp_path/'frames',review_criteria=[criterion])
    assert result['result']=='resolved'


def test_scope_quote_allows_only_terminal_punctuation_differences():
    target='手机自身可见，必须保持画外；不得出现1.25米杆体，X-Y状态不变。'
    assert not confirmed._out_of_target_visual_checks(
        {'checks':[{'criterion':'手机自身可见。','scope':'visual'}]},target)
    for criterion in ('不得出现1.26米杆体', '必须出现1.25米杆体', '不得出现1.25米杆体，X—Y状态不变'):
        assert confirmed._out_of_target_visual_checks(
            {'checks':[{'criterion':criterion,'scope':'visual'}]},target)


def test_cached_valid_correction_reparses_without_judge_call(tmp_path, monkeypatch):
    d, clips, rows=fixture_episode(tmp_path)
    instruction=('保持G15-02拆机前设备状态：后置画面不再出现从握柄到可见空端的完整独立已拆空杆。'
        '拍摄手机仍在镜头后方画外；自身手脚或部分杆柄可自然出现，连接处不入画不判缺失，'
        '不要求手机自身可见，也不将此条件套到G16-02。')
    correction={'observations':['手机和空杆均未入画'], 'premise_valid':True,
        'premise_evidence':'当前帧符合当前镜头前提', 'checks':[
            {'criterion':'后置画面不再出现从握柄到可见空端的完整独立已拆空杆。',
             'scope':'visual','met':True,'evidence':'未见完整空杆'},
            {'criterion':'拍摄手机仍在镜头后方画外；自身手脚或部分杆柄可自然出现，连接处不入画不判缺失，不要求手机自身可见。',
             'scope':'visual','met':True,'evidence':'手机未入画'},
            {'criterion':'不将此条件套到G16-02。','scope':'visual','met':True,'evidence':'本镜为G15-02'}]}
    stale={'video':'old.mp4','take':[0,0,0],'issue':'旧take出现完整空杆',
        'instruction':instruction,'check':{
            'video':rows['a']['video'],'take':rows['a']['take'],'result':'uncertain',
            'error_kind':'review_scope_violation','scope_recovery_attempted':True,
            'observations':['已完成旧协议纠正'], 'checks':[], 'correction':correction,
            'correction_history':[{'observations':['首次答复'],'checks':[]},correction]}}
    previous=json.loads((d/'episode_review.json').read_text())
    previous['clips']['a']['confirmed']=stale
    fresh=copy.deepcopy(previous)
    fresh['clips']['a'].update(video=rows['a']['video'],take=rows['a']['take'],severity='pass',tier='ignore')
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a,**k:pytest.fail('cached correction must not call judge'))
    result=confirmed.enforce(d,fresh,previous,verify=True)
    check=result['clips']['a']['confirmed']['check']
    assert check['result']=='resolved' and check['reparsed'] is True
    assert check['reparsed_from']=='review_scope_violation'
    assert check['scope_recovery_attempted'] is True
    assert check['check_history'][-1]['error_kind']=='review_scope_violation'
    assert result['clips']['a']['severity']=='pass'


@pytest.mark.parametrize(('met','expected'),[(False,'unresolved'),(None,'uncertain')])
def test_cached_reparse_keeps_visual_false_or_unknown_blocking(tmp_path, monkeypatch, met, expected):
    d, clips, rows=fixture_episode(tmp_path)
    instruction='画面不得出现完整空杆。'
    correction={'observations':['当前帧已检查'], 'premise_valid':True,'premise_evidence':'前提成立',
        'checks':[{'criterion':'画面不得出现完整空杆。','scope':'visual','met':met,'evidence':'当前证据'}]}
    previous=json.loads((d/'episode_review.json').read_text())
    previous['clips']['a']['confirmed']={'video':'old.mp4','take':[0,0,0],
        'issue':'旧take缺陷','instruction':instruction,'check':{
            'video':rows['a']['video'],'take':rows['a']['take'],'result':'uncertain',
            'error_kind':'review_scope_violation','scope_recovery_attempted':True,
            'checks':[], 'observations':[], 'correction':correction}}
    fresh=copy.deepcopy(previous)
    fresh['clips']['a'].update(video=rows['a']['video'],take=rows['a']['take'],severity='pass',tier='ignore')
    monkeypatch.setattr(confirmed,'judge_finding',lambda *a,**k:pytest.fail('reparse must remain deterministic'))
    result=confirmed.enforce(d,fresh,previous,verify=True)
    check=result['clips']['a']['confirmed']['check']
    assert check['result']==expected and check['reparsed'] is True
    assert result['clips']['a']['severity']=='fail'
