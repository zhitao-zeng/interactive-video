"""An Agent can take over a filmed edition without copying away its budget."""
import json
from pathlib import Path

import pytest
from PIL import Image

from novel_manga.application.agents.episode import cli, context
from novel_manga.application.agents.episode.inputs import prepare_inputs
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.repair import history
from novel_manga.interactive.authored import prepare_authored, pack_authored
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def production(tmp_path, monkeypatch):
    monkeypatch.setattr(context, 'configured_root', lambda: tmp_path / 'project')
    project = tmp_path / 'approved'
    project.mkdir()
    material = json.loads((Path(__file__).parent / 'fixtures/interactive/minimal-material.json').read_text())
    Image.new('RGB', (32, 48), 'gray').save(project / 'identity.png')
    material['reference_images'] = [{'source': 'identity.png',
        'path': 'series_assets/characters/character_001/turnaround.jpeg', 'scope': 'identity_portrait'}]
    (project / 'source.md').write_text('原始片段：先核对记录。')
    atomic_write_json(project / 'material.json', material)
    episode = prepare_authored(project, tmp_path / 'adventure-existing', family='adventure')
    plan = pack_authored(episode)
    return episode.parent, episode, plan


def test_takeover_retains_actual_takes_pending_tasks_and_original_budget(production, tmp_path):
    novel, episode, plan = production
    cid = plan['clips'][0]['clip_id']
    history.begin_trial(episode, {cid}, 'existing producer', after_plan=plan, after_notes={})
    saved = history.load(episode)
    saved['trials'][-1]['managed'] = True
    history.save(episode, saved)
    video = episode / 'work/clips' / cid / 'attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    request = history.generation_request(episode, video,
        {'clip_id': cid, 'attempt': 1, 'workflow': 'thin', 'prompt': 'existing request'},
        expected_plan=plan_fingerprint(plan), expected_notes={})
    atomic_write_json(video.parent / 'request.json', request)
    atomic_write_json(video.with_suffix('.mp4.task.json'), {'task_id': 'existing-completed-task'})
    video.write_bytes(b'completed physical take fixture')
    history.complete_generation(episode, cid, video, duration=5)
    pending = episode / 'work/clips/clip_pending/attempt_01/clip.mp4.task.json'
    atomic_write_json(pending, {'task_id': 'already-submitted-pending-task', 'seconds': 7})
    atomic_write_json(episode / 'repair_budget_grants.json', {cid: {'limit': 3, 'reason': 'original limit'}})
    before_counts = history.generation_counts(episode)
    assert before_counts == {cid: 1}
    paths = [episode / 'chapter_script.json', episode / 'clip_plan.json', video,
             video.parent / 'request.json', video.with_suffix('.mp4.task.json'), pending,
             episode / 'repair_history/history.json', episode / 'repair_budget_grants.json']
    before = {p: p.read_bytes() for p in paths}
    image = novel / 'series_assets/characters/character_001/turnaround.jpeg'
    image_stat = image.stat()
    ctx = context.create_context(novel, episode.name, output_root=tmp_path / 'agent',
                                 existing_production=True)
    assert ctx.novel_dir == novel and ctx.episode_dir == episode
    assert not ctx.run_dir.is_relative_to(novel)
    for _ in range(2):
        manifest = prepare_inputs(ctx)
        assert history.generation_counts(ctx.episode_dir) == before_counts
        assert all(p.read_bytes() == contents for p, contents in before.items())
        assert image.stat().st_ino == image_stat.st_ino
        assert image.stat().st_mtime_ns == image_stat.st_mtime_ns
        assert read_json(ctx.run_dir / 'prepared.json')['generation_counts'] == before_counts
        assert read_json(ctx.run_dir / 'prepared.json')['method'] == 'existing production takeover'
        assert manifest['input_mode'] == 'script'
        assert read_json(ctx.work_dir / 'input/interaction.json') == read_json(episode / 'interaction.json')


