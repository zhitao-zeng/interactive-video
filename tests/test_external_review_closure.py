"""Deterministic regressions for the external unattended-production review."""
import copy
import json

import pytest
from PIL import Image

from novel_manga.application.agents.episode import first_frames, opening_source
from novel_manga.application.agents.episode.input_contracts import opening_grant
from novel_manga.application.agents.episode.input_repair import current_repair_grant
from novel_manga.application.preparation import audit
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


@pytest.mark.parametrize('correction',['valid','missing','duplicate','content'])
def test_evidence_correction_cannot_drop_an_already_grounded_issue(tmp_path, monkeypatch,correction):
    episode={'chapter_script':{'shots':[{'index':1,'start_state':'Door closed.',
        'opening_frame':{'image_prompt':'Door open.'},'camera':'Wide.'}]}}
    calls=[]
    def issue(basis, problem):
        return {'evidence_origin':'authored_field','kind':'action','stage':None,
            'source_segment':'','source_quote':'','basis':basis,'observed':'','expected':'',
            'observed_time':'t=0','expected_time':'t=0','relation':'contradiction',
            'problem':problem,'correction':'Unify the actual source fields.'}
    def ask(parts,schema,**kw):
        calls.append(kw['name'])
        if kw['name']=='pre_render_story_audit':
            data=json.loads(parts[0]['text'].rsplit('\n',1)[1]);paths=list(data['evidence_paths'].values())
            a=paths.index('chapter_script.shots[0].start_state')
            b=paths.index('chapter_script.shots[0].opening_frame.image_prompt')
            return {'source_readable':True,'source_problem':'','issues':[
                issue([a,b],'A true contradiction'),issue([999,b],'B bad citation')]}
        if kw['name']=='pre_render_audit_evidence':
            paths=audit._creative_evidence_paths(episode)
            fixed=issue([paths.index('chapter_script.shots[0].camera')]*2,'B bad citation')
            repaired={**fixed,'issue_index':1}
            if correction=='missing':return {'issues':[]}
            if correction=='duplicate':return {'issues':[repaired,repaired]}
            if correction=='content':repaired['problem']='Replaced with a different problem'
            return {'issues':[repaired]}
        assert kw['name']=='pre_render_audit_confirmation'
        data=json.loads(parts[0]['text'].rsplit('\n',1)[1])
        assert [i['problem'] for i in data['issues']]==['A true contradiction','B bad citation']
        return {'decisions':[{'issue_index':0,'decision':'keep','reason':'The two starts conflict.'},
            {'issue_index':1,'decision':'withdraw','reason':'The same camera field is compatible.'}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    if correction!='valid':
        from novel_manga.llm.client import ModelOutputMalformed
        with pytest.raises(ModelOutputMalformed):
            audit.creative_audit(episode,{'source':'Original text.'},cache_path=tmp_path/'audit.json')
        assert not (tmp_path/'audit.json').exists()
        return
    result=audit.creative_audit(episode,{'source':'Original text.'},cache_path=tmp_path/'audit.json')
    assert [i['problem'] for i in result['answer']['issues']]==['A true contradiction']
    assert calls==['pre_render_story_audit','pre_render_audit_evidence','pre_render_audit_confirmation']


def test_accepted_retake_does_not_restore_its_parents_old_grant(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01','extra_references':[]}]})
    checks=[]
    def check(*args):
        checks.append(1)
        return {'findings':[{'origin':'image','relation':'contradiction','observed':'Wrong pose.',
            'expected':'Requested pose.','basis':['opening.camera'],'reason':'wrong pose'}]} if len(checks)==1 else {'findings':[]}
    op=first_frames.FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),checker=check)
    old=op.prepare({});assert not old['ok']
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01','extra_references':[],
        'retake_candidate':{'candidate':'candidate_01','reason':'Correct the confirmed pose.'}}]})
    assert op.prepare({})['ok']
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',old)
    assert first_frames.status(ctx,plan)['clips']['clip_01']['current']
    assert current_repair_grant(ctx,{'clip_id':'clip_01'},plan) is None


