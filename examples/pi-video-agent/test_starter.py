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
    (workspace / 'tools/check.py').write_text("print('before')\n")
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
               BUN_OPTIONS='--preload ' + str(preload))
    cmd = [sys.executable, str(workspace / 'starter.py'), 'run', str(workspace)]
    first = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=45)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (workspace / 'tools/check.py').read_text() == "print('after')\n"
    body = json.loads((workspace / 'last-request.json').read_text())
    assert {'read', 'write', 'edit', 'bash'} <= {tool['function']['name'] for tool in body['tools']}
    assert 'episode-production' in str(body['messages'])
    assert any('after' in str(m.get('content')) and m['role'] == 'tool' for m in body['messages'])
    assert body['max_tokens'] == 128000
    saved = (workspace / 'session-path.txt').read_text()
    second = subprocess.run(cmd + ['--resume'], cwd=workspace, env=dict(env, TEST_RESUME='1'),
                            capture_output=True, text=True, timeout=45)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (workspace / 'session-path.txt').read_text() == saved
    assert (workspace / 'tools/check.py').read_text() == "print('after')\n"
    resumed = json.loads((workspace / 'last-request.json').read_text())
    assert any('after' in str(m.get('content')) and m['role'] == 'tool' for m in resumed['messages'])
    with pytest.raises(FileExistsError):
        starter.initialize(workspace, SDK, DEPS)


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
        'seconds': 6, 'output': 'media/h3.mp4'})
    assert posted[0]['model'] == 'MiniMax-H3'
    conditions = posted[0]['conditions']
    assert [x['role'] for x in conditions] == ['reference', 'reference', 'keyframe']
    assert conditions[0]['uri'] == conditions[-1]['uri']
    assert conditions[-1]['frame_index'] == 0
    wire = json.loads((workspace / 'media/h3.mp4.wire/request.json').read_text())
    assert len(wire['attachments']) == 2 and wire['first_frame_bound'] is True


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
