from novel_manga.media.retries import starting_attempt
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json
from support.render_context import media_runner, video_clip


def unknown(directory, number=1, repair_take=1):
    root=directory/'work/clips/a'/f'attempt_{number:02d}'
    root.mkdir(parents=True,exist_ok=True)
    atomic_write_json(root/'request.json',{'repair_take':repair_take})
    atomic_write_json(root/'clip.mp4.task.json',{'submit_uncertain_at':1,'model':'MiniMax-H3'})
    return root


def test_retake_uses_distinct_attempt_and_keeps_unknown_bytes(tmp_path,monkeypatch):
    runner=media_runner(tmp_path/'book');root=unknown(runner.context.episode_dir)
    before={p.name:p.read_bytes() for p in root.iterdir()}
    item=video_clip();item['repair_take']=2
    calls=[]
    def generate(clip,attempt):
        calls.append(attempt)
        path=runner.context.work/'clips/a'/f'attempt_{attempt:02d}'/'clip.mp4'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'new')
        return path
    monkeypatch.setattr(runner,'generate_clip',generate)
    monkeypatch.setattr(runner,'analyse_clip',lambda c,p:{'passed':True,'video':str(p),'cer':0,'max_volume_db':0,'issues':[]})
    result=runner.process_clip(item)
    assert calls==[2] and result['selected']['passed']
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}


def test_unchanged_request_stays_at_unknown_attempt(tmp_path):
    unknown(tmp_path)
    assert starting_attempt(tmp_path/'work',{'clip_id':'a','repair_take':1})==1
    assert starting_attempt(tmp_path/'work',{'clip_id':'a','repair_take':2})==2


def test_unknown_occupies_original_budget_without_becoming_confirmed(tmp_path):
    unknown(tmp_path)
    assert history.generation_counts(tmp_path)=={}
    assert history.remaining_takes(tmp_path,'a')==2
    assert history.remaining_takes(tmp_path,'a',counts={'a':2})==0


def test_attempt_ceiling_does_not_refresh_on_new_retake(tmp_path,monkeypatch):
    runner=media_runner(tmp_path/'book');runner.context.options.max_attempts=1
    unknown(runner.context.episode_dir)
    monkeypatch.setattr(runner,'generate_clip',lambda *args:(_ for _ in ()).throw(AssertionError('must not submit')))
    result=runner.process_clip({**video_clip(),'repair_take':2})
    assert result['selected'] is None and 'attempt limit used' in result['error']
