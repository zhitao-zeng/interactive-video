"""No paid calls: native Pi with fake SSE, real SDK with MockTransport, real ffmpeg."""
import fcntl
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import types

import httpx
from PIL import Image
import pytest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parents[1] / 'examples/pi-video-agent'
SDK = Path(os.environ.get('PI_STARTER_SDK_ROOT', HERE.parents[1]))
DEPS = os.environ.get('PI_STARTER_NODE_MODULES')
spec = importlib.util.spec_from_file_location('starter', HERE / 'starter.py')
starter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(starter)


@pytest.fixture
def workspace(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'work', SDK, DEPS)
    (work / 'TASK.md').write_text('离线测试任务：使用模拟模型和媒体，不授权真实生产。')
    return work


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
    (limited/'TASK.md').write_text('离线预算测试：使用模拟模型，不授权真实生产。')
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


def test_assembly_mixes_only_explicitly_reviewed_music(tmp_path, monkeypatch):
    workspace = tmp_path
    media = types.ModuleType('media')
    media.WORK = workspace
    media.file = lambda name: (workspace / name).resolve()
    media.probe = lambda path: json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], text=True))
    monkeypatch.setitem(sys.modules, 'media', media)
    for name, color in [('red', 'red'), ('blue', 'blue')]:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=64x96:d=0.5',
            '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000:duration=0.5',
            '-t', '0.5', '-c:v', 'libx264', '-c:a', 'aac',
            '-pix_fmt', 'yuv420p', '-shortest', str(workspace / f'{name}.mp4')], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2',
                    '-ar', '48000', '-ac', '2', str(workspace / 'track.wav')], check=True)
    assemble = module_at('starter_assemble_music', HERE / 'template/tools/assemble.py')
    music = {'path': 'track.wav', 'track_id': 'test-1', 'title': 'Test tone', 'artist': 'test',
             'license_id': 'test-only', 'attribution': 'test', 'gain_db': -24, 'loop': True,
             'fade_in': 0.1, 'fade_out': 0.1,
             'review': {'status': 'accepted', 'reason': 'offline mix test'}}
    result = assemble.assemble({'clips': [{'path': 'red.mp4'}, {'path': 'blue.mp4'}],
                                'width': 64, 'height': 96, 'fps': 25, 'music': music},
                               'output/mixed.mp4')
    assert result['music'] is True
    manifest = workspace / 'output/mixed.mp4.music.json'
    record = json.loads(manifest.read_text())
    assert record['music']['attribution'] == 'test'
    assert record['mix']['target_lufs'] == -29 and record['mix']['loudness_reference_lufs'] == -5
    output = workspace / result['movie']
    joined = workspace / 'output/mixed.mp4.parts/joined.mp4'
    def strength(path, hz):
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.15', '-i', str(path), '-t', '0.3',
            '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
        samples = struct.unpack('<' + 'f' * (len(data) // 4), data)
        return abs(sum(x * complex(math.cos(2 * math.pi * hz * i / 16000),
            math.sin(2 * math.pi * hz * i / 16000)) for i, x in enumerate(samples))) / len(samples)
    assert strength(output, 1000) == pytest.approx(strength(joined, 1000), rel=.05)
    assert strength(output, 440) > max(5 * strength(joined, 440), .005)
    with pytest.raises(ValueError, match='review.status'):
        assemble.assemble({'clips': [{'path': 'red.mp4'}], 'music': {'path': 'track.wav'}},
                          'output/unreviewed.mp4')
    assert not (workspace / 'output/unreviewed.mp4').exists()


def test_previous_attempt_is_privately_copied_and_indexed(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    previous = tmp_path / 'previous'
    (previous / 'output').mkdir(parents=True)
    (previous / 'media/shots').mkdir(parents=True)
    (previous / 'output/script.md').write_text('# old plan\n')
    (previous / 'media/shots/s01a.mp4').write_bytes(b'old-video')
    work = starter.initialize(tmp_path / 'resume-work', SDK, DEPS, previous=previous,
                              asset_cache=tmp_path / 'empty-cache')
    assert not (work / 'input/previous').is_symlink()
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


def test_resume_uses_frozen_asset_publish_dir(workspace, monkeypatch):
    config = json.loads((workspace / 'local.json').read_text())
    config['novel_asset_publish_dir'] = '/frozen/assets-A'
    (workspace / 'local.json').write_text(json.dumps(config))
    (workspace / 'TASK.md').write_text('制作一个明确的离线测试片段；不授权真实媒体。')
    monkeypatch.setenv('NOVEL_ASSET_PUBLISH_DIR', '/external/assets-B')
    monkeypatch.setenv('PI_PROBE_MODEL', 'test-model')
    monkeypatch.setenv('PI_PROBE_BASE', 'http://test.invalid')
    monkeypatch.setenv('PI_PROBE_API_KEY', 'test-only')
    captured = {}

    def fake_call(command, cwd, env):
        captured.update(env)
        return 0

    monkeypatch.setattr(starter.subprocess, 'call', fake_call)
    assert starter.run(workspace, resume=True) == 0
    assert captured['NOVEL_ASSET_PUBLISH_DIR'] == '/frozen/assets-A'


def test_blank_task_is_rejected_before_agent_starts(workspace, monkeypatch):
    (workspace / 'TASK.md').write_text('# 待填写的制作任务\n尚未授权付费生成。')
    monkeypatch.setenv('PI_PROBE_MODEL', 'test-model')
    monkeypatch.setenv('PI_PROBE_BASE', 'http://test.invalid')
    monkeypatch.setenv('PI_PROBE_API_KEY', 'test-only')

    def forbidden(*args, **kwargs):
        raise AssertionError('agent started with a blank task')

    monkeypatch.setattr(starter.subprocess, 'call', forbidden)
    with pytest.raises(ValueError, match='TASK.md'):
        starter.run(workspace)


def test_init_records_explicit_budget(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'budget-work', SDK, DEPS,
                              wall_seconds=123, max_model_requests=7)
    config = json.loads((work / 'local.json').read_text())
    assert config['wall_seconds'] == 123
    assert config['max_model_requests'] == 7


AUTHORISED_TASK = '离线批次测试任务：使用模拟模型和媒体，不授权真实生产。'


def test_batch_initializes_and_runs_episodes(tmp_path, monkeypatch):
    calls = []

    def fake_init(workspace, *args, **kwargs):
        workspace = Path(workspace)
        workspace.mkdir()
        calls.append(('init', workspace.name))
        return workspace

    def fake_run(workspace, **kwargs):
        calls.append(('run', Path(workspace).name))
        return 2 if Path(workspace).name == 'first' else 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    (tmp_path / 'second').mkdir()
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'episodes': [
        {'workspace': str(tmp_path / 'first'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
        {'workspace': str(tmp_path / 'second'), 'endpoint': 'test', 'resume': True},
    ]}))
    result = starter.run_batch(manifest)
    assert calls[0] == ('init', 'first')
    assert sorted(calls[1:]) == [('run', 'first'), ('run', 'second')]
    assert [row['returncode'] for row in result['episodes']] == [2, 0]
    assert result['workers'] == 2
    assert (manifest.parent / 'batch-state.json').is_file()


def test_previous_copy_cannot_modify_original(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    previous = tmp_path / 'previous'
    (previous / 'media').mkdir(parents=True)
    original = previous / 'media/old.mp4'
    original.write_bytes(b'original-media')
    work = starter.initialize(tmp_path / 'copy-work', SDK, DEPS, previous=previous)
    copied = work / 'input/previous/media/old.mp4'
    assert not copied.is_symlink()
    copied.write_bytes(b'new-workspace-media')
    assert original.read_bytes() == b'original-media'


def test_unknown_asset_upload_never_reposts(media, monkeypatch, workspace):
    calls = []

    def handler(req):
        if req.method == 'HEAD':
            return httpx.Response(200)
        if req.url.path.endswith('/CreateAsset'):
            calls.append(req)
            raise httpx.ReadTimeout('asset response lost', request=req)
        raise AssertionError(req.url.path)

    mock_provider(media, monkeypatch, handler)
    request = {'action': 'upload', 'path': 'input/identity.png'}
    with pytest.raises(Exception):
        media.execute(request)
    with pytest.raises(Exception, match='uncertain'):
        media.execute(request)
    assert len(calls) == 1


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


def test_fasth3_init_requires_endpoint_and_rejects_required(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    with pytest.raises(ValueError, match='endpoint'):
        starter.initialize(tmp_path / 'missing-endpoint', SDK, DEPS, lane='FastH3-Omni')
    with pytest.raises(ValueError, match='first_frame_policy=required'):
        starter.initialize(tmp_path / 'required-frame', SDK, DEPS, lane='FastH3-Omni',
                           first_frame='required', fasth3_endpoint='http://127.0.0.1:30027')


def test_fasth3_init_freezes_endpoint(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'fast-options', SDK, DEPS, lane='FastH3-Omni',
                              first_frame='selective', h3_mode='cloud',
                              fasth3_endpoint='http://127.0.0.1:30027')
    config = json.loads((work / 'local.json').read_text())
    assert config['media_lane'] == 'FastH3-Omni'
    assert config['fasth3_endpoint'] == 'http://127.0.0.1:30027'
    assert config['first_frame_policy'] == 'selective'
    capabilities = json.loads((work / 'CAPABILITIES.json').read_text())
    assert capabilities['fasth3']['model'] == 'FastH3-Omni'
    assert capabilities['fasth3']['endpoint'] == 'http://127.0.0.1:30027'
    assert capabilities['fasth3']['restart_clears_jobs_and_content'] is True
    assert 'FastH3-Omni' in (work / 'PRODUCTION.md').read_text()


def test_fasth3_media_preserves_bytes_nested_duration_and_lane_rules(media, monkeypatch, workspace):
    config = json.loads((workspace / 'local.json').read_text())
    config.update(media_lane='FastH3-Omni', fasth3_endpoint='http://127.0.0.1:30027', h3_mode='cloud')
    (workspace / 'local.json').write_text(json.dumps(config))
    media = module_at('media_fast_options', workspace / 'tools/media.py')
    posted = []

    def handler(request):
        if request.method == 'POST':
            posted.append(json.loads(request.content))
            return httpx.Response(202, json={'id': 'fast-task'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'fake MP4', headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={'id': 'fast-task', 'status': 'completed'})

    from novel_manga.providers import fast_h3
    original = fast_h3.FastH3MediaProvider

    def create(*args, **kwargs):
        provider = original(*args, **kwargs)
        provider.fast_client.close()
        provider.fast_client = httpx.Client(transport=httpx.MockTransport(handler))
        return provider

    monkeypatch.setattr(fast_h3, 'FastH3MediaProvider', create)
    media.execute({'action': 'fasth3', 'prompt': 'Picture 1 corridor', 'references': ['input/identity.png'],
                   'seconds': 5, 'seed': 2101, 'ratio': '9:16', 'output': 'media/fast.mp4'})
    assert len(posted) == 1
    payload = posted[0]
    assert payload['model'] == 'FastH3-Omni' and payload['task'] == 'ref2va'
    assert payload['prompt'] == 'Picture 1 corridor'
    assert payload['target'] == {'short_edge': 768, 'aspect_ratio': '9:16', 'duration_seconds': 5.0}
    assert payload['seed'] == 2101
    uri = payload['conditions'][0]['uri']
    import base64
    assert base64.b64decode(uri.split(',', 1)[1]) == (workspace / 'input/identity.png').read_bytes()
    wire = json.loads((workspace / 'media/fast.mp4.wire/request.json').read_text())
    assert wire['kind'] == 'fasth3' and wire['duration'] == 5.0
    assert wire['target']['short_edge'] == 768 and wire['seed'] == 2101
    assert json.loads((workspace / 'media/fast.mp4.wire/response.json').read_text())['task_id'] == 'fast-task'
    media.execute({'action': 'fasth3', 'prompt': 'Picture 1 corridor', 'references': ['input/identity.png'],
                   'seconds': 5, 'seed': 2101, 'ratio': '9:16', 'output': 'media/fast.mp4'})
    assert len(posted) == 1
    with pytest.raises(ValueError, match='first_frame'):
        media.execute({'action': 'fasth3', 'prompt': 'x', 'references': ['input/identity.png'],
                       'first_frame': 'input/identity.png', 'seconds': 5, 'output': 'media/fast-frame.mp4'})
    with pytest.raises(ValueError, match='Pinned video lane is FastH3-Omni'):
        media.execute({'action': 'h3', 'endpoint': 'pool', 'prompt': 'x', 'references': [],
                       'seconds': 5, 'output': 'media/fast-old-lane.mp4'})
    assert len(posted) == 1
    config['first_frame_policy'] = 'required'
    (workspace / 'local.json').write_text(json.dumps(config))
    media = module_at('media_fast_required', workspace / 'tools/media.py')
    with pytest.raises(ValueError, match='first_frame_policy=required'):
        media.execute({'action': 'fasth3', 'prompt': 'x', 'references': ['input/identity.png'],
                       'seconds': 5, 'output': 'media/fast-required.mp4'})
    assert len(posted) == 1


def test_fasth3_batch_passes_explicit_endpoint(tmp_path, monkeypatch):
    captured = {}

    def fake_init(workspace, *args, **kwargs):
        workspace = Path(workspace)
        workspace.mkdir()
        captured.update(kwargs)
        return workspace

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', lambda *args, **kwargs: 0)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS,
        'fasth3_endpoint': 'http://127.0.0.1:30027',
        'episodes': [{'workspace': str(tmp_path / 'episode'), 'lane': 'FastH3-Omni', 'task': AUTHORISED_TASK}]}))
    assert starter.run_batch(manifest)['ok'] is True
    assert captured['fasth3_endpoint'] == 'http://127.0.0.1:30027'


@pytest.mark.parametrize('endpoint', [
    'http://user:pass@example.test', 'http://example.test/?token=secret', 'http://example.test/#fragment'])
def test_fasth3_init_rejects_credential_urls_before_any_files(tmp_path, endpoint):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    workspace = tmp_path / 'unsafe-endpoint'
    with pytest.raises(ValueError):
        starter.initialize(workspace, SDK, DEPS, lane='FastH3-Omni', fasth3_endpoint=endpoint)
    assert not workspace.exists()


def test_fasth3_init_normalizes_trailing_slash(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'normalized-endpoint', SDK, DEPS, lane='FastH3-Omni',
                              fasth3_endpoint='http://127.0.0.1:30027/')
    assert json.loads((work / 'local.json').read_text())['fasth3_endpoint'] == 'http://127.0.0.1:30027'


@pytest.mark.parametrize('endpoint', [
    'http://127.0.0.1:30028', 'http://user:pass@127.0.0.1:30027', 'http://127.0.0.1:30027/?token=secret'])
def test_fasth3_request_endpoint_mismatch_writes_no_request_files(media, workspace, endpoint):
    config = json.loads((workspace / 'local.json').read_text())
    config.update(media_lane='FastH3-Omni', fasth3_endpoint='http://127.0.0.1:30027')
    (workspace / 'local.json').write_text(json.dumps(config))
    media = module_at('media_frozen_endpoint', workspace / 'tools/media.py')
    output = workspace / 'media/endpoint-mismatch.mp4'
    with pytest.raises(ValueError):
        media.execute({'action': 'fasth3', 'endpoint': endpoint, 'prompt': 'Picture 1 corridor',
                       'references': ['input/identity.png'], 'seconds': 5,
                       'output': 'media/endpoint-mismatch.mp4'})
    assert not output.with_suffix(output.suffix + '.request.json').exists()
    assert not output.with_suffix('.mp4.wire').exists()



def test_batch_runs_episodes_in_parallel(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    events = []
    lock = threading.Lock()

    def fake_init(workspace, *args, **kwargs):
        Path(workspace).mkdir()
        return Path(workspace)

    def fake_run(workspace, **kwargs):
        name = Path(workspace).name
        with lock:
            events.append(('start', name))
        barrier.wait(timeout=5)
        with lock:
            events.append(('end', name))
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    result = starter.run_batch(manifest)
    assert [row['returncode'] for row in result['episodes']] == [0, 0]
    starts = {name for kind, name in events if kind == 'start'}
    ends = {name for kind, name in events if kind == 'end'}
    assert starts == ends == {'a', 'b'}
    # Both runs must have started before either finished.
    second_start = max(events.index(('start', 'a')), events.index(('start', 'b')))
    first_end = min(events.index(('end', 'a')), events.index(('end', 'b')))
    assert second_start < first_end


def test_batch_results_stay_in_manifest_order_and_failure_does_not_block(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)

    def fake_init(workspace, *args, **kwargs):
        Path(workspace).mkdir()
        return Path(workspace)

    def fake_run(workspace, **kwargs):
        barrier.wait(timeout=5)
        return 1 if Path(workspace).name == 'a' else 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    result = starter.run_batch(manifest)
    assert [row['workspace'] for row in result['episodes']] == [str(tmp_path / 'a'), str(tmp_path / 'b')]
    assert [row['returncode'] for row in result['episodes']] == [1, 0]
    assert result['ok'] is False
    state = json.loads((manifest.parent / 'batch-state.json').read_text())
    assert [e['returncode'] for e in state['episodes']] == [1, 0]
    assert all(e['completed_at'] for e in state['episodes'])


def test_batch_state_updated_when_single_episode_finishes(tmp_path, monkeypatch):
    hold = threading.Event()
    result_holder = [None]

    def fake_init(workspace, *args, **kwargs):
        Path(workspace).mkdir()
        return Path(workspace)

    def fake_run(workspace, **kwargs):
        if Path(workspace).name == 'a':
            hold.wait(timeout=30)
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    state_path = manifest.parent / 'batch-state.json'

    def target():
        result_holder[0] = starter.run_batch(manifest)

    runner = threading.Thread(target=target)
    runner.start()
    try:
        for _ in range(200):
            if state_path.exists():
                state = json.loads(state_path.read_text())
                if state['episodes'][1]['completed_at'] is not None:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError('episode b did not complete before a was released')
        assert state['episodes'][0]['returncode'] is None
        assert state['episodes'][0]['completed_at'] is None
        assert state['episodes'][1]['returncode'] == 0
        assert state['episodes'][1]['completed_at'] is not None
    finally:
        hold.set()
    runner.join(timeout=10)
    assert runner.is_alive() is False
    assert [row['returncode'] for row in result_holder[0]['episodes']] == [0, 0]


def test_batch_pre_check_errors_never_call_run(tmp_path, monkeypatch):
    (tmp_path / 'existing').mkdir()
    (tmp_path / 'parent').mkdir()

    def fake_init(*args, **kwargs):
        raise AssertionError('initialize should not be called during pre-check failures')

    def fake_run(*args, **kwargs):
        raise AssertionError('run should not be called during pre-check failures')

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    base = {'sdk_root': str(SDK), 'dependencies': DEPS,
            'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}
    cases = [
        ({**base, 'workers': 0}, 'workers'),
        ({**base, 'workers': -2}, 'workers'),
        ({**base, 'workers': 'two'}, 'workers'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test'},
                               {'workspace': str(tmp_path / 'ws'), 'endpoint': 'test'}]}, 'overlap'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'parent'), 'endpoint': 'test', 'resume': True},
                               {'workspace': str(tmp_path / 'parent' / 'child'), 'endpoint': 'test'}]}, 'overlap'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'existing'), 'endpoint': 'test'}]}, 'resume'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'missing'), 'endpoint': 'test', 'resume': True}]},
         'Cannot resume'),
        ({**base, 'episodes': [{'endpoint': 'test'}]}, 'missing workspace'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'resume': 'false'}]}, 'boolean'),
        ({'dependencies': DEPS, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}, 'missing sdk_root'),
        ({'sdk_root': str(SDK), 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}, 'missing dependencies'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'lane': 'unknown'}]}, 'invalid lane'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': AUTHORISED_TASK, 'first_frame': 'all'}]}, 'invalid first_frame'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'interaction_page': 'optional'}]}, 'invalid interaction_page'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'h3_mode': 'hybrid'}]}, 'invalid h3_mode'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'viewpoint': 'third-person'}]}, 'invalid viewpoint'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'wall_seconds': 0}]}, 'wall_seconds'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'max_model_requests': -1}]}, 'max_model_requests'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'lane': 'FastH3-Omni'}]}, 'fasth3_endpoint'),
        ({**base, 'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'lane': 'FastH3-Omni',
                                'fasth3_endpoint': 'http://127.0.0.1:30027', 'first_frame': 'required'}]}, 'incompatible'),
    ]
    for data, match in cases:
        manifest = tmp_path / 'batch.json'
        manifest.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=match):
            starter.run_batch(manifest)
        assert not (manifest.parent / 'batch-state.json').exists()