def test_current_source_location_and_start_have_minimal_input_authority():
    findings=[{'origin':'source','relation':'contradiction','observed':'Inside a sealed basement.',
        'expected':'Outdoor pier.','basis':['opening.start_state','opening.location'],'reason':'different locations'}]
    grant=opening_grant({'clip_id':'clip_01','shot_indexes':[1]},findings)
    assert grant.operation=='input' and grant.shot_fields=={'start_state','location'}


@pytest.mark.parametrize('basis,observed',[
    (['opening.start_state','opening.nonexistent'],'A is standing.'),
    (['opening.start_state','opening.camera'],'Invented source quote.')])
def test_ordinary_source_conflicts_require_actual_fields_and_quotes(basis,observed):
    finding={'kind':'source_contradiction','origin':'source','relation':'contradiction',
        'observed':observed,'expected':'A sits.','basis':basis,'reason':'contradiction'}
    with pytest.raises(ValueError):
        opening_source._validated_review({'findings':[finding]},
            opening={'start_state':'A is standing.','camera':'A sits.'})


@pytest.mark.parametrize('frames',[[],None,[{'clip_id':'missing'}],
    [{'clip_id':'clip_01'},{'clip_id':'clip_01'}],'not-an-array'])
def test_outer_first_frame_arguments_have_structured_feedback(tmp_path,monkeypatch,frames):
    ctx,_=case(tmp_path,monkeypatch)
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':frames})
    op=first_frames.FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no paid allocation'))
    with pytest.raises(ValueError):op.prepare({})
    assert op.partial_outcome['argument_errors']
    assert op.partial_outcome['image_candidates_allocated']==0


def test_malformed_first_frame_json_has_structured_position(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    (ctx.work_dir/'output/first-frames.json').write_text('{"frames": [}')
    op=first_frames.FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no paid allocation'))
    with pytest.raises(ValueError):op.prepare({})
    assert op.partial_outcome['argument_errors']
    assert 'line' in json.dumps(op.partial_outcome['argument_errors']).lower()


@pytest.mark.parametrize('document',['{"frames": [}', '[]', '{"frames": []}',
    '{"render_ready":"yes","frames":[{"clip_id":"clip_01"}]}'])
def test_normal_tool_entry_preserves_outer_argument_feedback(tmp_path,monkeypatch,document):
    from types import SimpleNamespace
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,_=case(tmp_path,monkeypatch)
    (ctx.work_dir/'output/first-frames.json').write_text(document)
    result=EpisodeTools.first_frames(SimpleNamespace(ctx=ctx),{})
    assert result['ok'] is False and result['argument_errors'] and result['image_candidates_allocated']==0
    assert read_json(ctx.work_dir/'input/latest-first-frames.json')==result


def test_source_conflict_status_is_repeatable_and_keeps_field_scope(tmp_path,monkeypatch):
    ctx,clips=case(tmp_path,monkeypatch)
    shot=copy.deepcopy(clips[0]['shots'][0])
    shot.update(start_state='Inside a sealed basement.',location='Outdoor pier.')
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[shot]})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01'}]})
    def ask(*args,**kwargs):
        return {'reviews':[{'clip_id':'clip_01','findings':[{
            'kind':'source_contradiction','origin':'source','relation':'contradiction',
            'observed':'Inside a sealed basement.','expected':'Outdoor pier.',
            'basis':['opening.start_state','opening.location'],'reason':'The current locations conflict.'}]}]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    monkeypatch.setattr(first_frames,'review_opening_sources',opening_source.review_batch)
    result=first_frames.FirstFrameOperations(ctx,builder=lambda *a:pytest.fail('no image')).prepare({})
    assert not result['ok'] and result['error']=='opening_source_conflict'
    for _ in range(2):
        row=first_frames.status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))['clips']['clip_01']
        assert set(row['repair_grant']['shot_fields'])=={'start_state','location'}


