import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import final_cut
from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json, read_json
from support.repair_history import episode as publication_episode, candidate_episode


def episode(tmp_path):
    directory=tmp_path/'book'/'book_1';directory.mkdir(parents=True)
    movie=directory/'movie.mp4';movie.write_bytes(b'movie')
    clip={'clip_id':'a','kind':'video','shot_indexes':[1],'references':[]}
    plan={'clips':[clip]}
    atomic_write_json(directory/'clip_plan.json',plan)
    atomic_write_json(directory/'chapter_script.json',{'shots':[]})
    atomic_write_json(directory/'episode_review.json',{'clips':{'a':{'severity':'pass'}},'feedback':{}})
    media={'clips':[{'clip_id':'a','selected':{'video':str(movie),'chunks':[]}}],
           'assembly':{'final_video':str(movie),'thin_passed':True,
                       'timeline':[{'unit_id':'a','start':0,'end':9}]}}
    ctx=SimpleNamespace(episode_dir=directory,clip_plan=plan,script={'shots':[]},bible=None,
                        settings=SimpleNamespace(width=1080,height=1920,font_path='/font'))
    return SimpleNamespace(context=ctx),media,movie


def test_real_extractor_cache_and_incomplete_retry(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);calls=[]
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    def extract(command,**kwargs):Path(command[-1]).write_bytes(b'frame')
    monkeypatch.setattr(final_cut.subprocess,'run',extract)
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    def judge(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise TimeoutError('service failed')
        return {'passed':True,'observations':['Visible frames match the source'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    assert final_cut.review_assembly(runner,media)['complete'] is False
    result=final_cut.review_assembly(runner,media)
    assert result['complete'] is True and result['passed'] is True
    assert len(result['frames'])==3
    assert final_cut.review_assembly(runner,media)==result
    assert len(calls)==2
    movie.write_bytes(b'new movie')
    assert final_cut.review_assembly(runner,media)['complete'] is True
    assert len(calls)==3
    assert len(list((runner.context.episode_dir/'work/final_cut_review').glob('review-*/review.json')))==3


def test_issue_requires_cited_clip_evidence():
    answer={'passed':False,'observations':['actual frames'], 'issues':[{
        'clip_ids':['b'],'frame_numbers':[1],'problem':'bad','evidence':'visible','source_basis':'source'}]}
    with pytest.raises(ValueError,match='no cited'):
        final_cut.validate_group(answer,[{'clip_id':'a'},{'clip_id':'b'}])


def test_ambience_check_uses_global_asr_overlap():
    media={'assembly':{'timeline':[{'unit_id':'a','start':100,'end':112}]},
           'clips':[{'clip_id':'a','selected':{'chunks':[
               {'start':7,'end':9,'hypothesis':'one sentence'},
               {'start':10,'end':11,'hypothesis':'last sentence'}]}}]}
    receipt={'ambience_source':{'clip_id':'a','start':108,'end':111}}
    issues=final_cut.ambience_speech_issues(media,receipt,[{'clip_id':'a','path':'frame.jpg'}])
    assert issues[0]['clip_ids']==['a']
    evidence=json.loads(issues[0]['evidence'])
    assert sum(row['overlap'] for row in evidence['ASR_overlap'])==2
    receipt['ambience_source'].update(start=111,end=114)
    assert final_cut.ambience_speech_issues(media,receipt,[])==[]


def test_delivery_reuses_reviewed_movie_and_refuses_changed_export(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);directory=runner.context.episode_dir
    destination=tmp_path/'delivery';calls=[]
    def export(runner,report,path):
        calls.append(path);path.mkdir(parents=True)
        final=path/'episode01.mp4';final.write_bytes(b'export')
        return {'final_video':str(final),'technical_passed':True}
    monkeypatch.setattr(delivery,'deliver',export)
    def review(runner,media,receipt=None):
        return {'movie':receipt['final_video'],'policy':final_cut.POLICY,'complete':True,'passed':True,
                'issues':[],'errors':[],'frames':[],
                'context':final_cut.context(directory,media,receipt['final_video'])}
    monkeypatch.setattr(final_cut,'review_assembly',review)
    final=delivery.reviewed_delivery(runner,media,destination)
    media['final_cut_review']=final
    native=read_json(directory/'episode_review.json')
    native['clips']['a']['final_cut']={'issues':[]};atomic_write_json(directory/'episode_review.json',native)
    delivery.reviewed_delivery(runner,media,destination)
    assert len(calls)==1
    media['quality_review']={'passed':True,'content_passed':True}
    assert delivery.finish_reviewed_delivery(runner,media,destination)['status']=='accepted'
    atomic_write_json(directory/'interaction.json',{'changed':'card'})
    assert delivery.finish_reviewed_delivery(runner,media,destination)['status']=='candidate'
    delivery.reviewed_delivery(runner,media,destination)
    assert len(calls)==2 and calls[0]!=calls[1]
    assert (calls[0]/'episode01.mp4').is_file()


def test_apply_feedback_preserves_native_identity_and_does_not_duplicate(tmp_path):
    runner,media,movie=episode(tmp_path)
    native={'clips':{'a':{'severity':'pass','verify':{'verdict':'fine'}}},'feedback':{'a':'native'}}
    final={'movie':str(movie),'context':{},'frames':[],'complete':True, 'issues':[{'clip_ids':['a'],
           'problem':'double subtitles','source_basis':'one caption track','evidence':'two lines'}]}
    merged=final_cut.apply_feedback(runner.context.episode_dir,native,final)
    assert merged['clips']['a']['severity']=='pass'
    assert merged==final_cut.apply_feedback(runner.context.episode_dir,merged,final)
    cleared=final_cut.apply_feedback(runner.context.episode_dir,merged,{**final,'issues':[]})
    assert cleared['feedback']=={'a':'native'}


def test_publication_preserves_review_of_the_exact_controlled_copy(publication_episode):
    from novel_manga.application.repair import delivery as publisher
    directory,plan,review,media,takes=candidate_episode(publication_episode)
    review['feedback']={};review['clips']['clip_01'].update(story_ok=True,verify={'verdict':'fine'})
    atomic_write_json(directory/'episode_review.json',review)
    movie=media['assembly']['final_video']
    final={'policy':final_cut.POLICY,'movie':movie,'context':final_cut.context(directory,media,movie),
           'complete':True,'passed':True,'issues':[],'errors':[]}
    # The renderer can replace the report without carrying this prior channel.
    atomic_write_json(directory/'final_cut_review.json',final)
    assert publisher.publish_if_ready(directory,review,takes)
    promoted=read_json(directory/'thin_media_report.json')
    assert final_cut.current(directory,promoted,promoted['final_cut_review'])
    assert Path(promoted['final_cut_review']['movie']).read_bytes()==b'new candidate'
    assert not Path(movie).exists()


def test_persistent_failed_final_review_blocks_native_only_publication(publication_episode):
    from novel_manga.application.repair import delivery as publisher
    directory,plan,review,media,takes=candidate_episode(publication_episode)
    review['feedback']={};review['clips']['clip_01'].update(story_ok=True,verify={'verdict':'fine'})
    atomic_write_json(directory/'episode_review.json',review)
    movie=media['assembly']['final_video']
    final={'policy':final_cut.POLICY,'movie':movie,'context':final_cut.context(directory,media,movie),
           'complete':True,'passed':False,'issues':[{'clip_ids':['clip_01'],'problem':'actual movie failed'}],'errors':[]}
    atomic_write_json(directory/'final_cut_review.json',final)
    assert publisher.publish_if_ready(directory,review,takes) is False
    assert (directory/'nov_1.mp4').read_bytes()==b'incumbent movie'