def test_batch_invalid_second_episode_prevents_all_init_and_run(tmp_path, monkeypatch):
    calls = []

    def fake_init(*args, **kwargs):
        calls.append('init')
        return args[0]

    def fake_run(*args, **kwargs):
        calls.append('run')
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'lane': 'unknown'}]}))
    with pytest.raises(ValueError, match='invalid lane'):
        starter.run_batch(manifest)
    assert calls == []
    assert not (manifest.parent / 'batch-state.json').exists()


def test_batch_run_exception_does_not_block_other_episode(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)

    def fake_init(workspace, *args, **kwargs):
        Path(workspace).mkdir()
        return Path(workspace)

    def fake_run(workspace, **kwargs):
        barrier.wait(timeout=5)
        if Path(workspace).name == 'a':
            raise RuntimeError('simulated run crash')
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    result = starter.run_batch(manifest)
    assert [row['returncode'] for row in result['episodes']] == [2, 0]
    assert result['episodes'][0]['error'] == 'RuntimeError: simulated run crash'
    state = json.loads((manifest.parent / 'batch-state.json').read_text())
    assert [e['returncode'] for e in state['episodes']] == [2, 0]


def test_batch_directory_lock_rejects_concurrent_batch(tmp_path, monkeypatch):
    def fake_init(*args, **kwargs):
        raise AssertionError('initialize should not be called while lock is held by another batch')

    def fake_run(*args, **kwargs):
        raise AssertionError('run should not be called while lock is held by another batch')

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS,
        'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    lock_path = manifest.with_name('batch-state.lock')
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match='already running'):
            starter.run_batch(manifest)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert not (manifest.parent / 'batch-state.json').exists()


