import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'projects/adventure/laughing-country'


def test_import_preserves_words_and_sources(tmp_path):
    from novel_manga.interactive.authored import prepare_authored
    episode = prepare_authored(PROJECT, tmp_path/'travel-master')
    script = json.loads((episode/'chapter_script.json').read_text())
    source = json.loads((PROJECT/'material.json').read_text())
    planned = [t['text'] for s in script['shots'] for t in s['turns'] if t['delivery_mode'] != 'silent_action']
    assert ''.join(planned) == ''.join(s['words'] for s in source['source_shots'] if s['kind'] in ('原文台词','新增结尾口播'))
    assert '被发现的警报声' not in ''.join(planned)
    assert {t['speaker_name'] for s in script['shots'] for t in s['turns'] if t['speaker_name']} == {'知遥','系统广播','守卫喊声'}
    assert sum(s['duration_seconds'] for s in script['shots']) == 292
    assert len(json.loads((episode/'source_trace.json').read_text())['shots']) == len(script['shots'])
    assert script['profile']['frame'] == '9:16'
    for shot in script['shots']:
        if shot['source_shot'] > 24:
            assert shot['device_state'] == 'off'
        if 30 <= shot['source_shot'] <= 45:
            assert shot['key_state'] != 'absent'
    by_id={s['shot_id']:s for s in script['shots']}
    assert by_id['L09b']['turns'][0]['delivery_mode']=='visible_dialogue'
    assert by_id['L09b']['turns'][0]['text']=='好可怕。'
    for sid in ('L09b','L20','L25','L42'):
        assert by_id[sid]['in_frame']==['知遥']
    choices = json.loads((episode/'interaction.json').read_text())
    assert [o['id'] for o in choices['options']] == ['A','B','C']
    assert all(o['reason'] for o in choices['options'])
    assert choices['selected'] is None


def test_groups_cover_every_beat_and_keep_reference_changes(tmp_path):
    from novel_manga.interactive.authored import prepare_authored, pack_authored
    episode = prepare_authored(PROJECT, tmp_path/'travel-master')
    plan = pack_authored(episode)
    script = json.loads((episode/'chapter_script.json').read_text())
    assert [i for c in plan['clips'] for i in c['shot_indexes']] == list(range(1,len(script['shots'])+1))
    assert sum(c['request_seconds'] for c in plan['clips']) == 292
    for clip in plan['clips']:
        assert 4 <= clip['request_seconds'] <= 15
        assert clip['stage_count'] <= 6
        assert clip['references']
        assert '真人' in clip['prompt']
        assert '恋综' not in clip['prompt']
    assert any(s['shot_id']=='L31b' for s in script['shots'])
    assert any(s['shot_id']=='L16c' for s in script['shots'])


def test_existing_edition_and_history_are_not_overwritten(tmp_path):
    from novel_manga.interactive.authored import prepare_authored
    target = tmp_path/'travel-master'
    target.mkdir()
    history = target/'generation-history.json'
    history.write_text('{"old":3}')
    with pytest.raises(FileExistsError):
        prepare_authored(PROJECT,target)
    assert history.read_text() == '{"old":3}'


def test_multiline_evidence_fits_physical_surface():
    from novel_manga.interactive.evidence import surface_image
    from novel_manga.config import DEFAULT_FONT_PATH
    font = DEFAULT_FONT_PATH
    cue={'kind':'text_panel','text':'必须佩戴快乐监控装置\n公共场所保持微笑\n传播悲伤情绪者送往快乐矫正中心'}
    layer=surface_image(cue,font)
    # The panel covers any native model lettering; inspect only the new ink.
    assert layer.getpixel((0,0))[3]==255
    ink=layer.convert('L').point(lambda value:255 if value<180 else 0)
    box=ink.getbbox()
    assert box is not None
    assert 10 <= box[0] and box[2] <= 790
    assert box[1] > 50 and box[3] < 450


def test_actual_flat_occlusion_is_normalized():
    from novel_manga.interactive.evidence import validated_region
    region={'cue_id':'paper','quad':[[100,100],[900,100],[900,900],[100,900]],
        'occlusions':[[480,520,620,620,620,720,480,620]],'anchors':[]}
    result=validated_region(region)
    assert result['occlusions']==[[[480,520],[620,620],[620,720],[480,620]]]
    assert region['occlusions'][0][0]==480
    box=validated_region({**region,'occlusions':[[380,650,750,1000]]})
    assert box['occlusions']==[[[380,650],[750,650],[750,1000],[380,1000]]]
    with pytest.raises(ValueError):validated_region({**region,'occlusions':[[1,2,3]]})


