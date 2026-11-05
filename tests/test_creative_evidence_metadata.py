"""Host-owned field metadata never substitutes for independent content confirmation."""
import copy
import json
from pathlib import Path

import pytest

from novel_manga.application.preparation import audit
from novel_manga.planning.preparation import ground_creative_finding


def episode():
    return {'title':'Global title before shots', 'chapter_script':{'shots':[
        {'index':10, 'start_state':'The tool box is closed.', 'event':'He opens the tool box.',
         'end_state':'She returns with the record.', 'camera':'Begin outside; follow him inside.',
         'opening_frame':{'image_prompt':'Two people wait beside the closed box.'},
         'storyboard':{'frame':'The box is closed.', 'action_beats':['He opens the lid.']}},
        {'index':20, 'start_state':'She is inside.', 'end_state':'She leaves.'}],
        'music_plan':{'cues':[{'reason':'Keep the final moment silent.'}]}}}


def finding(basis, **changes):
    return {'evidence_origin':'authored_field','kind':'action','stage':999,
        'source_segment':'','source_quote':'','basis':basis,'observed':'','expected':'',
        'observed_time':'during','expected_time':'t=0','relation':'contradiction',
        'problem':'The ending is claimed to happen at time zero.',
        'correction':'Check the complete action before changing either field.',**changes}


def payload(parts):
    text=parts[0]['text']
    return json.JSONDecoder().raw_decode(text[text.index('{"episode":'):])[0]


def paths_from_payload(value):
    paths=value['evidence_paths']
    return list(paths.values()) if isinstance(paths,dict) else paths


def test_metadata_is_derived_before_independent_withdrawal_and_raw_is_immutable(tmp_path,monkeypatch):
    original=episode();before=copy.deepcopy(original);calls=[];raw=None
    chosen=['chapter_script.shots[0].opening_frame.image_prompt','chapter_script.shots[0].end_state']
    def ask(parts,schema,**options):
        nonlocal raw
        calls.append(options['name']);data=payload(parts)
        if options['name']=='pre_render_story_audit':
            assert isinstance(data['evidence_paths'],dict)
            paths=paths_from_payload(data)
            assert all(data['evidence_paths'][str(i)]==path for i,path in enumerate(paths))
            raw={'source_readable':True,'source_problem':'','issues':[finding([paths.index(p)for p in chosen])]}
            return copy.deepcopy(raw)
        assert options['name']=='pre_render_audit_confirmation'
        issue=data['issues'][0]
        assert issue['stage']==10
        assert (issue['observed_time'],issue['expected_time'])==('t=0','t=end')
        assert issue['basis']==chosen
        assert issue['expected']==original['chapter_script']['shots'][0]['end_state']
        for key in ('problem','correction','relation'):
            assert issue[key]==raw['issues'][0][key]
        return {'decisions':[{'issue_index':0,'decision':'withdraw','reason':'End state is not an opening requirement.'}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=audit.creative_audit(original,{'package.md':'原包中完整的真实文本。'},cache_path=tmp_path/'audit.json')
    assert calls==['pre_render_story_audit','pre_render_audit_confirmation']
    assert result['answer']['issues']==[]
    assert result['raw_response_history'][0]['response']==raw
    assert original==before
    assert audit.creative_audit(original,{'package.md':'原包中完整的真实文本。'},cache_path=tmp_path/'audit.json')==result
    assert len(calls)==2


@pytest.mark.parametrize('suffix,time', [('start_state','t=0'),('opening_frame.image_prompt','t=0'),
    ('storyboard.frame','t=0'),('event','during'),('storyboard.action_beats[0]','during'),('end_state','t=end')])
def test_known_times_come_from_actual_fields_without_rewriting_the_finding(suffix,time):
    data=episode();paths=audit._creative_evidence_paths(data);path='chapter_script.shots[0].'+suffix
    issue=finding([paths.index(path)]*2,observed_time=None,expected_time=None)
    audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[issue]},data,
                                 {'source':'真实原始内容文本'},evidence_paths=paths)
    assert issue['stage']==10 and issue['observed_time']==issue['expected_time']==time
    assert issue['basis']==[path,path]
    ground_creative_finding(copy.deepcopy(issue),data)


def test_unknown_camera_time_remains_model_selected_and_required():
    data=episode();paths=audit._creative_evidence_paths(data)
    pair=[paths.index('chapter_script.shots[0].camera'),paths.index('chapter_script.music_plan.cues[0].reason')]
    issue=finding(pair,observed_time='during',expected_time='whole_shot')
    audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[issue]},data,
                                 {'source':'真实原始内容文本'},evidence_paths=paths)
    assert (issue['observed_time'],issue['expected_time'])==('during','whole_shot')
    with pytest.raises(ValueError,match='evidence time'):
        audit._ground_creative_answer({'source_readable':True,'source_problem':'',
            'issues':[finding(pair,observed_time=None)]},data,{'source':'真实原始内容文本'},evidence_paths=paths)