@pytest.mark.parametrize('explicit',[False,True])
def test_unchanged_reference_mtime_preserves_passed_retake_chain(tmp_path,monkeypatch,explicit):
    import os
    from novel_manga.application.rendering import h3
    from novel_manga.application.profiles import h3_stamp
    ctx,clips=case(tmp_path,monkeypatch,host_h3=True)
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    def submit(retake=False):
        row={'clip_id':'clip_01','extra_references':[]}
        if retake:row['retake_candidate']={'candidate':'candidate_01','reason':'Correct the wrong pose.'}
        atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[row]})
    calls=[]
    def build(c,r,p):calls.append('image');Image.new('RGB',(8,16)).save(p)
    def check(*args):
        calls.append('review')
        return {'findings':[{'origin':'image','relation':'contradiction','observed':'Wrong pose.',
            'expected':'Right pose.','basis':['opening.camera'],'reason':'wrong pose'}]} if calls.count('review')==1 else {'findings':[]}
    def convert(clip,*,errors=None,**kw):
        calls.append('compile')
        if calls.count('compile')==1:errors.append(ConnectionError('interrupted'));return False
        clip.update(prompt_h3='English request',prompt_h3_of=h3_stamp(clip));return True
    monkeypatch.setattr(h3,'convert',convert)
    submit();op=first_frames.FirstFrameOperations(ctx,builder=build,checker=check)
    assert not op.prepare({})['ok']
    submit(True)
    with pytest.raises(ConnectionError):op.prepare({})
    for reference in clips[0]['references']:
        path=ctx.novel_dir/reference['path']
        if path.is_file():
            info=path.stat();os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+1_000_000_000))
    submit(explicit)
    result=op.prepare({})
    assert result['ok'] and result['clips']['clip_01']['first_frame']['candidate']=='candidate_02'
    assert calls.count('image')==2 and calls.count('review')==2


def test_h3_source_conflict_is_preserved_as_a_source_review_request(tmp_path,monkeypatch):
    from novel_manga.application.rendering import h3
    from novel_manga.application.agents.episode import readiness
    ctx,clips=case(tmp_path,monkeypatch,host_h3=True)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':copy.deepcopy(clips[0]['shots'])})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01'}]})
    conflicts=['The opening says the door is closed while camera says it is already open.']
    def convert(clip,*,problems=None,source_conflicts=None,**kw):
        problems.extend(conflicts)
        if source_conflicts is not None:source_conflicts.extend(conflicts)
        return False
    monkeypatch.setattr(h3,'convert',convert)
    monkeypatch.setattr('novel_manga.application.preparation.audit.audit',lambda *a,**k:{
        'source_readable':True,'issues':[{'stage':1,'problem':'The current source fields conflict.'}]})
    op=first_frames.FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
                                        checker=lambda *a:{'findings':[]})
    with pytest.raises(ValueError):op.prepare({})
    row=op.partial_outcome['clips']['clip_01']
    assert row['stage']=='h3_source_review' and row['source_conflicts']==conflicts
    assert row['image_review_passed'] is True and row['source_conflicts_confirmed'] is False
    assert not row.get('service_interrupted') and not row.get('repair_grant')
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',{'ok':False,**op.partial_outcome})
    status={'adopted':True,'job':None,'first_frames':first_frames.status(ctx,read_json(ctx.episode_dir/'clip_plan.json'))}
    route=readiness.next_action(ctx,status)
    assert route['action']=='agent' and route['request_input_conflicts']['clip_01']['source_conflicts']==conflicts
    assert route['source_conflicts_confirmed'] is False
    revised=read_json(ctx.episode_dir/'chapter_script.json');revised['shots'][0]['start_state']='Source revised by its author.'
    atomic_write_json(ctx.episode_dir/'chapter_script.json',revised)
    assert 'request_input_conflicts' not in readiness.next_action(ctx,status)


@pytest.mark.parametrize('source',['门旁立着一块红色警示牌。','门旁立着一块红色警示牌，警示牌上没有文字。'])
def test_explicit_colour_confirmation_accepts_paraphrased_initial_requirement(source):
    payload={'author_text_fields':{'opening.start_state':source}}
    finding={'origin':'image','relation':'contradiction','observed':'警示牌是蓝色的。','expected':'警示牌应为红色。',
        'author_field_path':'opening.start_state','author_subject':'警示牌'}
    check={'id':0,'result':'confirmed','evidence_class':'authored_explicit_colour',
        'author_field_path':'opening.start_state','author_quote':'红色警示牌','author_subject':'警示牌'}
    decision,selected=first_frames._effective_finding(payload,finding,check)
    assert decision['result']=='confirmed' and selected['expected']=='红色警示牌'


