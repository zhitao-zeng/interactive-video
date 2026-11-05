"""No paid calls: native Pi with fake SSE, real SDK with MockTransport, real ffmpeg."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from PIL import Image
import pytest

HERE = Path(__file__).resolve().parent
SDK = Path(os.environ.get('PI_STARTER_SDK_ROOT', HERE.parents[1]))
DEPS = os.environ.get('PI_STARTER_NODE_MODULES')
spec = importlib.util.spec_from_file_location('starter', HERE / 'starter.py')
starter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(starter)


@pytest.fixture
def workspace(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    return starter.initialize(tmp_path / 'work', SDK, DEPS)


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_native_agent_edits_and_runs_working_copy_and_resumes(workspace, tmp_path):
    skill = workspace / 'skills/episode-production'
    official = Path('references/official/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md')
    assert (skill / official).read_bytes() == (SDK / 'skills/episode-production' / official).read_bytes()
    assert len(json.loads((skill / 'creation-methods.json').read_text())) == 6
    dummy='test-credential-value-123456789'
    tail="import os; print(os.getenv('TEST_API_KEY',''))\n"
    (workspace / 'tools/check.py').write_text("print('before')\n"+tail)
    preload = tmp_path / 'fake-fetch.mjs'
    preload.write_text('''import fs from 'node:fs';
let calls=0;
globalThis.fetch=async(url,options={})=>{
 if(!String(url).startsWith('http://127.0.0.1:1/'))throw new Error('Offline test forbids network');
 const body=JSON.parse(options.body);calls++;
 fs.writeFileSync(process.env.PI_RUN_DIR+'/last-request.json',JSON.stringify(body));
 const resuming=process.env.TEST_RESUME==='1';
 const name=calls===1?'edit':'bash';
 const args=calls===1?{path:'tools/check.py',oldText:"'before'",newText:"'after'"}
   :{command:process.env.TEST_PYTHON+' tools/check.py'};
 const done=resuming||calls>2;
 const delta=done?{content:'offline done'}:{tool_calls:[{index:0,id:'call_'+calls,type:'function',function:{name,arguments:JSON.stringify(args)}}]};
 const common={id:'offline',object:'chat.completion.chunk',model:body.model,created:1};
 const chunks=[{...common,choices:[{index:0,delta:{role:'assistant',...delta},finish_reason:null}]},
 {...common,choices:[{index:0,delta:{},finish_reason:done?'stop':'tool_calls'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}];
 return new Response(chunks.map(x=>'data: '+JSON.stringify(x)+'\\n\\n').join('')+'data: [DONE]\\n\\n',{headers:{'content-type':'text/event-stream'}});
};''')
    env = dict(os.environ, PI_RUN_DIR=str(workspace), PI_PROBE_MODEL='offline-model',
               PI_PROBE_BASE='http://127.0.0.1:1/v1', PI_PROBE_API_KEY='offline', TEST_PYTHON=sys.executable,
               BUN_OPTIONS='--preload ' + str(preload), TEST_API_KEY=dummy)
    cmd = [sys.executable, str(workspace / 'starter.py'), 'run', str(workspace)]
    first = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=45)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (workspace / 'tools/check.py').read_text() == "print('after')\n"+tail
    body = json.loads((workspace / 'last-request.json').read_text())
    assert {'read', 'write', 'edit', 'bash'} <= {tool['function']['name'] for tool in body['tools']}
    assert 'episode-production' in str(body['messages'])
    assert any('after' in str(m.get('content')) and m['role'] == 'tool' for m in body['messages'])
    assert body['max_tokens'] == 128000
    assert dummy not in json.dumps(body)
    assert '[REDACTED_CREDENTIAL]' in json.dumps(body)
    assert json.loads((workspace/'run-budget.json').read_text())['provider_requests']==3
    saved = (workspace / 'session-path.txt').read_text()
    second = subprocess.run(cmd + ['--resume'], cwd=workspace, env=dict(env, TEST_RESUME='1'),
                            capture_output=True, text=True, timeout=45)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (workspace / 'session-path.txt').read_text() == saved
    assert (workspace / 'tools/check.py').read_text() == "print('after')\n"+tail
    assert dummy not in Path(saved.strip()).read_text()
    assert json.loads((workspace/'run-budget.json').read_text())['provider_requests']==4
    resumed = json.loads((workspace / 'last-request.json').read_text())
    assert any('after' in str(m.get('content')) and m['role'] == 'tool' for m in resumed['messages'])
    with pytest.raises(FileExistsError):
        starter.initialize(workspace, SDK, DEPS)
    limited=starter.initialize(tmp_path/'limited',SDK,DEPS)
    (limited/'tools/check.py').write_text("print('before')\n")
    cfg=json.loads((limited/'local.json').read_text());cfg['max_model_requests']=2
    (limited/'local.json').write_text(json.dumps(cfg))
    limited_run=subprocess.run([sys.executable,str(limited/'starter.py'),'run',str(limited)],
        cwd=limited,env=dict(env,PI_RUN_DIR=str(limited)),capture_output=True,text=True,timeout=45)
    assert limited_run.returncode==1
    state=json.loads((limited/'run-budget.json').read_text())
    assert state['provider_requests']==2 and 'RUN_BUDGET_EXHAUSTED' in state['error']
    # Deadline is original across resume; an expired run does not dispatch another provider request.
    state['deadline_at']=1;(limited/'run-budget.json').write_text(json.dumps(state))
    expired=subprocess.run([sys.executable,str(limited/'starter.py'),'run',str(limited),'--resume'],
        cwd=limited,env=dict(env,PI_RUN_DIR=str(limited)),capture_output=True,text=True,timeout=45)
    assert expired.returncode==1
    assert json.loads((limited/'run-budget.json').read_text())['provider_requests']==2


@pytest.fixture
def media(workspace, monkeypatch):
    monkeypatch.setenv('PI_RUN_DIR', str(workspace))
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY', 'offline')
    monkeypatch.setenv('PHANROUTER_BASE_URL', 'https://mock.invalid')
    monkeypatch.setenv('PHANROUTER_ASSET_GROUP_ID', 'offline-group')
    monkeypatch.setenv('PHANROUTER_ASSET_PUBLIC_BASE', 'https://mock.invalid/images')
    monkeypatch.setenv('NOVEL_ASSET_PUBLISH_DIR', str(workspace / 'published'))
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR', str(workspace / 'quota'))
    for name, color in [('identity.png', 'red'), ('space.png', 'blue')]:
        Image.new('RGB', (32, 48), color).save(workspace / 'input' / name)
    return module_at('media', workspace / 'tools/media.py')


def mock_provider(media, monkeypatch, handler):
    original = media.PhanRouterMediaProvider
    def create(*args, **kwargs):
        provider = original(*args, **kwargs)
        provider.client.close()
        provider.client = httpx.Client(transport=httpx.MockTransport(handler))
        return provider
    monkeypatch.setattr(media, 'PhanRouterMediaProvider', create)


def test_image_real_sdk_attaches_both_files_and_retains_task(media, monkeypatch, workspace):
    posted = []
    def handler(req):
        if req.method == 'POST':
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={'task_id': 'image-1'})
        if '/generations/' in req.url.path:
            return httpx.Response(200, json={'status': 'succeeded', 'url': 'https://mock.invalid/result.png'})
        return httpx.Response(200, content=(workspace / 'input/identity.png').read_bytes())
    mock_provider(media, monkeypatch, handler)
    result = media.execute({'action': 'image', 'prompt': 'Picture 1 identity, Picture 2 space',
        'references': ['input/identity.png', 'input/space.png'], 'output': 'media/opening.png'})
    assert len(posted) == 1 and len(posted[0]['base64Files']) == 2
    wire = json.loads((workspace / 'media/opening.png.wire/request.json').read_text())
    assert wire['first_frame_bound'] is False
    for row, expected in zip(wire['attachments'], ['red', 'blue']):
        with Image.open(row['uploaded_file']) as image:
            assert image.getpixel((0, 0)) == Image.new('RGB', (1, 1), expected).getpixel((0, 0))
    assert json.loads((workspace / 'media/opening.png.wire/response.json').read_text())['task_id'] == 'image-1'
    assert Path(result['path']).is_file()
    with pytest.raises(ValueError, match='Unbound'):
        media.execute({'action': 'image', 'prompt': 'Picture 3', 'references': ['input/identity.png'], 'output': 'media/bad.png'})
    assert len(posted) == 1


def test_terminal_image_failure_cannot_implicitly_repost(media, monkeypatch, workspace):
    posted = []
    def handler(req):
        if req.method == 'POST':
            posted.append(req)
            return httpx.Response(200, json={'task_id': 'failed-image'})
        return httpx.Response(200, json={'status': 'failed'})
    mock_provider(media, monkeypatch, handler)
    request = {'action': 'image', 'prompt': 'opening', 'references': ['input/identity.png'], 'output': 'media/failed.png'}
    with pytest.raises(RuntimeError, match='generation failed'):
        media.execute(request)
    with pytest.raises(RuntimeError, match='Already submitted'):
        media.execute(request)
    assert len(posted) == 1
    assert (workspace / 'media/failed.png.wire/response.json').is_file()


def test_sd_library_and_actual_first_frame_role(media, monkeypatch, workspace):
    posted = []
    def handler(req):
        if req.url.path.endswith('/CreateAsset'):
            return httpx.Response(200, json={'Id': 'asset-1'})
        if req.url.path.endswith('/GetAsset'):
            return httpx.Response(200, json={'Status': 'Active'})
        if req.method == 'HEAD':
            return httpx.Response(200)
        if req.method == 'POST':
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={'task_id': 'video-1'})
        if '/tasks/' in req.url.path:
            return httpx.Response(200, json={'status': 'succeeded', 'url': 'https://mock.invalid/clip.mp4'})
        return httpx.Response(200, content=b'fake-video-download')
    mock_provider(media, monkeypatch, handler)
    uploaded = media.execute({'action': 'upload', 'path': 'input/identity.png'})
    assert uploaded['status'] == 'Active'
    media.execute({'action': 'video', 'prompt': 'Continue from opening', 'first_frame': 'input/identity.png',
                   'references': ['input/identity.png'], 'seconds': 6, 'output': 'media/framed.mp4'})
    parts = posted[0]['content']
    assert [part['role'] for part in parts if part['type'] == 'image_url'] == ['first_frame']
    assert parts[-1]['image_url']['url'] == 'asset://asset-1'
    wire = json.loads((workspace / 'media/framed.mp4.wire/request.json').read_text())
    assert wire['first_frame_bound'] is True and wire['attachments'][0]['asset_uri'] == 'asset://asset-1'


def test_unknown_submission_does_not_repost(media, monkeypatch, workspace):
    posted = []
    def handler(req):
        posted.append(req)
        raise httpx.ReadTimeout('offline timeout after submission', request=req)
    mock_provider(media, monkeypatch, handler)
    request = {'action': 'image', 'prompt': 'opening', 'references': ['input/identity.png'], 'output': 'media/unknown.png'}
    with pytest.raises(Exception):
        media.execute(request)
    with pytest.raises(Exception):
        media.execute(request)
    assert len(posted) == 1
    assert (workspace / 'media/unknown.png.wire/request.json').is_file()


def test_h3_real_sdk_binds_opening_and_all_references(media, monkeypatch, workspace):
    import wave
    with wave.open(str(workspace/'voice.wav'),'wb')as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0'*32000)
    from novel_manga.providers import local_h3
    original = local_h3.LocalH3MediaProvider
    posted = []
    def handler(req):
        if req.method == 'POST':
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={'id': 'h3-1'})
        if req.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake-video-download')
        return httpx.Response(200, json={'status': 'completed'})
    def create(*args, **kwargs):
        provider = original(*args, **kwargs)
        provider.h3_client.close()
        provider.h3_client = httpx.Client(transport=httpx.MockTransport(handler))
        return provider
    monkeypatch.setattr(local_h3, 'LocalH3MediaProvider', create)
    media.execute({'action': 'h3', 'endpoint': 'http://mock.invalid', 'prompt': 'Picture 1 start, Picture 2 room',
        'references': ['input/identity.png', 'input/space.png'], 'first_frame': 'input/identity.png',
        'audios':['voice.wav'],'seconds': 6, 'output': 'media/h3.mp4'})
    assert posted[0]['model'] == 'MiniMax-H3'
    conditions = posted[0]['conditions']
    assert [x['role'] for x in conditions if x['type']=='image'] == ['reference', 'reference', 'keyframe']
    assert len([x for x in conditions if x['type']=='audio'])==1
    assert conditions[0]['uri'] == conditions[-1]['uri']
    assert conditions[-1]['frame_index'] == 0
    wire = json.loads((workspace / 'media/h3.mp4.wire/request.json').read_text())
    assert len(wire['attachments']) == 2 and wire['first_frame_bound'] is True
    assert len(wire['audio_attachments'])==1
    assert Path(wire['audio_attachments'][0]['uploaded_file']).read_bytes()==(workspace/'voice.wav').read_bytes()


def test_assembly_uses_current_cuts_not_cached_movie(media, workspace):
    for name, color in [('red', 'red'), ('blue', 'blue')]:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=64x96:d=0.4',
            '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '0.4', '-c:v', 'libx264', '-c:a', 'aac',
            str(workspace / f'{name}.mp4')], check=True)
    assemble = module_at('starter_assemble', workspace / 'tools/assemble.py')
    def spec(name):
        return {'clips': [{'path': f'{name}.mp4'}], 'width': 64, 'height': 96, 'fps': 25}
    first = assemble.assemble(spec('red'), 'output/first.mp4')
    with pytest.raises(ValueError, match='previous movie'):
        assemble.assemble(spec('blue'), 'output/first.mp4')
    second = assemble.assemble(spec('blue'), 'output/second.mp4')
    def pixel(movie):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', movie, '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return tuple(raw)
    assert pixel(first['movie'])[0] > pixel(first['movie'])[2] + 100
    assert pixel(second['movie'])[2] > pixel(second['movie'])[0] + 100


def test_assembly_mixes_only_explicitly_reviewed_music(media, workspace):
    for name, color in [('red', 'red'), ('blue', 'blue')]:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=64x96:d=0.5',
            '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '0.5', '-c:v', 'libx264', '-c:a', 'aac',
            '-pix_fmt', 'yuv420p', '-shortest', str(workspace / f'{name}.mp4')], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2',
                    '-ar', '48000', '-ac', '2', str(workspace / 'track.wav')], check=True)
    assemble = module_at('starter_assemble_music', workspace / 'tools/assemble.py')
    music = {'path': 'track.wav', 'track_id': 'test-1', 'title': 'Test tone', 'artist': 'test',
             'license_id': 'test-only', 'attribution': 'test', 'gain_db': -24, 'loop': True,
             'fade_in': 0.1, 'fade_out': 0.1,
             'review': {'status': 'accepted', 'reason': 'offline mix test'}}
    result = assemble.assemble({'clips': [{'path': 'red.mp4'}, {'path': 'blue.mp4'}],
                                'width': 64, 'height': 96, 'fps': 25, 'music': music},
                               'output/mixed.mp4')
    assert result['music'] is True
    assert (workspace / 'output/mixed.mp4.music.json').is_file()
    with pytest.raises(ValueError, match='review.status'):
        assemble.assemble({'clips': [{'path': 'red.mp4'}], 'music': {'path': 'track.wav'}},
                          'output/unreviewed.mp4')
    assert not (workspace / 'output/unreviewed.mp4').exists()


def test_previous_attempt_is_linked_and_indexed(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    previous = tmp_path / 'previous'
    (previous / 'output').mkdir(parents=True)
    (previous / 'media/shots').mkdir(parents=True)
    (previous / 'output/script.md').write_text('# old plan\n')
    (previous / 'media/shots/s01a.mp4').write_bytes(b'old-video')
    work = starter.initialize(tmp_path / 'resume-work', SDK, DEPS, previous=previous,
                              asset_cache=tmp_path / 'empty-cache')
    assert (work / 'input/previous').is_symlink()
    assert (work / 'input/previous/output/script.md').read_text() == '# old plan\n'
    index = (work / 'input/PREVIOUS-MEDIA.tsv').read_text()
    assert 'media/shots/s01a.mp4' in index and 'old-video' not in index
    note = (work / 'input/PREVIOUS.md').read_text()
    assert '同一集' in note and 'input/previous/output/script.md' in note


def test_asset_cache_seeds_only_missing_files(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    cache = tmp_path / 'cache'
    cache.mkdir()
    Image.new('RGB', (4, 5), 'red').save(cache / 'identity.png')
    (cache / 'identity.png.asset.json').write_text('{"asset_id": "cached"}\n')
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    Image.new('RGB', (4, 5), 'blue').save(inputs / 'identity.png')
    work = starter.initialize(tmp_path / 'asset-work', SDK, DEPS, inputs=inputs, asset_cache=cache)
    with Image.open(work / 'input/identity.png') as image:
        assert image.getpixel((0, 0))[2] > 200
    assert json.loads((work / 'input/identity.png.asset.json').read_text())['asset_id'] == 'cached'
    assert 'novel_asset_publish_dir' in json.loads((work / 'local.json').read_text())


@pytest.mark.parametrize('action,audio_key', [('video','reference_audios'), ('h3','audios')])
def test_reference_audio_reaches_sdk(media, monkeypatch, workspace, action, audio_key):
    import wave
    audio = workspace / 'voice.wav'
    with wave.open(str(audio), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\0' * 32000)
    calls = []
    class Client:
        def post(self, *args, **kwargs): raise AssertionError('offline test')
        def close(self): pass
    class Provider:
        def __init__(self,*args,**kwargs): self.client=Client(); self.h3_client=Client()
        def create_video(self,*args,**kwargs): calls.append(kwargs)
    monkeypatch.setattr(media,'PhanRouterMediaProvider',Provider)
    from novel_manga.providers import local_h3
    monkeypatch.setattr(local_h3,'LocalH3MediaProvider',Provider)
    media.execute({'action':action,'endpoint':'http://127.0.0.1:1','references':[],
        'prompt':'One speaker','seconds':5,'output':'media/voice.mp4',audio_key:['voice.wav']})
    assert calls[0]['reference_audios'] == (audio,)


def test_audio_unknown_submit_and_resume_reuse_job(media, monkeypatch, workspace):
    from urllib.error import URLError
    (workspace/'sample.mp4').write_bytes(b'fake source')
    config=json.loads((workspace/'local.json').read_text());config['audio_service_url']='http://127.0.0.1:1'
    (workspace/'local.json').write_text(json.dumps(config))
    calls={'post':0,'worker':0,'query':0};accepted={}
    def run(args, **kwargs):
        if args[0]=='ffmpeg':Path(args[-1]).write_bytes(b'fake wave')
        else:calls['worker']+=1;raise AssertionError('uncertain POST must not start a local worker')
    def urlopen(req, **kwargs):
        if not isinstance(req,str):
            calls['post']+=1;accepted.update(json.loads(req.data));raise TimeoutError('reply lost after accept')
        calls['query']+=1
        if calls['query']==1:raise URLError('temporary query outage')
        assert req.endswith('/job/'+accepted['job_id'])
        for row in accepted['jobs']:Path(row['output']).write_text(json.dumps({'response':'heard'}))
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self):return json.dumps({'status':'done','observations':[{'response':'heard'}]}).encode()
        return Response()
    monkeypatch.setattr(media.subprocess,'run',run)
    monkeypatch.setattr(media.urllib.request,'urlopen',urlopen)
    request={'action':'audio','path':'sample.mp4','windows':[{'start':0,'end':1}]}
    with pytest.raises((TimeoutError,URLError)):media.execute(request)
    assert calls['worker']==0
    result=media.execute(request)
    assert result['observations'][0]['response']=='heard'
    assert calls['post']==1 and calls['query']>=2
    assert media.execute(request)['observations'][0]['response']=='heard'


def test_frame_aligned_multicut_export(media, workspace):
    source=workspace/'source24.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=s=64x96:r=24:d=0.7',
        '-f','lavfi','-i','sine=frequency=500:sample_rate=48000','-t','0.7','-c:v','libx264','-c:a','aac',str(source)],check=True)
    assemble=module_at('aligned_assemble',workspace/'tools/assemble.py')
    result=assemble.assemble({'clips':[{'path':'source24.mp4','start':.031,'end':.491}for _ in range(9)],
        'width':64,'height':96,'fps':25},'output/aligned.mp4')
    cached=assemble.assemble({'clips':[{'path':'source24.mp4','start':.031,'end':.491}for _ in range(9)],
        'width':64,'height':96,'fps':25},'output/aligned-2.mp4')
    assert cached['cache_hits'] >= 9 and cached['cache_misses'] == 0
    meta=media.probe(Path(result['movie']));streams={s['codec_type']:s for s in meta['streams']}
    assert abs(float(streams['video']['duration'])-float(streams['audio']['duration']))<=.04
    timeline=json.loads((workspace/'output/aligned.mp4.timeline.json').read_text())
    assert len(timeline['clips'])==9
    assert abs(timeline['duration']-float(streams['video']['duration']))<.001


def test_audio_service_caller_id_is_idempotent(workspace):
    service=module_at('test_audio_service',workspace/'tools/audio_service.py')
    sensor=service.AudioService.__new__(service.AudioService);sensor.allow_root=workspace
    jobs=[{'audio':str(workspace/'a.wav'),'output':str(workspace/'a.json')}]
    first=sensor.submit(jobs,{},'caller-1');again=sensor.submit(jobs,{},'caller-1')
    assert first==again=='caller-1' and service.QUEUE.qsize()==1
    with pytest.raises(ValueError,match='different inputs'):sensor.submit(jobs,{'max_new_tokens':99},'caller-1')


def test_batch_waits_for_owned_processes_and_reports_failures(workspace, tmp_path):
    helper=module_at('test_batch',workspace/'tools/batch.py')
    worker=tmp_path/'worker.py';worker.write_text('import sys,time\ntime.sleep(0.05)\nsys.exit(3 if "bad" in sys.argv[1] else 0)\n')
    files=[]
    for name in ['a','bad','c']:
        p=tmp_path/(name+'.json');p.write_text('{}');files.append(p)
    result=helper.batch(files,2,tmp_path/'report.json',command=[sys.executable,str(worker)])
    assert len(result['results'])==3
    assert sorted(row['returncode']for row in result['results'])==[0,0,3]


def test_production_options_are_enforced(workspace):
    cfg = json.loads((workspace / 'local.json').read_text())
    cfg.update(media_lane='sd2.0-fast', first_frame_policy='none')
    (workspace / 'local.json').write_text(json.dumps(cfg))
    media = module_at('media_options_sd', workspace / 'tools/media.py')
    with pytest.raises(ValueError, match='Pinned video lane is sd2.0-fast'):
        media.execute({'action': 'h3', 'endpoint': 'pool', 'prompt': 'x', 'references': [],
                       'seconds': 5, 'output': 'media/a.mp4'})
    with pytest.raises(ValueError, match='first_frame_policy=none'):
        media.execute({'action': 'video', 'prompt': 'x', 'references': [], 'seconds': 5,
                       'output': 'media/b.mp4', 'first_frame': 'input/identity.png'})
    cfg.update(media_lane='sd2.0-mini', first_frame_policy='selective')
    (workspace / 'local.json').write_text(json.dumps(cfg))
    mini = module_at('media_options_mini', workspace / 'tools/media.py')
    assert mini.settings().video_model == 'sd2.0-mini'
    with pytest.raises(ValueError, match='sd2.0-mini'):
        mini.execute({'action': 'h3', 'endpoint': 'pool', 'prompt': 'x', 'references': [],
                      'seconds': 5, 'output': 'media/mini-reject.mp4'})
    cfg['first_frame_policy'] = 'required'
    (workspace / 'local.json').write_text(json.dumps(cfg))
    media = module_at('media_options_required', workspace / 'tools/media.py')
    with pytest.raises(ValueError, match='first_frame_policy=required'):
        media.execute({'action': 'video', 'prompt': 'x', 'references': [], 'seconds': 5,
                       'output': 'media/c.mp4'})


def test_init_records_production_options(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'options-work', SDK, DEPS, lane='MiniMax-H3',
                              first_frame='required', interaction_page='required',
                              asset_cache=tmp_path / 'no-cache')
    cfg = json.loads((work / 'local.json').read_text())
    assert cfg['media_lane'] == 'MiniMax-H3'
    assert cfg['first_frame_policy'] == 'required'
    assert cfg['interaction_page'] == 'required'
    text = (work / 'PRODUCTION.md').read_text()
    assert 'MiniMax-H3' in text and 'interaction_page' in text


@pytest.mark.parametrize('viewpoint', [None, 'auto', 'first-person', 'mixed'])
def test_viewpoint_cli_reaches_initial_native_request_and_survives_resume(tmp_path, viewpoint):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = tmp_path / 'viewpoint-work'
    initialize = [sys.executable, str(HERE / 'starter.py'), 'init', str(work),
                  '--sdk-root', str(SDK), '--dependencies', DEPS]
    if viewpoint is not None:
        initialize += ['--viewpoint', viewpoint]
    result = subprocess.run(initialize, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    selected = viewpoint or 'auto'
    assert json.loads((work / 'local.json').read_text())['viewpoint'] == selected
    task = '本轮人类 TASK：稍后明确的拍法要求优先；不要调用媒体。'
    (work / 'TASK.md').write_text(task)
    production = (work / 'PRODUCTION.md').read_text()
    preload = tmp_path / 'capture-fetch.mjs'
    preload.write_text('''import fs from 'node:fs';
globalThis.fetch=async(url,options={})=>{
 if(!String(url).startsWith('http://127.0.0.1:1/'))throw new Error('Offline test forbids network');
 const body=JSON.parse(options.body);
 fs.writeFileSync(process.env.TEST_CAPTURE,JSON.stringify(body));
 const common={id:'offline',object:'chat.completion.chunk',model:body.model,created:1};
 const chunks=[{...common,choices:[{index:0,delta:{role:'assistant',content:'offline done'},finish_reason:null}]},
 {...common,choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}];
 return new Response(chunks.map(x=>'data: '+JSON.stringify(x)+'\\n\\n').join('')+'data: [DONE]\\n\\n',{headers:{'content-type':'text/event-stream'}});
};''')
    capture = tmp_path / 'initial-request.json'
    env = dict(os.environ, PI_PROBE_MODEL='offline-model', PI_PROBE_BASE='http://127.0.0.1:1/v1',
               PI_PROBE_API_KEY='offline', BUN_OPTIONS='--preload ' + str(preload),
               TEST_CAPTURE=str(capture))
    command = [sys.executable, str(work / 'starter.py'), 'run', str(work)]
    result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    body = json.loads(capture.read_text())
    user_message = next(message for message in body['messages'] if message['role'] == 'user')
    content = user_message['content']
    text = content if isinstance(content, str) else '\n'.join(block.get('text', '') for block in content)
    assert production in text and task in text
    assert text.index(production) < text.index(task)
    assert 'TASK' in text and '后续明确要求' in text
    expected = {
        'first-person': ('全程第一视角', '同一主角眼位', '其他角色可以露脸', '不要求一镜到底'),
        'mixed': ('混合视角', '主观与客观', '不规定比例'),
        'auto': ('导演自定', '不额外限定视角'),
    }
    assert all(instruction in text for instruction in expected[selected])
    if selected == 'first-person':
        # Resume keeps the original instruction and budget, rather than hot-loading file edits.
        original_budget = json.loads((work / 'run-budget.json').read_text())
        (work / 'PRODUCTION.md').write_text('REPLACEMENT_PRODUCTION_MUST_NOT_BE_HOT_LOADED')
        (work / 'TASK.md').write_text('REPLACEMENT_TASK_MUST_NOT_BE_HOT_LOADED')
        resumed_capture = tmp_path / 'resumed-request.json'
        result = subprocess.run(command + ['--resume'], cwd=work,
                                env=dict(env, TEST_CAPTURE=str(resumed_capture)),
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        resumed = resumed_capture.read_text()
        assert '全程第一视角' in resumed and '本轮人类 TASK' in resumed
        assert 'MUST_NOT_BE_HOT_LOADED' not in resumed
        budget = json.loads((work / 'run-budget.json').read_text())
        assert budget['started_at'] == original_budget['started_at']
        assert budget['deadline_at'] == original_budget['deadline_at']
        assert budget['provider_requests'] == original_budget['provider_requests'] + 1


def test_interaction_page_is_exactly_three_seconds(workspace):
    film = workspace / 'film.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'color=c=0x223322:s=1080x1920:d=1:r=25', '-f', 'lavfi',
                    '-i', 'anullsrc=r=48000:cl=stereo', '-t', '1', '-c:v', 'libx264',
                    '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest', str(film)], check=True)
    tool = module_at('interaction_tool', workspace / 'tools/interaction.py')
    result = tool.build({'film': str(film), 'output': str(workspace / 'film-with-page.mp4'),
        'title': '林野探秘档案', 'question': '下一站，你想和我去哪里探险？',
        'options': [{'id': 'A', 'label': 'A，空椅剧场', 'reason': '规则一。'},
                    {'id': 'B', 'label': 'B，风筝浮城', 'reason': '规则二。'},
                    {'id': 'C', 'label': 'C，预言车站', 'reason': '规则三。'}],
        'footer': '评论区留下你的选择', 'seconds': 3})
    assert result['interaction']['seconds'] == 3
    assert result['interaction']['start'] == 1.0
    assert (workspace / 'film-with-page.mp4.interaction.json').is_file()