def test_schema_anchors_first_evidence_to_shot_and_keeps_all_global_leaves(tmp_path,monkeypatch):
    data=episode();paths=audit._creative_evidence_paths(data)
    assert paths[0].startswith('chapter_script.shots[')
    assert 'title' in paths and 'chapter_script.music_plan.cues[0].reason' in paths
    shot_count=sum(p.startswith('chapter_script.shots[')for p in paths)
    assert all(p.startswith('chapter_script.shots[')for p in paths[:shot_count])
    seen=[]
    def ask(parts,schema,**kwargs):
        seen.append(schema)
        return {'source_readable':True,'source_problem':'','issues':[]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    audit.creative_audit(data,{'a.md':'原文甲完整段落','b.md':'原文乙完整段落'},cache_path=tmp_path/'audit.json')
    branches=seen[0]['properties']['issues']['items']['anyOf']
    authored=next(b for b in branches if b['properties']['evidence_origin']['enum']==['authored_field'])
    basis=authored['properties']['basis']
    assert basis['minItems']==basis['maxItems']==len(basis['prefixItems'])==2
    assert basis['prefixItems'][0]=={'type':'integer','minimum':0,'maximum':shot_count-1}
    assert basis['prefixItems'][1]==basis['items']=={'type':'integer','minimum':0,'maximum':len(paths)-1}
    assert authored['properties']['stage']=={'type':'null'}
    for branch in branches:
        if branch['properties']['evidence_origin']['enum']!=['original_text']:continue
        assert branch['properties']['source_segment']['enum']==['a.md','b.md']
        stage=branch['properties']['stage'];stages=next(v['enum']for v in stage.get('anyOf',[stage])if 'enum'in v)
        assert set(stages)==({0,10,20}if branch['properties']['kind']['enum']==['missing_event']else{10,20})


def test_global_first_or_fabricated_field_is_not_reanchored_to_a_shot():
    data=episode();paths=audit._creative_evidence_paths(data)
    bad=finding([paths.index('title'),paths.index('chapter_script.shots[0].camera')])
    with pytest.raises(ValueError,match='first.*shot'):
        audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[bad]},data,
                                     {'source':'真实原始内容文本'},evidence_paths=paths)
    with pytest.raises(ValueError,match='path'):
        audit._ground_creative_answer({'source_readable':True,'source_problem':'',
            'issues':[finding(['chapter_script.shots[90].camera','title'])]},data,
            {'source':'真实原始内容文本'},evidence_paths=paths)


@pytest.mark.parametrize('kind,stage,ok',[('missing_event',0,True),('action',0,False),('action',10,True),('action',99,False)])
def test_original_source_without_basis_keeps_stage_and_literal_grounding(kind,stage,ok):
    issue=finding(None,evidence_origin='original_text',source_segment='source.md',kind=kind,stage=stage,
                  observed=None,expected=None,observed_time=None,expected_time=None,relation=None)
    answer={'source_readable':True,'source_problem':'','issues':[issue]};data=episode()
    if not ok:
        with pytest.raises(ValueError,match='unknown stage'):
            audit._ground_creative_answer(answer,data,{'source.md':'真实原始来源完整内容。'},evidence_paths=audit._creative_evidence_paths(data))
    else:
        audit._ground_creative_answer(answer,data,{'source.md':'真实原始来源完整内容。'},evidence_paths=audit._creative_evidence_paths(data))
        assert issue['stage']==stage and issue['source_quote']=='真实原始来源完整内容。'


def test_final_fresh_ground_failure_is_retryable_and_preserves_both_raw_replies(tmp_path,monkeypatch):
    from novel_manga.llm.client import ModelOutputMalformed
    from novel_manga.service_errors import service_failure
    replies=[];calls=[];cache=tmp_path/'audit.json';data=episode()
    def ask(parts,schema,**options):
        calls.append(options['name'])
        paths=paths_from_payload(payload(parts))
        response={'source_readable':True,'source_problem':'','issues':[finding([len(paths)+3,0])]}
        if options['name']=='pre_render_audit_evidence':response['issues'][0]['issue_index']=0
        replies.append(copy.deepcopy(response))
        return response
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    with pytest.raises(ValueError) as stopped:
        audit.creative_audit(data,{'package':'真实原始完整文本。'},cache_path=cache)
    assert isinstance(stopped.value,ModelOutputMalformed)
    assert service_failure(stopped.value)['retryable'] is True
    assert calls==['pre_render_story_audit','pre_render_audit_evidence']
    artifact=Path(stopped.value.response_path)
    assert artifact.parent==cache.parent and artifact!=cache
    saved=json.loads(artifact.read_text())
    assert [row['response']for row in saved['raw_response_history']]==replies
    assert 'index is out of range' in saved['error']
    assert saved['confirmation'] is None and not cache.exists()
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:{
        'source_readable':True,'source_problem':'','issues':[]})
    assert audit.creative_audit(data,{'package':'真实原始完整文本。'},cache_path=cache)['answer']['issues']==[]
    assert artifact.is_file()  # A diagnostic is retained, never treated as an accepted cache.


