"""H3 compilation can resume a passed opening without buying or reviewing another image."""
import copy

from PIL import Image
import pytest

from novel_manga.application.agents.episode import first_frames
from novel_manga.application.rendering import h3
from novel_manga.application.profiles import h3_stamp
from novel_manga.service_errors import UnsupportedInput, service_failure, service_failure_from_saved
from novel_manga.util import atomic_write_json, read_json
from support.first_frame_cases import case


def opening_case(tmp_path, monkeypatch):
    ctx, clips = case(tmp_path, monkeypatch, host_h3=True)
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': clips[:1]})
    atomic_write_json(ctx.work_dir/'output/first-frames.json', {
        'render_ready': False, 'frames': [{'clip_id':'clip_01','extra_references':[]}]})
    return ctx


def pixel_and_review(calls):
    def build(ctx, row, path):
        calls.append('image')
        Image.new('RGB',(8,16),'blue').save(path)
    def review(*args):
        calls.append('review')
        return {'findings':[]}
    return build, review


@pytest.mark.parametrize('error_kind', ['connection', 'malformed'])
def test_passed_opening_survives_h3_failure_then_same_request_resumes_without_media(tmp_path, monkeypatch, error_kind):
    if error_kind == 'malformed':
        from novel_manga.llm.client import ModelOutputMalformed
        error = ModelOutputMalformed('h3prompt', tmp_path/'native/output/response.json')
    else:
        error = ConnectionError('H3 translator connection unavailable')
    ctx = opening_case(tmp_path, monkeypatch)
    calls=[]
    def convert(clip, *, errors=None, problems=None, **options):
        calls.append('compile')
        assert options['tries']==1
        if calls.count('compile')==1:
            if errors is not None:errors.append(error)
            problems.append(type(error).__name__+': '+str(error))
            return False
        clip.update(prompt_h3='English request', prompt_h3_of=h3_stamp(clip))
        return True
    monkeypatch.setattr(h3,'convert',convert)
    build,review = pixel_and_review(calls)
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=review)
    with pytest.raises(type(error)):
        operation.prepare({})
    failure=operation.partial_outcome['clips']['clip_01']
    assert failure['stage']=='h3_compile'
    assert failure['candidate']=='candidate_01' and failure['image_review_passed'] is True
    assert failure['retryable'] is True and failure['service_interrupted']
    assert not {'source_input_conflicts','review_incomplete'}.intersection(failure)
    assert failure.get('repair_grant',{}).get('operation')!='input'
    candidate=ctx.novel_dir/'first_frames'/ctx.episode_dir.name/'clip_01/candidate_01'
    before={str(f.relative_to(candidate)):f.read_bytes() for f in candidate.rglob('*') if f.is_file()}
    assert not (candidate/'accepted.json').exists()
    assert not read_json(ctx.episode_dir/'clip_plan.json')['clips'][0].get('first_frame')
    result=first_frames.FirstFrameOperations(ctx,builder=build,checker=review).prepare({})
    assert result['ok'] is True and result['changed']==['clip_01']
    assert calls==['image','review','compile','compile']
    assert all((candidate/name).read_bytes()==value for name,value in before.items())
    assert len(list(candidate.parent.glob('candidate_*')))==1
    clip=read_json(ctx.episode_dir/'clip_plan.json')['clips'][0]
    assert clip['first_frame']['candidate']=='candidate_01' and clip['prompt_h3']=='English request'
    assert (candidate/'accepted.json').is_file()
    assert not list(ctx.episode_dir.rglob('*.mp4'))


@pytest.mark.parametrize('error', [AssertionError('compiler invariant'), UnsupportedInput('source conflict')])
def test_unknown_or_content_compile_error_is_not_retryable(tmp_path, monkeypatch, error):
    ctx=opening_case(tmp_path,monkeypatch)
    def convert(clip, *, errors=None, problems=None, **options):
        if errors is not None:errors.append(error)
        problems.append(str(error))
        return False
    monkeypatch.setattr(h3,'convert',convert)
    calls=[];build,review=pixel_and_review(calls)
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=review)
    with pytest.raises(type(error)):
        operation.prepare({})
    row=operation.partial_outcome['clips']['clip_01']
    assert row['stage']=='h3_compile' and row['image_review_passed'] is True
    assert row['retryable'] is False and 'service_interrupted' not in row
    assert 'repair_grant' not in row


def test_convert_preserves_original_exception_without_changing_legacy_retries(monkeypatch):
    clip={'clip_id':'clip_01','request_seconds':4,'references':[],
          'prompt':'【阶段1】水面轻晃。结束时：水面平静。画面呈现'}
    original=copy.deepcopy(clip);calls=[];failure=ConnectionError('unavailable')
    def ask(*args,**kwargs):calls.append(1);raise failure
    monkeypatch.setattr(h3,'ask_json',ask)
    problems=[];errors=[]
    assert h3.convert(clip,tries=3,problems=problems,errors=errors) is False
    assert len(calls)==3 and errors==[failure]
    assert problems==['ConnectionError: unavailable'] and clip==original


