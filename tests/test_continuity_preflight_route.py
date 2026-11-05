"""A changed neighbouring source must be checked without regenerating an approved frame."""
import copy

from novel_manga.application.agents.episode import opening_source, readiness
from novel_manga.util import atomic_write_json
from support.first_frame_cases import case


def setup_route(tmp_path, monkeypatch):
    ctx, clips = case(tmp_path, monkeypatch)
    clips[1]['first_frame'] = {'candidate':'candidate_02','path':'approved.png',
                               'review':{'consistent':True,'problems':[]}}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips':clips})
    monkeypatch.setattr(readiness,'first_frame_blocks',lambda *_:{})
    monkeypatch.setattr(opening_source,'source_blockers',lambda *_:{})
    pending={'clip_02':{'opening':{'start_state':'current'},
                        'previous_shot_context':{'shot_index':1,'start_state':'before'}}}
    monkeypatch.setattr(opening_source,'pending_context',lambda *_:copy.deepcopy(pending),raising=False)
    status={'adopted':True,'first_frames':{'clips':{
        'clip_02':{'current':True,'first_frame':{'candidate':'candidate_02'},
                   'repair_origin':'image','last_rejection':{'candidate':'candidate_01'}}}},
        'job':{'action':'apply','status':'completed','result':{'changed':['clip_01']}}}
    return ctx, status, pending


def test_host_rechecks_only_neighbour_using_existing_approved_candidate(tmp_path,monkeypatch):
    ctx,status,_=setup_route(tmp_path,monkeypatch)
    route=readiness.next_action(ctx,status)
    assert route['action']=='first_frames'
    assert set(route['source_preflight_rechecks'])=={'clip_02'}
    request=readiness.automatic_request(ctx,status)
    assert request['document']=={'render_ready':False,'frames':[{
        'clip_id':'clip_02','extra_references':[],'reuse_candidate':'candidate_02'}]}
    assert request['action']=='first_frames'


def test_unchanged_recheck_does_not_repeat_after_same_terminal_job(tmp_path,monkeypatch):
    ctx,status,_=setup_route(tmp_path,monkeypatch)
    request=readiness.automatic_request(ctx,status)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'next_action':{
        'automatic_fingerprint':request['fingerprint']}})
    status['job']={'action':'first_frames','status':'completed','result':{'ok':True,'changed':[]}}
    assert readiness.automatic_request(ctx,status) is None


def test_preflight_never_invents_a_candidate_for_accepted_frame(tmp_path,monkeypatch):
    ctx,status,_=setup_route(tmp_path,monkeypatch)
    status['first_frames']['clips']['clip_02']['first_frame'].pop('candidate')
    plan_path=ctx.episode_dir/'clip_plan.json'
    from novel_manga.util import read_json
    plan=read_json(plan_path);plan['clips'][1]['first_frame'].pop('candidate')
    atomic_write_json(plan_path,plan)
    assert readiness.automatic_request(ctx,status) is None


def test_only_host_grounded_source_clarifications_enter_existing_repair_route():
    from novel_manga.media.first_frames import interpret_review, source_conflicts
    row={'kind':'continuity_clarification','origin':'source','relation':'underspecified',
         'reason':'Please clarify the current opening using the cited source facts.'}
    assert interpret_review({'findings':[row]})['consistent'] is True
    assert source_conflicts({'findings':[row]})==[]
    verified={**row,'continuity_verified':True}
    projected=interpret_review({'findings':[verified]})
    assert projected['consistent'] is False and projected['problems']==[verified['reason']]
    assert source_conflicts(projected)==[verified]
    assert source_conflicts({'findings':[{**verified,'origin':'image'}]})==[]


def test_preflight_route_distinguishes_clarification_from_real_conflicts(tmp_path,monkeypatch):
    ctx,_=case(tmp_path,monkeypatch)
    clarification={'kind':'continuity_clarification','origin':'source','relation':'underspecified',
                   'continuity_verified':True,'reason':'Please clarify the current state.'}
    findings={'clip_01':[clarification]}
    monkeypatch.setattr(opening_source,'source_blockers',lambda *_:copy.deepcopy(findings))
    route=readiness.next_action(ctx,{})
    assert route['action']=='agent' and 'author clarification' in route['reason']
    assert route['source_input_conflicts']['clip_01']['source_conflicts']==[clarification]
    findings['clip_01'].append({'kind':'source_contradiction','origin':'source',
                                'relation':'contradiction','reason':'Conflicting explicit positions.'})
    route=readiness.next_action(ctx,{})
    assert route['action']=='agent' and 'fields conflict' in route['reason']


