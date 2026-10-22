import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import final_cut
from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json, read_json
from support.repair_history import episode as publication_episode, candidate_episode


from support.delivery_cases import episode




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
        'clip_ids':['b'],'frame_numbers':[1],'severity':'must_fix','problem':'bad','evidence':'visible','source_basis':'source'}]}
    with pytest.raises(ValueError,match='no cited'):
        final_cut.validate_group(answer,[{'clip_id':'a'},{'clip_id':'b'}])


def test_invalid_review_response_returns_exact_protocol_feedback_once(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);calls=[]
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    issue={'clip_ids':['a'],'frame_numbers':[1],'severity':'must_fix','problem':'wet ground on dry platform',
           'evidence':'visible water','source_basis':'source requires dry platform'}
    def judge(parts,*args,**kwargs):
        calls.append(parts[-1]['text'])
        return {'passed':len(calls)==1,'observations':['visible water'],'issues':[issue.copy()]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['complete'] is True and result['passed'] is False
    assert len(calls)==2 and 'conflicting passage' in calls[1]
    assert 'wet ground on dry platform' in calls[1]
    assert len(result['responses'])==2 and 'protocol_error' in result['responses'][0]
    assert result['issues'][0]['problem']==issue['problem']


def test_repeated_bad_protocol_stops_without_certifying_movie(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path);calls=[]
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    def judge(*args,**kwargs):calls.append(1);return {'passed':False,'observations':['uncertain'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert len(calls)==2 and result['complete'] is False and result['passed'] is False
    assert result['errors'] and len(result['responses'])==2


def test_final_reviewer_uses_approved_caption_stage_without_raw_request_prohibitions(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path)
    runner.context.clip_plan['clips'][0]['prompt_h3']='native-only-no-text-sentinel'
    ass=tmp_path/'dialogue.ass';ass.write_text('Dialogue: 0,0:00:00.00,0:00:09.00,Default,,0,0,0,,approved dialogue\n')
    media['assembly']['ass']=str(ass)
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':9}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda command,**k:Path(command[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'test image placeholder'})
    def judge(parts,*args,**kwargs):
        text=json.dumps(parts,ensure_ascii=False)
        assert 'native-only-no-text-sentinel' not in text
        assert 'approved dialogue' in text and 'expected approved captions' in text
        assert '单层吻合expected_captions的字幕完全合法' in text
        return {'passed':True,'observations':['approved single caption layer'],'issues':[]}
    monkeypatch.setattr(final_cut.model,'ask_json',judge)
    result=final_cut.review_assembly(runner,media)
    assert result['passed'] and result['frames'][0]['expected_captions']==['approved dialogue']


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
    # reviewed_delivery runs the final audio review inline; a passing review bound
    # to this movie keeps the delivery gate exercised without a real audio model.
    def audio_review(root, episode_dir, movie, *, runtime=None):
        return {'movie': str(movie), 'complete': True, 'passed': True,
                'issues': [], 'errors': [], 'coverage': []}
    monkeypatch.setattr('novel_manga.application.review.final_audio.review_audio', audio_review)
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


def test_publication_card_is_reviewed_and_later_frames_use_movie_time(tmp_path,monkeypatch):
    runner,media,movie=episode(tmp_path)
    second={'clip_id':'b','kind':'video','shot_indexes':[2],'references':[]}
    runner.context.clip_plan['clips'].append(second)
    media['assembly']['timeline'].append({'unit_id':'b','start':9,'end':18})
    media['clips'].append({'clip_id':'b','selected':{'video':str(movie),'chunks':[]}})
    atomic_write_json(runner.context.episode_dir/'clip_plan.json',runner.context.clip_plan)
    card={'after_clip_id':'a','start':9,'end':12,'seconds':3,'title':'approved world title'}
    atomic_write_json(runner.context.episode_dir/'publication.json',{'world_card':card})
    monkeypatch.setattr(final_cut.subprocess,'check_output',lambda *a,**k:json.dumps({'format':{'duration':21}}))
    monkeypatch.setattr(final_cut.subprocess,'run',lambda c,**k:Path(c[-1]).write_bytes(b'frame'))
    monkeypatch.setattr(final_cut.model,'image_part',lambda *a:{'type':'text','text':'fixture'})
    monkeypatch.setattr(final_cut.model,'ask_json',lambda *a,**k:{'passed':True,'observations':['actual supplied frames'],'issues':[]})
    result=final_cut.review_assembly(runner,media,receipt={'final_video':str(movie),'publication':{'world_card':card}})
    assert result['complete']
    assert [r['seconds'] for r in result['frames'] if r['clip_id']=='b']==[12.15,16.5,20.8]
    world=next(r for r in result['frames'] if 'world introduction card' in r['label'])
    assert world['seconds']==10.5 and 'approved world title' in world['approved_labels']
    assert media['assembly']['timeline'][1]['start']==9


def test_ambience_asr_uses_publication_shifted_movie_time():
    media={'assembly':{'timeline':[{'unit_id':'a','start':0,'end':9},{'unit_id':'b','start':9,'end':18}]},
           'clips':[{'clip_id':'b','selected':{'chunks':[{'start':3,'end':4,'hypothesis':'spoken words'}]}}]}
    receipt={'publication':{'world_card':{'start':9,'seconds':3}},
             'ambience_source':{'clip_id':'b','start':15,'end':18}}
    issues=final_cut.ambience_speech_issues(media,receipt,[{'clip_id':'b','path':'frame.jpg'}])
    assert len(issues)==1 and 'spoken words' in issues[0]['evidence']
