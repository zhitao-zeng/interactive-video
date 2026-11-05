from types import SimpleNamespace

import pytest

from novel_manga.util import atomic_write_json


@pytest.mark.parametrize('lane', ['sd25','sd20-fast','sd20-mini'])
def test_standard_sd_run_passes_the_selected_agent_config_to_nested_repair(tmp_path, monkeypatch, lane):
    from novel_manga.interactive import production
    from novel_manga.interactive import agent as interactive_agent
    from novel_manga.interactive import delivery

    novel = tmp_path/'suspense-edition'
    episode = novel/(novel.name+'_1')
    episode.mkdir(parents=True)
    atomic_write_json(novel/'profile.json', {})
    atomic_write_json(episode/'chapter_script.json', {'shots':[]})
    monkeypatch.setattr(production, 'edition_directory', lambda *_:novel)
    runner = SimpleNamespace(context=SimpleNamespace(novel_dir=novel,episode_dir=episode,profile={}))
    monkeypatch.setattr(production, 'runner_for', lambda *_a,**_k:runner)
    selected = {'provider':'phanrouter-sandbox','model':'deepseek-v4.1-flash-1017',
        'base_url':'https://cloud.phanthy.com/phanrouter','key_var':'PHANROUTER_IMAGE_API_KEY'}
    seen = {}
    def run(_runner, selected_lane, **kwargs):
        seen.update(lane=selected_lane, **kwargs)
        return {'status':'assembled','clips':[],'failed_clips':[],'gate_failed_clips':[],
                'assembly':{'final_video':'not-used'}}
    monkeypatch.setattr(interactive_agent, 'run', run)
    monkeypatch.setattr(delivery, 'deliver', lambda *_:{'status':'candidate'})

    result = production.run_episode(tmp_path,lane,project='suspense',edition=novel.name,
                                    agent_config=selected)
    assert result['status']=='candidate'
    assert seen['lane']==lane
    assert seen['config']['model']==selected['model']
    assert seen['config']['base_url']==selected['base_url']
    assert seen['config']['key_var']==selected['key_var']
    assert seen['config']['_agent_config_explicit'] is True
    record = __import__('novel_manga.util', fromlist=['read_json']).read_json(
        episode/'work/interactive-agent-config.json', {})
    assert record['selected']['model'] == selected['model']
    assert 'api_key' not in record['selected']

    seen.clear()
    result = production.run_episode(tmp_path,lane,project='suspense',edition=novel.name,
                                    resume=True)
    assert result['status']=='candidate'
    assert seen['config']['model']==selected['model']
    assert seen['config']['_agent_config_explicit'] is True
