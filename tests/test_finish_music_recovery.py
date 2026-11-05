import pytest
from types import SimpleNamespace
from novel_manga.util import atomic_write_json, read_json


def test_finished_assembly_survives_postmix_failure(tmp_path, monkeypatch):
    from novel_manga.application.agents.episode.media_operations import MediaOperations
    from novel_manga.application.review import execution
    from novel_manga.application.repair import ambience
    ctx=SimpleNamespace(episode_dir=tmp_path,run_dir=tmp_path/'run')
    atomic_write_json(tmp_path/'clip_plan.json',{'clips':[]})
    monkeypatch.setattr(execution,'media_matches',lambda *a,**k:True)
    monkeypatch.setattr('novel_manga.application.agents.episode.media_operations.record_state',lambda *a,**k:None)
    monkeypatch.setattr(MediaOperations,'hydrate_analysis',lambda self,report,runner:report)
    monkeypatch.setattr(ambience,'apply_assembly',lambda *a,**k:(_ for _ in ()).throw(ValueError('selected source 42s shorter than cue 43.499s')))
    movie=tmp_path/'body.mp4';movie.write_bytes(b'encoded once')
    runner=SimpleNamespace(assemble=lambda clips:{'final_video':str(movie),'thin_passed':True},context=ctx)
    with pytest.raises(ValueError,match='43.499'):
        MediaOperations(ctx,None,None).assemble({'status':'clips_ready','clips':[]},runner)
    saved=read_json(tmp_path/'thin_media_report.json',{})
    assert saved['assembly']['final_video']==str(movie)
    assert saved['status']=='assembled'