def test_batch_default_workers_equals_episode_count(tmp_path, monkeypatch):
    def fake_init(workspace, *args, **kwargs):
        Path(workspace).mkdir()
        return Path(workspace)

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', lambda *args, **kwargs: 0)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'episodes': [
        {'workspace': str(tmp_path / 'a'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
        {'workspace': str(tmp_path / 'b'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
        {'workspace': str(tmp_path / 'c'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
    ]}))
    result = starter.run_batch(manifest)
    assert result['workers'] == 3



def test_batch_init_failure_keeps_error_and_does_not_run_that_episode(tmp_path, monkeypatch):
    calls = []

    def fake_init(workspace, *args, **kwargs):
        if Path(workspace).name == 'bad':
            raise RuntimeError('simulated init failure')
        Path(workspace).mkdir()
        calls.append(('init', Path(workspace).name))
        return Path(workspace)

    def fake_run(workspace, **kwargs):
        calls.append(('run', Path(workspace).name))
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS, 'workers': 2,
        'episodes': [{'workspace': str(tmp_path / 'bad'), 'endpoint': 'test', 'task': AUTHORISED_TASK},
                     {'workspace': str(tmp_path / 'ok'), 'endpoint': 'test', 'task': AUTHORISED_TASK}]}))
    result = starter.run_batch(manifest)
    assert calls == [('init', 'ok'), ('run', 'ok')]
    assert result['episodes'][0]['returncode'] == 2
    assert result['episodes'][0]['error'] == 'RuntimeError: simulated init failure'
    assert result['episodes'][1]['returncode'] == 0
    state = json.loads((manifest.parent / 'batch-state.json').read_text())
    assert state['episodes'][0]['returncode'] == 2
    assert state['episodes'][0]['error'] == 'RuntimeError: simulated init failure'
    assert state['episodes'][1]['returncode'] == 0


