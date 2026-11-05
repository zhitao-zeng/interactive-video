"""Selection is a candidate, not consent to add music to a film."""
import copy
import math
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace as NS
import wave

import pytest

from novel_manga.util import atomic_write_json,read_json
from novel_manga.application import music_selection as music
from novel_manga.application.agents.episode.music import MusicOperations


def tone(path,hz,seconds=4):
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb')as w:
        w.setparams((1,2,16000,0,'NONE','not compressed'))
        w.writeframes(b''.join(struct.pack('<h',int(3000*math.sin(2*math.pi*hz*i/16000)))for i in range(seconds*16000)))


@pytest.fixture
def studio(tmp_path):
    root=tmp_path/'studio';root.mkdir()
    catalog=[]
    for i in range(4):
        audio=root/f'catalog/{i}.wav';tone(audio,300+i*100)
        evidence=root/f'catalog/{i}.json';atomic_write_json(evidence,{'actual_audio':str(audio),'response':'Instrumental; 4-second test sample.'})
        catalog.append({'song_id':str(i),'title':f'Track{i}','artist':'Artist','local_audio':str(audio),
            'license_id':'CC-BY-3.0','source_url':'https://example.com/track/'+str(i),
            'audio_audit_report':str(evidence),'audio_audit_source_windows':[{'start':0,'end':4}],
            'usage_policy':{'requires_visible_attribution':True},'duration':4})
    path=root/'catalog.json';atomic_write_json(path,catalog)
    atomic_write_json(root/'configs/interactive.json',{'services':{'music_selection_config':'.runtime/music-selection/runtime.json'}})
    atomic_write_json(root/'.runtime/music-selection/runtime.json',{'catalog':str(path),'catalogue_scope':'small test pool; no popularity evidence'})
    return root,catalog


def raw_result():
    return {'success':True,'response':'Only local candidates; full dramatic fit is not established.',
        'recommendations':{'success':True,'data':[{'song':{'music_id':str(i%4)},'reason':'candidate'}for i in range(11)]}}


def test_music_exhausted_http_candidates_do_not_reenter_metered_template(tmp_path,monkeypatch):
    import novel_manga.llm.catalog as catalog
    rows=copy.deepcopy(catalog.catalogue())
    for name in rows['candidates']['json']:
        if 'http-chat' in rows['resources'][name]['qualified']:rows['resources'][name]['available']=False
    monkeypatch.setattr(catalog,'catalogue',lambda:rows)
    monkeypatch.setattr(music.subprocess,'run',lambda *a,**kw:pytest.fail('no music model call is allowed'))
    with pytest.raises(ValueError,match='No qualified HTTP chat'):
        music.recommend(tmp_path,{},'quiet music',tmp_path)


def test_real_count_and_unreviewed_status_reach_search_and_cache(studio,tmp_path):
    root,_=studio;seen=[]
    def backend(*args):seen.append(True);return raw_result()
    result=music.search(root,{'query':'quiet investigation','limit':3},tmp_path/'searches',backend=backend)
    assert len(result['candidates'])==3 and result['returned_count']==11
    assert all(c['review_status']=='unreviewed'for c in result['candidates'])
    assert result['requires_explicit_adoption']is True and 'dramatic fit'in result['backend_message']
    assert music.search(root,{'query':'quiet investigation','limit':3},tmp_path/'searches',backend=backend)==result
    assert len(seen)==1
    assert len(read_json(tmp_path/'searches/search-001/raw.json')['recommendations']['data'])==11


def test_original_ids_can_be_retrieved_exactly_without_a_fuzzy_model_call(studio,tmp_path):
    root,_=studio
    result=music.search(root,{'query':'known source tracks','track_ids':['3','1'],'limit':3},tmp_path/'exact',
        backend=lambda *a:pytest.fail('exact id lookup does not need a model or fuzzy ranking'))
    assert [c['id']for c in result['candidates']]==['3','1']
    assert result['retrieval_method']=='catalogue_id_lookup'
    assert all(c['review_status']=='unreviewed'for c in result['candidates'])