def test_takeover_requires_existing_approved_plan_and_does_not_change_default(production, tmp_path):
    novel, episode, _ = production
    normal = context.create_context(novel, episode.name, output_root=tmp_path / 'agent')
    assert normal.novel_dir != novel
    with pytest.raises(ValueError, match='existing production requires'):
        context.create_context(novel, episode.name, input_mode='source', existing_production=True)
    (episode / 'clip_plan.json').unlink()
    with pytest.raises(ValueError, match='existing production requires'):
        context.create_context(novel, episode.name, existing_production=True)


def test_formal_cli_hands_existing_production_to_same_episode_controller(production, tmp_path, monkeypatch):
    novel, episode, _ = production
    seen = []
    def run(ctx, **options):
        seen.append(ctx)
        assert ctx.novel_dir == novel and ctx.episode_dir == episode
        return {'exit_code': 0}
    monkeypatch.setattr(cli, 'run_episode', run)
    assert cli.main(['run', '--novel-dir', str(novel), '--episode', episode.name,
                     '--output-root', str(tmp_path / 'agent'), '--existing-production']) == 0
    assert len(seen) == 1


def test_standalone_takeover_resumes_saved_slice_without_retry_window(production, tmp_path, monkeypatch):
    novel, episode, _ = production
    calls = []
    results = iter([
        {'status': 'deferred', 'exit_code': 0, 'remaining_seconds': 240,
         'session_id': 'saved-session', 'reason': 'Agent reached this slice time limit; continue the saved session'},
        {'status': 'finished', 'exit_code': 0, 'session_id': 'saved-session'},
    ])
    def run(ctx, **options):
        calls.append((ctx, options))
        return next(results)
    monkeypatch.setattr(cli, 'run_episode', run)
    assert cli.main(['run', '--novel-dir', str(novel), '--episode', episode.name,
        '--output-root', str(tmp_path / 'agent'), '--existing-production', '--retry-issues']) == 0
    assert calls[0][0] is calls[1][0]
    assert calls[0][1] == {'timeout': None, 'retry_issues': True}
    assert calls[1][1] == {}  # lifecycle reloads the saved UUID and remaining 240 seconds


@pytest.mark.parametrize('existing,reason,remaining', [
    (False, 'Agent reached this slice time limit; continue the saved session', 240),
    (True, 'Agent reached this slice time limit; continue the saved session', 0),
    (True, 'Shared batch assets pending; release the production host', 240),
])
def test_no_automatic_new_window_or_unrelated_scheduler_loop(production, tmp_path, monkeypatch,
                                                           existing, reason, remaining):
    novel, episode, _ = production
    calls = []
    def run(ctx, **options):
        calls.append(ctx)
        return {'status': 'deferred', 'reason': reason, 'remaining_seconds': remaining, 'exit_code': 0}
    monkeypatch.setattr(cli, 'run_episode', run)
    args = ['run', '--novel-dir', str(novel), '--episode', episode.name, '--output-root', str(tmp_path / 'agent')]
    assert cli.main(args + (['--existing-production'] if existing else [])) == 0
    assert len(calls) == 1


def test_takeover_automatically_enters_existing_fallback_without_retrying_window(production,tmp_path,monkeypatch):
    novel,episode,_=production;attempts=[]
    for n in range(2):
        p=tmp_path/f'idle{n}';attempts.append(str(p))
        atomic_write_json(p/'attempt.json',{'exit':124,'produced':[]})
        (p/'session.jsonl').write_text('')
    config={'model':'primary','refusal_fallback':{'model':'fallback','base_url':'http://existing','key_var':'KEY'}}
    path=tmp_path/'task.json';atomic_write_json(path,config);calls=[]
    results=iter([
        {'status':'blocked','reason':'episode agent ended twice without operation or output progress',
         'remaining_seconds':240,'attempts':attempts,'exit_code':2},
        {'status':'finished','model_switch':{'to_model':'fallback'},'exit_code':0}])
    monkeypatch.setattr(cli,'run_episode',lambda ctx,**options:calls.append(options)or next(results))
    assert cli.main(['run','--novel-dir',str(novel),'--episode',episode.name,
        '--output-root',str(tmp_path/'agent'),'--existing-production','--config',str(path)])==0
    assert calls==[{'timeout':None,'config':config},{'config':config}]