def _fake_film(tmp_path, name='film-v1.mp4'):
    film = tmp_path / name
    film.write_bytes(b'finished-export-bytes')
    return film


def test_reviewer_role_installs_review_skill_and_task(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    film = _fake_film(tmp_path)
    timeline = tmp_path / 'film-v1.mp4.timeline.json'
    timeline.write_text(json.dumps({'duration': 12.0, 'fps': 25, 'clips': [
        {'path': 'media/takes/s01a-v2.mp4', 'start': 0.0, 'end': 6.0}]}))
    baseline = tmp_path / 'script.md'
    baseline.write_text('# 源剧本\n\n一句台词。\n')
    work = starter.initialize(tmp_path / 'rev', SDK, DEPS, role='reviewer',
                              review_film=film, review_timeline=timeline,
                              review_baseline=baseline, review_brief='允许合理改编；只有影响观看才拒绝。')
    # Reviewer system prompt and skill, and no producer skill.
    assert '独立审片人' in (work / 'SYSTEM.md').read_text()
    assert (work / 'skills/episode-review/SKILL.md').is_file()
    assert not (work / 'skills/episode-production').exists()
    assert not (work / 'SKILL.md').exists()
    # local.json narrows the media entry and points the loader at the review skill.
    config = json.loads((work / 'local.json').read_text())
    assert config['media_actions'] == ['inspect', 'audio']
    # Untimed bash calls are bounded; the value is frozen at init so a resume cannot drift.
    assert config['bash_timeout_seconds'] == 900
    assert config['skill_path'] == 'skills/episode-review'
    assert config['media_lane'] == ''
    assert config['first_frame_policy'] == 'none'
    # TASK.md is complete on arrival, so run() accepts it without manual editing.
    task = (work / 'TASK.md').read_text()
    assert str(film.resolve()) in task
    assert starter._sha256(film) in task
    assert 's01a' in task                      # shot id source is the timeline
    assert '允许合理改编' in task                 # authorization travels with the task
    assert not task.startswith('# 待填写的制作任务')
    assert '尚未授权付费生成' not in task
    # Baseline is copied into the reviewer's own input, not read from the producer tree.
    assert (work / 'input/review-baseline.md').read_text() == baseline.read_text()


def test_reviewer_role_rejects_production_options(tmp_path):
    film = _fake_film(tmp_path)
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r1', SDK, DEPS, role='reviewer')
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r2', SDK, DEPS, role='reviewer',
                           review_film=film, lane='FastH3-Omni')
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r3', SDK, DEPS, role='reviewer',
                           review_film=tmp_path / 'missing.mp4')
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r4', SDK, DEPS, role='reviewer',
                           review_film=film, review_timeline=tmp_path / 'missing.json')
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r5', SDK, DEPS, role='producer', review_film=film)
    with pytest.raises(ValueError):
        starter.initialize(tmp_path / 'r6', SDK, DEPS, role='nonsense', review_film=film)