def test_failed_and_unknown_catalogue_results_never_become_accepted_music(studio,tmp_path):
    root,_=studio
    with pytest.raises(ValueError,match='failed'):
        music.search(root,{'query':'q'},tmp_path/'failed',backend=lambda *a:{'success':False})
    response=raw_result();response['recommendations']['data']=[{'song':{'music_id':'outside-catalogue'}}]
    result=music.search(root,{'query':'q'},tmp_path/'empty',backend=lambda *a:response)
    assert result['candidates']==[] and result['unavailable_ids']==['outside-catalogue']


def test_cli_relative_output_becomes_an_absolute_backend_workspace(studio,monkeypatch):
    root,_=studio;monkeypatch.chdir(root)
    def backend(root,config,query,directory):
        assert directory.is_absolute() # subprocess changes cwd to this directory
        return raw_result()
    assert music.search(root,{'query':'q'},Path('relative-output'),backend=backend)['ok']


@pytest.mark.parametrize('already_running',[True,False])
def test_local_index_restores_its_own_start_and_keeps_existing_service(tmp_path,monkeypatch,already_running):
    calls=[];checks=iter([already_running,True])
    monkeypatch.setattr(music,'index_running',lambda config:next(checks))
    monkeypatch.setattr(music.subprocess,'run',lambda command,**kw:calls.append(command[-1])or NS(returncode=0))
    config={'neo4j_uri':'bolt://127.0.0.1:17687','neo4j_home':str(tmp_path/'neo4j'),'java_home':'/existing/jre'}
    with pytest.raises(RuntimeError,match='probe'):
        with music.local_index(config,tmp_path):raise RuntimeError('probe failed after start')
    assert calls==([]if already_running else['start','stop'])


@pytest.fixture
def episode(studio,tmp_path,monkeypatch):
    root,catalog=studio;book=tmp_path/'book';directory=book/'book_1';directory.mkdir(parents=True)
    run=tmp_path/'run';work=run/'sandbox';(work/'output').mkdir(parents=True)
    source='她停在原地，核对刚找到的记录。'
    (run/'source.txt').write_text(source)
    atomic_write_json(directory/'chapter_script.json',{'shots':[{'sfx':'water','audio_plan':{'music_cue':'quiet investigation'}}]})
    atomic_write_json(directory/'thin_media_report.json',{'status':'clips_ready','assembly':None})
    ctx=NS(project_root=root,novel_dir=book,episode_dir=directory,run_dir=run,work_dir=work)
    def backend(*a):return raw_result()
    monkeypatch.setattr(music,'recommend',backend)
    checker=lambda *a:{'consistent':True,'problems':[],'limitations':['No full-film listening yet.']}
    operations=MusicOperations(ctx,NS(runner=lambda:NS(context=NS())),source_checker=checker)
    def call(payload):
        atomic_write_json(work/'output/music-selection.json',payload)
        return operations.prepare({})
    result=call({'action':'search','query':'quiet investigation','limit':3})
    return ctx,call,result,source


def selection(result,source):
    return {'action':'apply','search_id':result['search_id'],'candidate_id':result['candidates'][0]['id'],
        'source_quote':source,'reason':'A quiet local cue for this evidence check.',
        'review':{'status':'accepted','reason':'Selected interval fits the quiet investigation; the supplied audio evidence covers it.',
                  'audio_evidence':result['candidates'][0]['audio_evidence']['report'],
                  'limitations':['Synthetic fixture; real music listening not established.']},
        'start_seconds':0,'seconds':4,'gain_db':-18}


@pytest.mark.parametrize('status',['unreviewed','uncertain','rejected'])
def test_only_explicitly_accepted_music_can_change_assembly(episode,status):
    ctx,call,result,source=episode;plan=selection(result,source);plan['review']['status']=status
    with pytest.raises(ValueError,match='accepted'):call(plan)
    assert not(ctx.episode_dir/'delivery_policy.json').exists()


def test_missing_evidence_bad_range_and_source_rejection_do_not_apply(episode):
    ctx,call,result,source=episode;plan=selection(result,source)
    plan['review']['audio_evidence']='invented.json'
    with pytest.raises(ValueError,match='evidence'):call(plan)
    plan=selection(result,source);plan['seconds']=9
    with pytest.raises(ValueError,match='range'):call(plan)
    plan=selection(result,source);plan['source_quote']='not the source'
    with pytest.raises(ValueError,match='source_quote'):call(plan)


