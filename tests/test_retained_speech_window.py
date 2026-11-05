import copy
from types import SimpleNamespace as NS

import pytest

from novel_manga.media.retained import silent_insert_seconds,speech_in_retained_window
from novel_manga.media import speech
from novel_manga.util import atomic_write_json,read_json


def silence():
    clip={'clip_id':'clip_01','kind':'video','shot_indexes':[1],'request_seconds':4,
          'spoken_text':'','dialogue_bindings':[]}
    script={'shots':[{'index':1,'duration_seconds':2,'turns':[]}]}
    return clip,{'h3_input_mode':'first-frame'},script


@pytest.mark.parametrize('start,failed',[(2.985,False),(2.,False),(1.8,True),(0.,True)])
def test_only_wholly_discarded_tail_speech_is_excluded(start,failed):
    raw={'duration':4.482,**speech.evaluate('',[{'start':start,'end':4.48,
         'hypothesis':'这句话不属于原稿'}],-16,-6)}
    before=copy.deepcopy(raw)
    result=speech_in_retained_window(raw,2)
    assert ('unscripted_speech'in result['issues'])is failed
    assert result['passed']is (not failed)
    assert raw==before
    assert result['retained_speech_window']=={'start':0.,'end':2,'complete':True}


def test_window_never_applies_to_spoken_or_other_modes_and_does_not_clear_other_failures():
    clip,profile,script=silence()
    assert silent_insert_seconds(clip,profile,script)==2
    script['shots'][0]['turns']=[{'text':'必须保留的台词'}]
    assert silent_insert_seconds(clip,profile,script)is None
    script['shots'][0]['turns']=[]
    assert silent_insert_seconds(clip,{},script)is None
    raw={**speech.evaluate('',[{'start':3,'end':4,'hypothesis':'这句话不属于原稿'}],-16,-6),
         'issues':['unscripted_speech','black_frames'],'passed':False}
    assert speech_in_retained_window(raw,2)['issues']==['black_frames']


def test_complete_cached_analysis_is_rescoped_without_video_or_raw_asr_changes(tmp_path):
    from novel_manga.application.agents.episode.media_operations import MediaOperations
    clip,profile,script=silence();book=tmp_path/'book';episode=book/'book_1';work=tmp_path/'sandbox'
    episode.mkdir(parents=True);work.mkdir()
    atomic_write_json(book/'profile.json',profile)
    atomic_write_json(episode/'chapter_script.json',script)
    atomic_write_json(episode/'clip_plan.json',{'clips':[clip]})
    raw={'clip_id':'clip_01','video':str(episode/'original.mp4'),'duration':4.482,
         **speech.evaluate('',[{'start':2.985,'end':4.48,'hypothesis':'这句话不属于原稿'}],-16,-6)}
    atomic_write_json(episode/'asr.json',raw);before=(episode/'asr.json').read_bytes()
    report={'clips':[{'clip_id':'clip_01','attempts':[copy.deepcopy(raw)],'selected':copy.deepcopy(raw)}],
            'gate_failed_clips':['clip_01'],'clip_plan_fingerprint':'streaming-first-takes'}
    ctx=NS(novel_dir=book,episode_dir=episode,work_dir=work)
    calls=[]
    renderer=NS(context=NS(options=NS(cache_only=False),state=NS(black_checks=set())))
    def analyse(clip,video):
        assert renderer.context.options.cache_only
        calls.append(video)
        return speech_in_retained_window(raw,2)
    renderer.analyse_clip=analyse
    media=MediaOperations(ctx,None,None)
    result=media.hydrate_analysis(report,renderer)
    assert len(calls)==1 and result['gate_failed_clips']==[]
    assert result['clips'][0]['selected']['passed']
    assert (episode/'asr.json').read_bytes()==before
    media.hydrate_analysis(result,renderer)
    assert len(calls)==1