def test_repeated_fresh_failures_keep_each_diagnostic_pointer_immutable(tmp_path,monkeypatch):
    from novel_manga.llm.client import ModelOutputMalformed
    cache=tmp_path/'audit.json';artifacts=[];calls=[]
    def ask(parts,schema,**options):
        calls.append(options['name'])
        paths=paths_from_payload(payload(parts))
        issue=finding([len(paths)+len(calls),0])
        if options['name']=='pre_render_audit_evidence':issue['issue_index']=0
        return {'source_readable':True,'source_problem':'','issues':[issue]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    for _ in range(2):
        with pytest.raises(ModelOutputMalformed) as stopped:
            audit.creative_audit(episode(),{'source':'真实原始完整文本。'},cache_path=cache)
        artifact=Path(stopped.value.response_path)
        assert artifact.parent==cache.parent
        artifacts.append((artifact,artifact.read_bytes()))
    assert len(calls)==4 and not cache.exists()
    assert artifacts[0][0]!=artifacts[1][0]
    assert artifacts[0][1]!=artifacts[1][1]
    assert all(path.read_bytes()==before for path,before in artifacts)


def test_bad_fresh_confirmation_is_typed_and_keeps_original_confirmation(tmp_path,monkeypatch):
    from novel_manga.llm.client import ModelOutputMalformed
    responses=[];confirmation={'decisions':[]}
    def ask(parts,schema,**options):
        if options['name']=='pre_render_audit_confirmation':return copy.deepcopy(confirmation)
        paths=paths_from_payload(payload(parts))
        basis=[paths.index('chapter_script.shots[0].start_state'),paths.index('chapter_script.shots[0].end_state')]
        response={'source_readable':True,'source_problem':'','issues':[finding(basis)]}
        responses.append(copy.deepcopy(response));return response
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    with pytest.raises(ValueError) as stopped:
        audit.creative_audit(episode(),{'source':'真实原始完整文本。'},cache_path=tmp_path/'audit.json')
    assert isinstance(stopped.value,ModelOutputMalformed)
    saved=json.loads(Path(stopped.value.response_path).read_text())
    assert saved['confirmation']==confirmation
    assert saved['raw_response_history'][0]['response']==responses[0]
    assert 'confirmation must cover every issue exactly once' in saved['error']
    assert not (tmp_path/'audit.json').exists()


def test_real_content_issue_remains_normal_author_feedback(tmp_path,monkeypatch):
    data=episode()
    data['chapter_script']['shots'][0]['opening_frame']['image_prompt']='The tool box is already open.'
    def ask(parts,schema,**options):
        if options['name']=='pre_render_audit_confirmation':
            return {'decisions':[{'issue_index':0,'decision':'keep','reason':'The two authored opening requirements conflict.'}]}
        paths=paths_from_payload(payload(parts))
        return {'source_readable':True,'source_problem':'','issues':[finding([
            paths.index('chapter_script.shots[0].start_state'),paths.index('chapter_script.shots[0].opening_frame.image_prompt')],
            problem='The opening tool box is both open and closed.')]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=audit.creative_audit(data,{'source':'真实原始完整文本。'},cache_path=tmp_path/'audit.json')
    assert len(result['answer']['issues'])==1
    assert result['answer']['issues'][0]['problem']=='The opening tool box is both open and closed.'
    assert sorted(p.name for p in tmp_path.iterdir())==['audit.json']


def test_input_and_cached_protocol_errors_are_not_fresh_model_retries(tmp_path,monkeypatch):
    from novel_manga.llm.client import ModelOutputMalformed
    from novel_manga.service_errors import service_failure
    monkeypatch.setattr('novel_manga.llm.client.ask_json',lambda *a,**k:pytest.fail('No model call for invalid input/cache'))
    with pytest.raises(ValueError) as bad_input:
        audit.creative_audit(episode(),{'../bad':'真实原始完整文本。'},cache_path=tmp_path/'input.json')
    assert not isinstance(bad_input.value,ModelOutputMalformed)
    assert service_failure(bad_input.value)['retryable'] is False
    cache=tmp_path/'audit.json';sources={'source':'真实原始完整文本。'}
    saved={'fingerprint':audit.creative_fingerprint(episode(),sources),'raw':{},'confirmation':None,'answer':{}}
    cache.write_text(json.dumps(saved));before=cache.read_bytes()
    with pytest.raises(ValueError,match='invalid cached creative audit') as bad_cache:
        audit.creative_audit(episode(),sources,cache_path=cache)
    assert not isinstance(bad_cache.value,ModelOutputMalformed)
    assert service_failure(bad_cache.value)['retryable'] is False
    assert cache.read_bytes()==before and sorted(p.name for p in tmp_path.iterdir())==['audit.json']


@pytest.mark.parametrize('error',[AssertionError('unknown compiler invariant'),ValueError('invalid caller schema')])
def test_model_call_exceptions_are_not_wrapped_by_grounding_retry(tmp_path,monkeypatch,error):
    def ask(*args,**kwargs):raise error
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    with pytest.raises(type(error)) as stopped:
        audit.creative_audit(episode(),{'source':'真实原始完整文本。'},cache_path=tmp_path/'audit.json')
    assert stopped.value is error and not list(tmp_path.iterdir())