def test_adoption_persists_before_assembly_without_generating_anything(episode):
    ctx,call,result,source=episode
    outcome=call(selection(result,source))
    assert outcome['ok']and outcome['assembly_changed']is False and outcome['video_generation']is False
    policy=read_json(ctx.episode_dir/'delivery_policy.json')
    assert policy['episode_music']['asset']['title']=='Track0'
    assert policy['episode_music']['asset']['license_id']=='CC-BY-3.0'
    assert policy['episode_music']['asset']['review']['status']=='accepted'
    assert not(ctx.episode_dir/'repair_history/history.json').exists()


def test_no_music_source_rejection_preserves_previous_policy(episode):
    ctx,call,result,source=episode
    atomic_write_json(ctx.episode_dir/'delivery_policy.json',{'existing':'retained'})
    checker=lambda *a:{'consistent':False,'problems':['Approved task requires no music.']}
    operations=MusicOperations(ctx,NS(),source_checker=checker)
    atomic_write_json(ctx.work_dir/'output/music-selection.json',selection(result,source))
    outcome=operations.prepare({})
    assert outcome['ok']is False
    assert read_json(ctx.episode_dir/'delivery_policy.json')=={'existing':'retained'}


def test_music_source_approval_is_rechecked_when_the_sound_design_changes(episode):
    ctx,call,result,source=episode;calls=[]
    def checker(*args):
        calls.append(True)
        return {'consistent':len(calls)==1,'problems':[]if len(calls)==1 else['New source says no music.']}
    tool=MusicOperations(ctx,NS(),source_checker=checker)
    atomic_write_json(ctx.work_dir/'output/music-selection.json',selection(result,source))
    assert tool.prepare({})['ok']and tool.prepare({})['ok']and len(calls)==1
    script=read_json(ctx.episode_dir/'chapter_script.json');script['shots'][0]['sfx']='No added music during the listening beat.'
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    assert tool.prepare({})['ok']is False and len(calls)==2


@pytest.mark.parametrize('timeline', [False, True])
def test_music_source_policy_change_rechecks_old_rejection_without_waiving_it(episode, monkeypatch, timeline):
    from novel_manga.application.agents.episode import music as agent_music
    ctx, _, result, source = episode
    plan = selection(result, source)
    if timeline:
        atomic_write_json(ctx.episode_dir/'chapter_script.json', {
            'shots': [{'index': 1, 'duration_seconds': 2}]})
        atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [
            {'clip_id': 'a', 'shot_indexes': [1], 'request_seconds': 2}]})
        plan = {'action': 'apply_timeline', 'source_quote': source, 'reason': 'approved cue',
                'cues': [{**plan, 'id': 'cue1', 'source_start_seconds': 0, 'source_seconds': 2,
                          'start': {'shot': 1, 'edge': 'start'},
                          'end': {'shot': 1, 'edge': 'end'}}]}
    calls = []
    def checker(*args):
        calls.append(True)
        return {'consistent': len(calls) > 1,
                'problems': [] if len(calls) > 1 else ['old scope interpretation']}
    tool = MusicOperations(ctx, NS(), source_checker=checker)
    atomic_write_json(ctx.work_dir/'output/music-selection.json', plan)
    current = agent_music.SOURCE_REVIEW_POLICY
    monkeypatch.setattr(agent_music, 'SOURCE_REVIEW_POLICY', 'older-policy')
    assert tool.prepare({})['ok'] is False
    assert tool.prepare({})['ok'] is False and len(calls) == 1
    monkeypatch.setattr(agent_music, 'SOURCE_REVIEW_POLICY', current)
    assert tool.prepare({})['ok'] is True and len(calls) == 2
    records = read_json(ctx.run_dir/'music-decisions.json')
    assert len(records) == 2
    assert records[0]['source_review']['consistent'] is False
    assert records[1]['inputs']['source_review_policy'] == current
    assert not (ctx.episode_dir/'repair_history/history.json').exists()


def test_declining_a_candidate_is_recorded_without_adding_music(episode):
    ctx,call,result,source=episode
    plan=selection(result,source);plan['action']='decline';plan['review'].update(status='uncertain',reason='The sampled evidence does not establish the required arc.')
    assert call(plan)['music_declined']is True
    assert not(ctx.episode_dir/'delivery_policy.json').exists()
    assert read_json(ctx.run_dir/'music-assessments.json')[0]['review']['status']=='uncertain'
    plan['review']['reason']='Rewording the same uncertainty.'
    assert call(plan)['assessment_added']is False
    assert len(read_json(ctx.run_dir/'music-assessments.json'))==1