def test_current_timing_evidence_is_grounded_and_mapped_to_its_real_source_field():
    finding={'kind':'source_contradiction','origin':'source','relation':'contradiction','observed':'','expected':'',
             'basis':['opening.start_state','current_time_context.scene_time'],'reason':'Different times.'}
    review=opening_source._validated_review({'findings':[finding]},opening={'start_state':'Night on the pier.'},
                                           current_time_context={'scene_time':'day'})
    assert review['findings'][0]['expected']=='day'
    grant=opening_grant({'clip_id':'clip_01','shot_indexes':[1]},review['findings'])
    assert grant.shot_fields=={'start_state','scene_time'}


def test_noncolour_property_quote_does_not_require_an_invented_subject_word():
    payload={'opening':{'opening':{'shot_scale':'medium'}},'reference_images':[],
             'author_text_fields':{'opening.shot_scale':'medium'}}
    finding={'origin':'image','relation':'contradiction','observed':'Full body framing.','expected':'Medium shot.',
             'basis':['opening.shot_scale']}
    check={'id':0,'result':'confirmed','evidence_class':'non_colour','author_field_path':'opening.shot_scale',
           'author_subject':'camera','author_quote':'medium','non_colour_observation':'Full body framing.',
           'non_colour_expected':'medium','non_colour_basis':['opening.shot_scale'],
           'reference_image_number':0,'reference_use':'none'}
    decision,selected=first_frames._effective_finding(payload,finding,check)
    assert decision['result']=='confirmed' and selected['expected']=='medium'


@pytest.mark.parametrize('disposition',['source_issue','false_fixed','false_repeated'])
def test_h3_claim_has_one_independent_review_and_at_most_one_evidenced_revision(tmp_path,monkeypatch,disposition):
    from novel_manga.application.rendering import h3
    from novel_manga.application.profiles import h3_stamp
    from novel_manga.application.agents.episode import readiness
    ctx,clips=case(tmp_path,monkeypatch,host_h3=True)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':copy.deepcopy(clips[0]['shots'])})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01'}]})
    compiled=[];audits=[]
    source_answer={'source_readable':True,'issues':[{'stage':1,'problem':'actual source issue'}]
                   if disposition=='source_issue' else []}
    def audit(directory,*,concerns=None):audits.append(concerns);return copy.deepcopy(source_answer)
    def convert(clip,*,source_conflicts=None,source_review=None,**kw):
        compiled.append(copy.deepcopy(source_review))
        if len(compiled)==1 or disposition=='false_repeated':
            source_conflicts.append('compiler source claim');return False
        assert source_review['answer']==source_answer and source_review['conflicts']==['compiler source claim']
        clip.update(prompt_h3='English request',prompt_h3_of=h3_stamp(clip));return True
    monkeypatch.setattr('novel_manga.application.preparation.audit.audit',audit)
    monkeypatch.setattr(h3,'convert',convert)
    op=first_frames.FirstFrameOperations(ctx,builder=lambda c,r,p:Image.new('RGB',(8,16)).save(p),
                                        checker=lambda *a:{'findings':[]})
    if disposition=='false_fixed':
        result=op.prepare({});assert result['ok']
        assert result['clips']['clip_01']['h3_source_review']['compiler_revision_used'] is True
    else:
        with pytest.raises(ValueError):op.prepare({})
        row=op.partial_outcome['clips']['clip_01']
        assert row['source_review']==source_answer
        atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',op.partial_outcome)
        status={'adopted':True,'job':None}
        route=readiness.next_action(ctx,status)
        if disposition=='false_repeated':
            assert route['error']=='compiler_source_disagreement' and readiness.automatic_request(ctx,status) is None
        else:assert route['action']=='agent'
    assert len(audits)==1 and len(compiled)==(1 if disposition=='source_issue' else 2)