def test_source_conflicts_are_not_the_exception_channel(monkeypatch):
    clip={'clip_id':'clip_01','request_seconds':4,'references':[],
          'prompt':'【阶段1】水面轻晃。结束时：水面平静。画面呈现'}
    monkeypatch.setattr(h3,'ask_json',lambda *a,**k:{'shots':[],'soundscape':'','conflicts':['source states conflict']})
    problems=[];source=[];errors=[]
    assert h3.convert(clip,tries=3,problems=problems,source_conflicts=source,errors=errors) is False
    assert problems==source==['source states conflict'] and errors==[]


def test_only_typed_malformed_model_output_is_a_service_retry(tmp_path):
    from novel_manga.llm.client import ModelOutputMalformed
    outcome=service_failure(ModelOutputMalformed('h3prompt',tmp_path/'response.json'))
    assert outcome['retryable'] is True
    assert outcome['service_interrupted']['kind']=='model_output_malformed'
    assert service_failure_from_saved(outcome['service_interrupted'])['retryable'] is True
    assert service_failure(ValueError('invalid source'))=={'retryable':False}
    assert service_failure(AssertionError())=={'retryable':False}


def test_normal_recovery_selects_passed_retake_instead_of_rejected_parent(tmp_path,monkeypatch):
    ctx=opening_case(tmp_path,monkeypatch);calls=[]
    def review(*args):
        calls.append('review')
        return {'findings':[{'origin':'image','relation':'contradiction','reason':'wrong visible pose'}]} if calls.count('review')==1 else {'findings':[]}
    build,_=pixel_and_review(calls)
    assert first_frames.FirstFrameOperations(ctx,builder=build,checker=review).prepare({})['ok'] is False
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'render_ready':False,'frames':[
        {'clip_id':'clip_01','extra_references':[],'retake_candidate':{
            'candidate':'candidate_01','reason':'correct the rejected visible pose'}}]})
    error=ConnectionError('compile connection failed')
    def convert(clip,*,errors=None,problems=None,**options):
        calls.append('compile')
        if calls.count('compile')==1:
            errors.append(error);problems.append(str(error));return False
        clip.update(prompt_h3='English request',prompt_h3_of=h3_stamp(clip));return True
    monkeypatch.setattr(h3,'convert',convert)
    operation=first_frames.FirstFrameOperations(ctx,builder=build,checker=review)
    with pytest.raises(ConnectionError):operation.prepare({})
    assert operation.partial_outcome['clips']['clip_01']['candidate']=='candidate_02'
    atomic_write_json(ctx.work_dir/'input/latest-first-frames.json',{'ok':False,**operation.partial_outcome})
    atomic_write_json(ctx.work_dir/'output/first-frames.json',{'render_ready':False,'frames':[
        {'clip_id':'clip_01','extra_references':[]}]})
    result=first_frames.FirstFrameOperations(ctx,builder=build,checker=review).prepare({})
    assert result['ok'] is True
    assert result['clips']['clip_01']['first_frame']['candidate']=='candidate_02'
    assert 'Confirmed defects to correct' in result['clips']['clip_01']['first_frame']['request_prompt']
    assert calls==['image','review','image','review','compile','compile']


@pytest.mark.parametrize('directions,expected_error', [
    (['A waits.','Extra scene.'],'requires exactly 1 items'),
    (['A waits.'],'lost or reordered dialogue slots'),
    (['人物 waits. <DIALOGUE_1>'],'contains Chinese or task commentary')])
def test_h3_native_schema_keeps_shot_count_marker_and_english_postconditions(monkeypatch,directions,expected_error):
    clip={'clip_id':'clip_01','request_seconds':4,
          'references':[{'role':'character','name':'梁舟','path':'actor.jpeg'}],
          'prompt':'【阶段1】梁舟站在门边。声音：中文普通话，压低声音且迟疑，梁舟开口说：{谁在那里？}结束时：门打开。画面呈现'}
    captured=[]
    def ask(parts,schema,**kwargs):
        captured.append(schema)
        return {'shots':directions,'soundscape':'None.','conflicts':[]}
    monkeypatch.setattr(h3,'ask_json',ask)
    problems=[]
    assert h3.convert(clip,tries=1,problems=problems) is False
    shots=captured[0]['properties']['shots']
    assert shots['minItems']==shots['maxItems']==len(shots['prefixItems'])==1
    assert shots['items']==h3.SCHEMA['properties']['shots']['items']
    assert '<DIALOGUE_1>' in shots['prefixItems'][0]['pattern']
    assert expected_error in problems[0]
