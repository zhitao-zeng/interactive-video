import sys


def test_run_uses_selected_existing_project_root_and_agent_config(monkeypatch, tmp_path, capsys):
    from scripts import interactive_video
    from novel_manga.application.agents import sandbox
    from novel_manga.interactive import production

    project_root = tmp_path / 'retained-project'
    postproduction = tmp_path/'b-cutaway.json'
    postproduction.write_text('{}')
    selected = {'model': 'deepseek-v4.1-flash-1017', 'base_url': 'https://provider.invalid',
                'key_var': 'PHANROUTER_IMAGE_API_KEY'}
    seen = {}
    monkeypatch.setattr(sys, 'argv', [
        'interactive_video.py', 'run', '--project-root', str(project_root), '--lane', 'h3',
        '--project', 'suspense', '--edition', 'suspense-backrooms55-ep01-20261020',
        '--config', str(tmp_path/'official.json'), '--postproduction-plan',str(postproduction),'--resume'])
    monkeypatch.setattr(sandbox, 'load_config', lambda path: selected)
    def run(root, lane, **kwargs):
        seen.update(root=root, lane=lane, **kwargs)
        return {'status': 'accepted'}
    monkeypatch.setattr(production, 'run_episode', run)

    assert interactive_video.main() == 0
    assert seen['root'] == project_root.resolve()
    assert seen['lane'] == 'h3' and seen['project'] == 'suspense'
    assert seen['resume'] is True and seen['edition'] == 'suspense-backrooms55-ep01-20261020'
    assert seen['agent_config'] == selected
    assert seen['postproduction_plan'] == postproduction


def test_status_uses_selected_project_root_for_retained_edition(monkeypatch, tmp_path, capsys):
    from scripts import interactive_video
    from novel_manga.interactive import production

    project_root = tmp_path / 'retained-project'
    seen = []
    monkeypatch.setattr(sys, 'argv', [
        'interactive_video.py', 'status', '--project-root', str(project_root), '--project', 'suspense',
        '--edition', 'suspense-backrooms55-ep01-20261020'])
    monkeypatch.setattr(production, 'edition_status', lambda root, project, edition:
        seen.append((root, project, edition)) or {'status': 'running'})
    assert interactive_video.main() == 0
    assert seen == [(project_root.resolve(), 'suspense', 'suspense-backrooms55-ep01-20261020')]


def test_first_frame_status_follows_existing_active_episode_marker(tmp_path, monkeypatch):
    from novel_manga.interactive import production
    from novel_manga.util import atomic_write_json
    from novel_manga.application.repair import history

    novel = tmp_path/'outputs'/'suspense-backrooms55-ep01-20261020'
    source = novel/(novel.name+'_1')
    active = tmp_path/'outputs'/'episode-agent-firstframes'/novel.name/(novel.name+'_1')
    run_dir = tmp_path/'outputs'/'episode-agent-firstframes'/novel.name/'runs'/'agent'
    source.mkdir(parents=True)
    active.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    atomic_write_json(source/'clip_plan.json', {'clips':[]})
    atomic_write_json(source/'thin_media_report.json', {'status':'prepared','clips':[]})
    atomic_write_json(novel/'first-frame-agent-run.json', {
        'active_episode_dir':str(active),'run_dir':str(run_dir)})
    atomic_write_json(active/'thin_media_report.json', {'status':'clips_ready','clips':[]})
    atomic_write_json(active/'final_cut_review.json', {'complete':False,'passed':False,'issues':[]})
    atomic_write_json(run_dir/'agent-run.json', {'status':'running','phase':'render'})
    monkeypatch.setattr(production, 'edition_directory', lambda *_:novel)
    observed=[]
    monkeypatch.setattr(history, 'generation_counts', lambda path: observed.append(path) or {'video':95})

    status = production.edition_status(tmp_path,'suspense',novel.name)
    assert status['episode_dir'] == str(active.resolve())
    assert status['source_episode_dir'] == str(source)
    assert status['active_run_dir'] == str(run_dir)
    assert status['source_status'] == 'clips_ready'
    assert status['active_agent_status'] == 'running' and status['active_phase'] == 'render'
    assert status['generation_total'] == 95 and observed == [active.resolve()]
