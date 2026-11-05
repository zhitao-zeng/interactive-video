"""Publishing a native assembly must retain the identity of a separately reviewed export."""
from pathlib import Path
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.repair.delivery import publish_if_ready


@pytest.mark.parametrize('separate_export',[False,True])
def test_native_publication_only_relocates_review_of_the_same_movie(tmp_path,monkeypatch,separate_export):
    episode=tmp_path/'book_1';candidate=episode/'repair_candidate/native.mp4'
    candidate.parent.mkdir(parents=True);candidate.write_bytes(b'native assembly')
    exported=tmp_path/'delivery-with-voting-card.mp4';exported.write_bytes(b'actual reviewed export with captions and card')
    observed=exported if separate_export else candidate
    review={'movie':str(observed),'complete':True,'passed':True,'issues':[],'errors':[]}
    atomic_write_json(episode/'thin_media_report.json',{'assembly':{'final_video':str(candidate),'pending_publish':True},'final_cut_review':review})
    atomic_write_json(episode/'clip_plan.json',{'clips':[{'kind':'video','clip_id':'a'}]})
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'done')
    monkeypatch.setattr('novel_manga.review.contracts.current_review',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.repair.delivery.precise_review_blockers',lambda *a:[])
    monkeypatch.setattr('novel_manga.application.review.final_cut.current',lambda *a:True)
    monkeypatch.setattr('novel_manga.application.review.final_cut.context',lambda d,m,p:{'observed_movie':str(p)})
    assert publish_if_ready(episode,{}, {})
    media=read_json(episode/'thin_media_report.json');native=episode/'book_1.mp4'
    expected=exported if separate_export else native
    assert Path(media['assembly']['final_video']).read_bytes()==b'native assembly'
    assert media['final_cut_review']['movie']==str(expected)
    assert media['final_cut_review']['context']=={'observed_movie':str(expected)}
    assert not candidate.exists()and expected.exists()
