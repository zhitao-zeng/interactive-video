"""Reuse the isolated SoulTuner runtime; return local candidates, never an adoption."""
from pathlib import Path
import json
import os
import subprocess
import socket
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from novel_manga.util import atomic_write_json,read_json


def configuration(root):
    root=Path(root)
    filename=read_json(root/'configs/interactive.json',{}).get('services',{}).get(
        'music_selection_config','.runtime/music-selection/runtime.json')
    path=root/filename
    if not path.is_file():raise ValueError('SoulTuner runtime is not configured; see docs/music-selection.md')
    config=read_json(path,{})
    if not Path(config.get('catalog','')).is_file():raise ValueError('SoulTuner local catalogue is missing')
    return config


def index_running(config):
    address=urlsplit(config['neo4j_uri'])
    try:
        with socket.create_connection((address.hostname,address.port or 7687),timeout=1):return True
    except OSError:return False


@contextmanager
def local_index(config,directory):
    """Serialize use of the configured local test index; stop only a start we own."""
    home=config.get('neo4j_home')
    if not home:
        if not index_running(config):raise RuntimeError('Configured SoulTuner graph service is unavailable')
        yield;return
    import fcntl
    home=Path(home);lock=home/'run/studio-music-selection.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a')as handle,(Path(directory)/'index.log').open('a')as log:
        fcntl.flock(handle,fcntl.LOCK_EX)
        started=False;env={**os.environ,'JAVA_HOME':config['java_home']}
        try:
            if not index_running(config):
                if urlsplit(config['neo4j_uri']).hostname not in {'127.0.0.1','localhost'}:
                    raise RuntimeError('Only the configured loopback index can be started on demand')
                done=subprocess.run([str(home/'bin/neo4j'),'start'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=45)
                if done.returncode:raise RuntimeError('SoulTuner local graph could not start; retained index.log')
                started=True;deadline=time.monotonic()+20
                while not index_running(config):
                    if time.monotonic()>=deadline:raise RuntimeError('SoulTuner local graph did not become ready')
                    time.sleep(.2)
            yield
        finally:
            if started:
                stopped=subprocess.run([str(home/'bin/neo4j'),'stop'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=45)
                if stopped.returncode:raise RuntimeError('SoulTuner local index cleanup failed; retained index.log')


def recommend(root,config,query,directory):
    """Run upstream with its existing environment; secrets only cross via environment."""
    from novel_manga.application.configuration import environment
    from novel_manga.llm.config import JsonEndpoint,ENDPOINTS
    from novel_manga.llm.catalog import catalogue, choose
    selected=config.get('text_endpoint','phanthy-ds41')
    if selected == 'phanthy-ds41':
        selected=choose(catalogue()['candidates']['json'],'http-chat') or selected
    endpoint=JsonEndpoint.from_env({**environment(Path(root)),**ENDPOINTS[selected]})
    if not endpoint.endpoints[0].startswith(('http://','https://')):
        raise ValueError('SoulTuner requires an HTTP chat resource; saved-login Codex uses another protocol')
    from novel_manga.llm import quota
    quota.check(quota.for_model(endpoint.model, endpoint.endpoints[0], endpoint.key), endpoint.key)
    env={**os.environ,'ST_LLM_BASE_URL':endpoint.endpoints[0],'ST_LLM_MODEL':endpoint.model,'ST_LLM_KEY':endpoint.key}
    for name in ('NO_PROXY','no_proxy'):
        env[name]=','.join(filter(None,[env.get(name,''),'127.0.0.1','localhost']))
    request=directory/'backend-input.json';output=directory/'backend-output.json'
    atomic_write_json(request,{'query':query,'runtime':config,'output':str(output)})
    script=Path(__file__).resolve().parents[3]/'scripts/soultuner_recommend.py'
    try:
        with local_index(config,directory),(directory/'backend.log').open('w')as log:
            done=subprocess.run([config['python'],str(script),str(request)],env=env,cwd=directory,
                stdout=log,stderr=subprocess.STDOUT,timeout=200)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('SoulTuner timed out; original backend log retained, no automatic retry')from error
    if done.returncode or not output.is_file():
        raise RuntimeError('SoulTuner failed; inspect the retained backend log and service availability')
    return read_json(output,{})


def search(root,request,directory,*,backend=None):
    config=configuration(root);query=request.get('query');limit=request.get('limit',3)
    if not isinstance(query,str)or not query.strip():raise ValueError('music query requires text')
    if type(limit)is not int or not 1<=limit<=10:raise ValueError('music limit must be an integer from1 to10')
    info=Path(config['catalog']).stat()
    identity={'query':query,'limit':limit,'runtime':config,'catalogue_state':[info.st_size,info.st_mtime_ns]}
    track_ids=request.get('track_ids')
    if track_ids is not None:
        if not isinstance(track_ids,list)or not track_ids or any(not isinstance(i,str)or not i.strip()for i in track_ids):raise ValueError('track_ids requires original catalogue IDs')
        identity['track_ids']=track_ids
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    previous=next((p for p in sorted(directory.glob('search-*'))if read_json(p/'input.json',{})==identity),None)
    folder=previous or directory/f'search-{len(list(directory.glob("search-*")))+1:03d}'
    folder.mkdir(exist_ok=True);atomic_write_json(folder/'input.json',identity)
    if (folder/'result.json').is_file():return read_json(folder/'result.json')
    # A failed invocation stays failed; another query remains an explicit Agent action.
    if (folder/'failure.json').exists():raise ValueError('Previous SoulTuner search failed; retain evidence and revise the request')
    try:
        if track_ids is not None:
            raw={'success':True,'source':'catalogue_id_lookup','response':'按原编号取得候选；未进行剧情适配判断。',
                'recommendations':{'success':True,'data':[{'song':{'music_id':i},'reason':'explicit original catalogue ID'}for i in track_ids]}}
        else:raw=(backend or recommend)(root,config,query,folder)
        atomic_write_json(folder/'raw.json',raw)
        if raw.get('success')is not True or (raw.get('recommendations')or{}).get('success')is not True:
            raise ValueError('SoulTuner search failed; no candidates adopted')
        rows=(raw.get('recommendations')or{}).get('data')or[]
        if not isinstance(rows,list):raise ValueError('SoulTuner candidate response is malformed')
        catalog={r['song_id']:r for r in read_json(Path(config['catalog']),[])}
        selected=[];unavailable=[];seen=set()
        for row in rows:
            sid=(row.get('song')or{}).get('music_id')
            if sid in seen:continue
            seen.add(sid);entry=catalog.get(sid)
            if not entry or not Path(entry.get('local_audio','')).is_file():
                unavailable.append(sid);continue
            if len(selected)>=limit:continue
            selected.append({'id':sid,'title':entry['title'],'artist':entry.get('artist',''),
                'path':entry['local_audio'],'license_id':entry.get('license_id'),
                'license_url':entry.get('license_url'),'source_url':entry.get('source_url'),
                'usage_policy':entry.get('usage_policy',{}),'review_status':'unreviewed',
                **{key:entry[key]for key in ('attribution','credit_file','trial_history','fit_status','feedback','scene','subscene','advice','dialogue_advice','intensity')if key in entry},
                'retrieval_reason':row.get('reason',''),
                'audio_evidence':{'report':entry.get('audio_audit_report'),
                    'source_windows':entry.get('audio_audit_source_windows',[]),
                    'summary':entry.get('audio_audit'),'scope':entry.get('vocal_evidence_scope','sampled audio, not full-track acceptance')}})
        result={'ok':True,'search_id':folder.name,'query':query,'requested_count':limit,'returned_count':len(rows),
            'retrieval_method':raw.get('source','soultuner'),
            'candidates':selected,'unavailable_ids':unavailable,'requires_explicit_adoption':True,
            'backend_message':raw.get('response',''),'catalogue_scope':config.get('catalogue_scope','local catalogue; popularity not verified'),
            'raw_result':str(folder/'raw.json')}
        atomic_write_json(folder/'result.json',result);return result
    except Exception as error:
        atomic_write_json(folder/'failure.json',{'error_type':type(error).__name__,'message':'Search failed; no music adopted'})
        raise


def episode_music_errors(directory,report):
    """An accepted decision is not proof the current assembled track contains it."""
    from novel_manga.review.storage import take_identity
    policy=read_json(Path(directory)/'delivery_policy.json',{}).get('episode_music')
    if not policy:
        planned=read_json(Path(directory)/'chapter_script.json',{}).get('music_plan')or{}
        return ['Authored story music is not selected/mixed; use select_music before acceptance']if planned.get('cues')else[]
    approval=policy.get('source_approval')
    if approval:
        records=read_json(Path(approval['file']),[]);index=approval['record']
        if type(index)is not int or not 0<=index<len(records):return ['Music source approval evidence is missing; revalidate selected music']
        record=records[index];previous=record.get('inputs',{}).get('source')
        if not previous or source_inputs(directory,previous['source_file'])!=previous:
            return ['Source sound design changed after music approval; revalidate selection before acceptance']
    if 'cues'in policy:
        from novel_manga.application.music_timeline import resolve
        try:expected=resolve(directory,policy,(report.get('assembly')or{}).get('timeline',[]))
        except (ValueError,KeyError,OSError)as error:return [str(error)]
        assembly=report.get('assembly')or{};mixed=assembly.get('episode_music_mix')or{}
        if mixed.get('output')!=assembly.get('final_video')or(mixed.get('inputs')or{}).get('music_timeline')!=expected:
            return ['Current music cues/silences do not match the actual shot timeline; apply music again before acceptance']
        return []
    assembly=report.get('assembly')or{};mixed=assembly.get('episode_music_mix')or{}
    inputs=mixed.get('inputs')or{};asset=policy['asset']
    if (asset.get('review',{}).get('status')!='accepted'or mixed.get('output')!=assembly.get('final_video')or inputs.get('asset_id')!=asset['id']
            or inputs.get('gain_db')!=policy['gain_db']or inputs.get('asset')!=take_identity(Path(asset['path']))):
        return ['Current assembly does not contain the selected music asset/gain; apply selected music before accepting delivery']
    return []


def selected_assets(policy):
    assets=[c['asset']for c in policy['cues']]if'cues'in policy else[policy['asset']]
    return list({a['id']:a for a in assets}.values())


def source_inputs(directory,source_file):
    script=read_json(Path(directory)/'chapter_script.json',{})
    return {'source_file':str(source_file),'source':Path(source_file).read_text(),
        'shot_audio':[{k:s.get(k)for k in ('index','scene_id','location','scene_time','duration_seconds','purpose','event','sfx','audio_plan')}for s in script.get('shots',[])],
        'authored_music_plan':script.get('music_plan')}