def test_reviewer_media_entry_refuses_generation_actions(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    film = _fake_film(tmp_path)
    work = starter.initialize(tmp_path / 'rev', SDK, DEPS, role='reviewer', review_film=film)
    media = module_at('review_media', work / 'tools/media.py')
    for action in ('image', 'video', 'h3', 'fasth3', 'upload'):
        with pytest.raises(ValueError, match='media_actions'):
            media.execute({'action': action})


def test_reviewer_native_launch_loads_system_task_and_review_skill(tmp_path):
    """Offline: the reviewer really loads REVIEWER-SYSTEM, its TASK and episode-review.

    The three install-only tests above prove file placement and entry refusal. This one runs the
    native Pi session against a fake SSE server and inspects the first request body, so a broken
    skill path or a TASK that never reaches the model would fail here.
    """
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    film = tmp_path / 'film-v1.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=navy:s=64x96:d=2', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono',
                    '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    str(film)], check=True, timeout=120)
    timeline = tmp_path / 'film-v1.mp4.timeline.json'
    timeline.write_text(json.dumps({'duration': 2.0, 'fps': 25, 'clips': [
        {'path': 'media/takes/s01a-v2.mp4', 'start': 0.0, 'end': 1.5}]}))
    baseline = tmp_path / '02-script.json'
    baseline.write_text(json.dumps({'flow': [{'speaker': '知遥', 'line': '一句台词。'}]},
                                   ensure_ascii=False))
    work = starter.initialize(
        tmp_path / 'rev', SDK, DEPS, role='reviewer', review_film=film,
        review_timeline=timeline, review_baseline=baseline,
        review_brief='本轮 46 句对白逐字保留、9 场事件与因果不改；固定 A/B/C 选项。')

    preload = tmp_path / 'fake-fetch.mjs'
    preload.write_text('''import fs from 'node:fs';
let calls=0;
globalThis.fetch=async(url,options={})=>{
 if(!String(url).startsWith('http://127.0.0.1:1/'))throw new Error('Offline test forbids network');
 const body=JSON.parse(options.body);calls++;
 fs.writeFileSync(process.env.PI_RUN_DIR+'/first-request.json',JSON.stringify(body));
 const common={id:'offline',object:'chat.completion.chunk',model:body.model,created:1};
 const chunks=[{...common,choices:[{index:0,delta:{role:'assistant',content:'offline review done'},finish_reason:null}]},
 {...common,choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:10,total_tokens:20}}];
 return new Response(chunks.map(x=>'data: '+JSON.stringify(x)+'\\n\\n').join('')+'data: [DONE]\\n\\n',
   {headers:{'content-type':'text/event-stream'}});
};''')
    env = dict(os.environ, PI_RUN_DIR=str(work), PI_PROBE_MODEL='offline-model',
               PI_PROBE_BASE='http://127.0.0.1:1/v1', PI_PROBE_API_KEY='offline',
               BUN_OPTIONS='--preload ' + str(preload))
    result = subprocess.run([sys.executable, str(work / 'starter.py'), 'run', str(work)],
                            cwd=work, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    body = json.loads((work / 'first-request.json').read_text())
    sent = json.dumps(body, ensure_ascii=False)

    # The reviewer system prompt is what the session actually runs under.
    assert '独立审片人' in sent
    assert '创作者、导演和制作人' not in sent
    # The review skill is loaded (config.skill_path), not the producer skill.
    assert 'episode-review' in sent
    assert 'episode-production' not in sent
    # TASK reaches the model: target, hash, shot-id source, brief and the page boundary.
    assert str(film.resolve()) in sent
    assert starter._sha256(film) in sent
    assert 's01a' in sent
    assert '46 句对白逐字保留' in sent
    assert '尾卡' in sent
    # Severity and owner are taught as separate axes, and coverage must be split by evidence type.
    assert 'severity' in sent and 'owner' in sent
    assert '不会改变已经导出的这一份 MP4' in sent
    assert '覆盖声明要按实际证据写' in sent
    # F0 is taught as non-identifying: no pass and no rejection from pitch alone.
    assert '基频（F0）不是说话人识别器' in sent

    # A real 96/96 run died before writing anything while tidying temp dirs. The contract must
    # tell the reviewer that the verdict, not the cleanup, is what has to land first.
    for source in ('SYSTEM.md', 'TASK.md', 'skills/episode-review/SKILL.md'):
        text = (work / source).read_text(encoding='utf-8')
        assert 'Run facts' in text, source
    system_text = (work / 'SYSTEM.md').read_text(encoding='utf-8')
    assert '先落盘' in system_text or '判词落盘优先' in system_text
    assert '证据够了就立刻写' in (work / 'skills/episode-review/SKILL.md').read_text()

    # SYSTEM, TASK and Skill must state the same review.json contract. SYSTEM outranks the others,
    # so a stale top-level blocking/advisory schema there would win and split the output shape.
    for source in ('SYSTEM.md', 'TASK.md', 'skills/episode-review/SKILL.md'):
        text = (work / source).read_text(encoding='utf-8')
        assert 'findings' in text, source
        assert 'severity' in text and 'owner' in text, source
        assert '"blocking": [' not in text, source
        assert '"advisory": [' not in text, source
        assert 'tool_defect' not in text, source
        assert 'fixable_in_episode' not in text, source
    # SYSTEM must not still ask for the retired top-level field list.
    system = (work / 'SYSTEM.md').read_text(encoding='utf-8')
    assert '`blocking`、`advisory`、`coverage`' not in system
    # And the contract as sent to the model names findings, not the old arrays.
    assert 'findings' in sent
    assert '"blocking": [' not in sent

    # Generation tools stay available to the shell, but the media entry is started as reviewer.
    config = json.loads((work / 'local.json').read_text())
    assert config['media_actions'] == ['inspect', 'audio']
    # Untimed bash calls are bounded; the value is frozen at init so a resume cannot drift.
    assert config['bash_timeout_seconds'] == 900


def _budget(work, deadline, limit=96, used=10):
    (work / 'run-budget.json').write_text(json.dumps({
        'started_at': 1, 'deadline_at': deadline * 1000, 'request_limit': limit,
        'provider_requests': used, 'assistant_messages': used,
        'automatic_continuations': 0, 'body_compactions': 0, 'ended_at': 1}))


def test_window_grant_extends_deadline_once(tmp_path):
    work = tmp_path / 'w'
    work.mkdir()
    past = time.time() - 3600
    _budget(work, past)
    (work / 'window-grant.json').write_text(json.dumps(
        {'seconds': 600, 'authorized_by': '用户批准修 A 的 blocking', 'reason': '审片 rejected'}))
    deadline = starter._consume_window_grant(work, work / 'run-budget.json', past)
    assert abs(deadline - (time.time() + 600)) < 5
    budget = json.loads((work / 'run-budget.json').read_text())
    assert budget['deadline_at'] == deadline * 1000
    assert budget['ended_at'] is None
    assert budget['window_grants'][0]['authorized_by'] == '用户批准修 A 的 blocking'
    # Same request limit: a grant buys time, never paid calls.
    assert budget['request_limit'] == 96 and budget['provider_requests'] == 10
    grant = json.loads((work / 'window-grant.json').read_text())
    assert grant['consumed_at'] and grant['previous_deadline_at'] == past
    # Second resume with the same file must not mint more time.
    assert starter._consume_window_grant(work, work / 'run-budget.json', deadline) == deadline
    assert json.loads((work / 'run-budget.json').read_text())['deadline_at'] == deadline * 1000


def test_window_grant_requires_authorization_and_positive_seconds(tmp_path):
    work = tmp_path / 'w'
    work.mkdir()
    past = time.time() - 60
    _budget(work, past)
    (work / 'window-grant.json').write_text(json.dumps({'seconds': 600}))
    with pytest.raises(ValueError, match='authorized_by'):
        starter._consume_window_grant(work, work / 'run-budget.json', past)
    (work / 'window-grant.json').write_text(json.dumps(
        {'seconds': 0, 'authorized_by': 'someone'}))
    with pytest.raises(ValueError, match='positive seconds'):
        starter._consume_window_grant(work, work / 'run-budget.json', past)
    # No grant file: deadline passes through untouched.
    (work / 'window-grant.json').unlink()
    assert starter._consume_window_grant(work, work / 'run-budget.json', past) == past


def test_batch_rejects_fresh_episode_without_task_before_creating_workspace(tmp_path, monkeypatch):
    """The real gap: init writes a blank TASK, run() refuses it, so the batch could never start."""
    calls = []

    def fake_init(*args, **kwargs):
        calls.append('init')
        return args[0]

    def fake_run(*args, **kwargs):
        calls.append('run')
        return 0

    monkeypatch.setattr(starter, 'initialize', fake_init)
    monkeypatch.setattr(starter, 'run', fake_run)
    manifest = tmp_path / 'batch.json'
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS,
        'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test'}]}))
    with pytest.raises(ValueError, match='needs task or task_file'):
        starter.run_batch(manifest)
    assert calls == []
    assert not (tmp_path / 'batch-state.json').exists()

    # A blank template handed in explicitly is rejected the same way.
    template = (Path(starter.__file__).parent / 'template/TASK.md').read_text()
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS,
        'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'task': template}]}))
    with pytest.raises(ValueError, match='blank template'):
        starter.run_batch(manifest)
    assert calls == []

    # Resume entries are left alone: their TASK.md was already authorised by an earlier run.
    (tmp_path / 'ws').mkdir()
    manifest.write_text(json.dumps({'sdk_root': str(SDK), 'dependencies': DEPS,
        'episodes': [{'workspace': str(tmp_path / 'ws'), 'endpoint': 'test', 'resume': True}]}))
    assert starter.run_batch(manifest)['episodes'][0]['returncode'] == 0
    assert calls == ['run']


def test_initialize_writes_authorised_task_so_run_accepts_it(tmp_path):
    if not DEPS:
        pytest.skip('Set PI_STARTER_NODE_MODULES to existing Pi dependencies; no install is performed.')
    work = starter.initialize(tmp_path / 'ep', SDK, DEPS, task=AUTHORISED_TASK)
    assert (work / 'TASK.md').read_text().strip() == AUTHORISED_TASK
    assert starter._task_is_authorised((work / 'TASK.md').read_text())
    # task_file is the same thing from disk, and both together is ambiguous.
    task_path = tmp_path / 'task.md'
    task_path.write_text(AUTHORISED_TASK + '\n', encoding='utf-8')
    second = starter.initialize(tmp_path / 'ep2', SDK, DEPS, task_file=task_path)
    assert (second / 'TASK.md').read_text().strip() == AUTHORISED_TASK
    with pytest.raises(ValueError, match='not both'):
        starter.initialize(tmp_path / 'ep3', SDK, DEPS, task=AUTHORISED_TASK, task_file=task_path)
    with pytest.raises(ValueError, match='blank template'):
        starter.initialize(tmp_path / 'ep4', SDK, DEPS, task='# 待填写的制作任务\n\n尚未授权付费生成。')