@pytest.mark.parametrize('interrupted_stage',['audit','correction'])
def test_h3_source_review_service_recovery_does_not_bypass_pending_review(tmp_path,monkeypatch,interrupted_stage):
    from novel_manga.application.rendering import h3
    from novel_manga.application.profiles import h3_stamp
    ctx,clips=case(tmp_path,monkeypatch,host_h3=True)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':copy.deepcopy(clips[0]['shots'])})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'frames':[{'clip_id':'clip_01'}]})
    calls=[];audits=[];translations=[];images=[]
    def audit(*a,**k):
        calls.append('audit');audits.append(1)
        if interrupted_stage=='audit' and len(audits)==1:raise TimeoutError('review interrupted')
        return {'source_readable':True,'issues':[]}
    def convert(clip,*,source_conflicts=None,source_review=None,errors=None,**k):
        calls.append('compile');translations.append(source_review)
        if len(translations)==1:source_conflicts.append('unverified compiler claim');return False
        assert source_review is not None,'a pending review cannot be bypassed by a second empty compiler reply'
        if interrupted_stage=='correction' and len(translations)==2:
            errors.append(ConnectionError('corrected compilation interrupted'));return False
        clip.update(prompt_h3='English request',prompt_h3_of=h3_stamp(clip));return True
    def build(c,r,p):images.append(1);Image.new('RGB',(8,16)).save(p)
    monkeypatch.setattr('novel_manga.application.preparation.audit.audit',audit)
    monkeypatch.setattr(h3,'convert',convert)
    op=first_frames.FirstFrameOperations(ctx,builder=build,checker=lambda *a:{'findings':[]})
    with pytest.raises((TimeoutError,ConnectionError)):op.prepare({})
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',op.partial_outcome)
    assert op.prepare({})['ok'] and images==[1]
    assert calls==(['compile','audit','audit','compile'] if interrupted_stage=='audit'
                  else ['compile','audit','compile','compile'])


def test_h3_compiler_revision_receives_actual_source_review_context(monkeypatch):
    from novel_manga.application.rendering import h3
    seen=[]
    proof={'conflicts':['prior compiler claim'],'answer':{'source_readable':True,'issues':[]}}
    def ask(parts,schema,**kw):
        seen.append(parts[0]['text']);return {'shots':['The water is calm.'],'soundscape':'None.','conflicts':[]}
    monkeypatch.setattr(h3,'ask_json',ask)
    clip={'clip_id':'clip_01','request_seconds':4,'references':[],
          'prompt':'【阶段1】水面轻晃。结束时：水面平静。画面呈现'}
    assert h3.convert(clip,tries=1,source_review=proof)
    assert 'source_conflict_review' in seen[0] and 'prior compiler claim' in seen[0]


def test_existing_source_audit_also_preserves_valid_issues_during_citation_correction(tmp_path,monkeypatch):
    directory=tmp_path/'book/book_1';directory.mkdir(parents=True)
    atomic_write_json(directory/'chapter_script.json',{'shots':[{'index':1,'start_state':'A waits.'}]})
    segments=[{'segment_id':'seg_1','text':'A says hello. B waits.'}]
    atomic_write_json(directory/'segments.json',segments)
    atomic_write_json(directory.parent/'story_bible.json',{'characters':[]})
    monkeypatch.setattr(audit.PlannerContext,'from_env',lambda:None)
    monkeypatch.setattr('novel_manga.application.planning.context.load_entity_index',lambda *a,**k:None)
    monkeypatch.setattr('novel_manga.planning.cast.mentioned_characters',lambda *a,**k:[])
    monkeypatch.setattr('novel_manga.application.identity.flow.resolve_chapter',lambda *a,**k:{})
    monkeypatch.setattr('novel_manga.application.identity.context.prompt_context',lambda *a,**k:{})
    monkeypatch.setattr('novel_manga.application.identity.context.reading_segments',lambda *a,**k:segments)
    a={'kind':'action','stage':1,'source_segment':'seg_1','source_quote':'','problem':'Keep A.',
       'correction':'Correct source-backed action.'}
    b={**a,'source_segment':'bad_segment','problem':'Check B.'}
    def ask(parts,schema,**kw):
        if kw['name']=='pre_render_story_audit':return {'source_readable':True,'source_problem':'','issues':[a,b]}
        if kw['name']=='pre_render_audit_evidence':return {'source_readable':True,'source_problem':'',
            'issues':[{**b,'source_segment':'seg_1','issue_index':1}]}
        return {'confirmed':[0],'reason':'A is real; B is compatible.'}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    result=audit.audit(directory)
    assert [issue['problem'] for issue in result['issues']]==['Keep A.']