def test_selected_but_unmixed_music_cannot_be_accepted(episode):
    ctx,call,result,source=episode;call(selection(result,source))
    assert music.episode_music_errors(ctx.episode_dir,{'assembly':{'final_video':'old.mp4'}})


def test_agent_can_adopt_story_cues_and_preserves_source_gaps_and_usage_history(episode):
    ctx,call,result,source=episode
    atomic_write_json(ctx.episode_dir/'chapter_script.json',{'shots':[{'index':1,'duration_seconds':2},{'index':2,'duration_seconds':2}]})
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'a','shot_indexes':[1],'request_seconds':2},
        {'clip_id':'b','shot_indexes':[2],'request_seconds':2}]})
    selected=selection(result,source)
    cue={**selected,'id':'cue1','source_start_seconds':0,'source_seconds':4,'loop':False,
        'start':{'shot':1,'edge':'start'},'end':{'shot':2,'edge':'end'},'fade_in_seconds':.2,'fade_out_seconds':.2}
    plan={'action':'apply_timeline','source_quote':source,'reason':'quiet cue with a listening gap',
        'cues':[cue],'silences':[{'start':{'shot':2,'edge':'start'},'end':{'shot':2,'edge':'end'},'source_quote':source,'reason':'hear the source'}]}
    assert call(plan)['music_adopted']is True
    policy=read_json(ctx.episode_dir/'delivery_policy.json')['episode_music']
    assert policy['cues'][0]['asset']['id']==result['candidates'][0]['id']
    assert policy['silences']==plan['silences']and policy['cues'][0]['start']==cue['start']
    assert len(read_json(ctx.project_root/'.runtime/music-selection/usages.json'))==1
    assert call(plan)['ok']is True and len(read_json(ctx.project_root/'.runtime/music-selection/usages.json'))==1


def test_music_and_room_tone_are_idempotent_and_keep_video_and_native_voice(episode,monkeypatch):
    from novel_manga.application.repair import ambience
    ctx,call,result,source=episode;call(selection(result,source))
    foreground=ctx.episode_dir/'foreground.wav';tone(foreground,1000)
    video=ctx.episode_dir/'native.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=25:d=4',
        '-i',str(foreground),'-c:v','libx264','-c:a','aac','-shortest',str(video)],check=True)
    room=ctx.episode_dir/'room.wav';tone(room,200)
    policy=read_json(ctx.episode_dir/'delivery_policy.json');policy['episode_ambience']={
        'asset':{'id':'room','path':str(room),'review':{'speech_free':True,'method':'test waveform'}},'gain_db':-24}
    atomic_write_json(ctx.episode_dir/'delivery_policy.json',policy)
    report={'status':'assembled','assembly':{'final_video':str(video),'timeline':[]}}
    first=ambience.apply_assembly(ctx.episode_dir,report)
    output=Path(first['assembly']['final_video']);stamp=output.stat().st_mtime_ns
    assert 'episode_music_mix'in first['assembly']and'episode_ambience_mix'in first['assembly']
    again=ambience.apply_assembly(ctx.episode_dir,copy.deepcopy(first))
    assert again['assembly']['final_video']==str(output)and output.stat().st_mtime_ns==stamp
    assert music.episode_music_errors(ctx.episode_dir,again)==[]
    def elementary(p):return subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:v:0','-c','copy','-f','h264','-'])
    assert elementary(output)==elementary(video)
    def strength(p,hz):
        data=subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'])
        samples=struct.unpack('<'+'f'*(len(data)//4),data)
        return abs(sum(x*complex(math.cos(2*math.pi*hz*i/16000),math.sin(2*math.pi*hz*i/16000))for i,x in enumerate(samples)))/len(samples)
    assert strength(output,1000)>.8*strength(video,1000)
    assert strength(output,300)>3*strength(video,300)
    policy['episode_music']['gain_db']=-12;atomic_write_json(ctx.episode_dir/'delivery_policy.json',policy)
    revised=ambience.apply_assembly(ctx.episode_dir,again)
    assert revised['assembly']['final_video']!=str(output)
    assert revised['assembly']['episode_music_mix']['source']==first['assembly']['episode_music_mix']['source']
    assert video.exists()and output.exists()