def test_changed_previous_shot_rechecks_text_and_reuses_unchanged_frame(tmp_path,monkeypatch):
    import json
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.util import read_json

    monkeypatch.setenv('NOVEL_MEDIA_WORKERS','1')
    ctx,clips=case(tmp_path,monkeypatch)
    shots=[]
    for clip in clips:
        shot=copy.deepcopy(clip['shots'][0])
        shot.update(scene_id='S1',event='A past action',end_state='A settled state')
        clip['shots']=[shot];shots.append(shot)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':shots})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':clips})
    monkeypatch.setattr(first_frames,'review_opening_sources',opening_source.review_batch)
    source_calls=[];images=[];image_reviews=[]
    def ask(parts,schema,**options):
        assert options['name']=='episode_opening_source_preflight'
        body=json.loads(parts[0]['text'].rsplit('\n',1)[-1]);source_calls.append(body)
        return {'reviews':[{'clip_id':item['clip_id'],'findings':[]} for item in body['clips']]}
    def build(ctx,row,path):
        images.append(row['clip_id']);Image.new('RGB',(8,16)).save(path)
    def check(*args):
        image_reviews.append(1);return {'findings':[]}
    monkeypatch.setattr('novel_manga.llm.client.ask_json',ask)
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=check)
    assert operation.prepare({})['ok'] is True
    assert images==['clip_01','clip_02'] and len(image_reviews)==2 and len(source_calls)==1
    original=read_json(ctx.episode_dir/'clip_plan.json')['clips'][1]['first_frame']
    # A candidate can have a retained old rejection followed by its current
    # accepted review. That history must not authorize a new paid retake.
    old_candidate=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_02'/'candidate_01'
    atomic_write_json(old_candidate/'rejected.json',{'error':'historical rejection','review':{
        'findings':[{'origin':'image','relation':'contradiction','reason':'old evidence'}]}})

    script=read_json(ctx.episode_dir/'chapter_script.json')
    script['shots'][0]['end_state']='A different, authored previous state'
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    before_calls=len(source_calls)
    status={'adopted':True,'first_frames':first_frames.status(ctx,plan),
            'job':{'action':'apply','status':'completed','result':{'changed':['clip_01']}}}
    assert status['first_frames']['clips']['clip_02']['current'] is True
    route=readiness.next_action(ctx,status)
    assert len(source_calls)==before_calls, 'read-only status/routing must not call a model'
    assert set(route['source_preflight_rechecks'])=={'clip_02'}
    request=readiness.automatic_request(ctx,status)
    assert request['document']['frames']==[{'clip_id':'clip_02','extra_references':[],
                                            'reuse_candidate':'candidate_01'}]
    atomic_write_json(ctx.work_dir/request['payload']['file'],request['document'])
    assert operation.prepare({})['ok'] is True
    assert images==['clip_01','clip_02'] and len(image_reviews)==2
    assert len(source_calls)==2 and [row['clip_id'] for row in source_calls[-1]['clips']]==['clip_02']
    current=read_json(ctx.episode_dir/'clip_plan.json')['clips'][1]['first_frame']
    assert current['path']==original['path'] and current['candidate']==original['candidate']
    assert not opening_source.pending_context(ctx,read_json(ctx.episode_dir/'clip_plan.json'))


def test_author_clarified_opening_rechecks_existing_pixels_before_new_image(tmp_path,monkeypatch):
    from PIL import Image
    from novel_manga.application.agents.episode import first_frames
    from novel_manga.util import read_json

    monkeypatch.setenv('NOVEL_MEDIA_WORKERS','1')
    ctx,clips=case(tmp_path,monkeypatch)
    shot=copy.deepcopy(clips[0]['shots'][0]);shot['scene_id']='S1'
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[shot]})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[clips[0]]})
    draft=read_json(ctx.work_dir/'output/first-frames.json');draft['frames']=draft['frames'][:1]
    atomic_write_json(ctx.work_dir/'output/first-frames.json',draft)
    images=[];reviews=[]
    def build(ctx,row,path):
        images.append(row['clip_id']);Image.new('RGB',(8,16)).save(path)
    def check(*args):reviews.append(1);return {'findings':[]}
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=check)
    assert operation.prepare({})['ok'] is True
    before=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']
    picture=(ctx.novel_dir/before['path']).read_bytes()
    script=read_json(ctx.episode_dir/'chapter_script.json')
    script['shots'][0]['start_state']='The pool is still empty at this opening.'
    script['shots'][0]['opening_frame']['image_prompt']='The pool remains empty at the start.'
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    status={'adopted':True,'first_frames':first_frames.status(ctx,plan),
            'job':{'action':'apply','status':'completed','result':{'changed':['clip_01']}}}
    row=status['first_frames']['clips']['clip_01']
    assert row['current'] is False and row['repair_grant']['operation']=='reuse_candidate'
    request=readiness.automatic_request(ctx,status)
    assert request['document']['frames']==[{'clip_id':'clip_01','extra_references':[],
                                             'reuse_candidate':'candidate_01'}]
    atomic_write_json(ctx.work_dir/request['payload']['file'],request['document'])
    assert operation.prepare({})['ok'] is True
    assert images==['clip_01'] and len(reviews)==2
    after=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]['first_frame']
    assert after['candidate']==before['candidate'] and (ctx.novel_dir/after['path']).read_bytes()==picture