def test_voice_task_resume_does_not_consume_new_submission(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from novel_manga.interactive import authored, runtime
    from novel_manga.util import atomic_write_json,read_json
    novel=tmp_path/'travel-master';novel.mkdir()
    atomic_write_json(novel/'voice_specs.json',{'知遥':{'prompt':'Voice casting','sample_text':'大家好','picture':'portrait.jpeg'}})
    calls=[]
    class Provider:
        before_video_submit=None
        def create_video(self,prompt,image,output,duration):
            calls.append(output)
            task=output.with_suffix('.mp4.task.json')
            if not task.exists():
                self.before_video_submit()
                atomic_write_json(task,{'task_id':'original-task'})
            if len(calls)<3:raise TimeoutError('poll interrupted')
            output.write_bytes(b'completed same task')
    provider=Provider()
    monkeypatch.setattr(runtime,'runner_for',lambda *a,**k:SimpleNamespace(context=SimpleNamespace(provider=provider)))
    monkeypatch.setattr(authored,'pack_authored',lambda _: {})
    monkeypatch.setattr(authored,'run',lambda args:Path(args[-1]).write_bytes(b'wav'))
    for _ in range(2):
        with pytest.raises(TimeoutError):authored.build_authored_voices(tmp_path,novel)
    authored.build_authored_voices(tmp_path,novel)
    bank=read_json(novel/'series_assets/voices/voices.json',{})
    assert len(calls)==3 and bank['知遥']['sample_requests']==1
    assert bank['知遥']['task_id']=='original-task'
    assert bank['知遥']['status']=='prepared'


def test_native_sampling_reads_the_actual_h3_adapter():
    from types import SimpleNamespace
    from novel_manga.media.adapters import FramedLocalH3
    from novel_manga.interactive.delivery import requested_sampling
    adapter=object.__new__(FramedLocalH3)
    adapter.local=SimpleNamespace(sampling_parameters=lambda:{'short_edge':768})
    assert requested_sampling(adapter,{'resolution':'480p'}) == ('768P',False)
    assert requested_sampling(SimpleNamespace(),{'resolution':'480p'}) == ('480p',True)


def test_h3_context_identifies_postproduction_labels(tmp_path):
    from novel_manga.application.rendering.h3 import request_context, POST_OVERLAY_RULES
    from novel_manga.util import atomic_write_json
    atomic_write_json(tmp_path/'segments.json',[{'segment_id':'seg_54','text':'交换人生预览；字幕文字为后期层'}])
    atomic_write_json(tmp_path/'chapter_script.json',{'shots':[{'index':54,'source_quote':'原画面说明'}]})
    atomic_write_json(tmp_path/'overlays.json',[{'index':53,'text':'A 巨人花园'},{'index':54,'text':'B 交换人生之国'}])
    context,images=request_context({'shot_indexes':[54],'segment_ids':['seg_54'],'references':[]},tmp_path)
    assert context['postproduction_overlays']==[{'index':54,'text':'B 交换人生之国'}]
    assert 'postproduction_overlays' in POST_OVERLAY_RULES
    assert not images


def test_composite_failure_closes_pipes_before_waiting(tmp_path,monkeypatch):
    import io
    from novel_manga.interactive import evidence
    from novel_manga.config import DEFAULT_FONT_PATH
    processes=[]
    class Process:
        def __init__(self,*args,**kwargs):
            self.stdout=io.BytesIO(bytes(64*96*3)) if 'stdout' in kwargs else None
            self.stdin=io.BytesIO() if 'stdin' in kwargs else None
            self.stopped=False
            processes.append(self)
        def poll(self):return 0 if self.stopped else None
        def terminate(self):self.stopped=True
        def wait(self,timeout=None):
            assert all(p is None or p.closed for p in (self.stdout,self.stdin))
            assert timeout is not None
            return 0
        def kill(self):self.stopped=True
    monkeypatch.setattr(evidence.subprocess,'Popen',Process)
    monkeypatch.setattr(evidence,'_media',lambda _:({'width':64,'height':96},1))
    def fail(*args):raise ValueError('unrenderable physical plane')
    monkeypatch.setattr(evidence,'paste_surface',fail)
    region={'cue_id':'sign','quad':[[0,0],[1000,0],[1000,1000],[0,1000]],'occlusions':[]}
    observed={'duration':1,'cuts':[],'frames':[{'time':0,'regions':[region]},{'time':.9,'regions':[region]}]}
    with pytest.raises(ValueError,match='unrenderable'):
        evidence.composite_evidence(tmp_path/'input.mp4',tmp_path/'out.mp4',
            [{'start':0,'end':1,'observed':observed}],[{'id':'sign','kind':'sign','text':'字'}],DEFAULT_FONT_PATH)
    assert all(p.stopped for p in processes)
