"""A single studio entry retains project choices and existing episode ownership."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.interactive import runtime,production,creation
from novel_manga.application.repair import history

ROOT=Path(__file__).resolve().parents[1]


def configuration(root):
    atomic_write_json(root/'configs/interactive.json',{
        'production':{'h3_short_edge':544,'workers_by_lane':{'h3':2,'sd25':4}},
        'services':{'text_endpoint':'shared','asset_publish_dir':'outputs/public'},
        'projects':{'adventure':{'production':{'h3_short_edge':768,'workers_by_lane':{'h3':1}},
                                 'services':{'text_endpoint':'adventure-text'}},
                    'romance':{'services':{'text_endpoint':'romance-text'}},'suspense':{}}})


def test_project_settings_preserve_other_defaults_and_neighbours(tmp_path):
    configuration(tmp_path)
    before=(tmp_path/'configs/interactive.json').read_bytes()
    a=runtime.project_config(tmp_path,'adventure');b=runtime.project_config(tmp_path,'romance')
    assert a['production']['h3_short_edge']==768
    assert a['production']['workers_by_lane']=={'h3':1,'sd25':4}
    assert a['services']=={'text_endpoint':'adventure-text','asset_publish_dir':'outputs/public'}
    assert b['production']['h3_short_edge']==544 and b['services']['text_endpoint']=='romance-text'
    a['production']['workers_by_lane']['sd25']=99
    assert runtime.project_config(tmp_path,'suspense')['production']['workers_by_lane']['sd25']==4
    assert (tmp_path/'configs/interactive.json').read_bytes()==before


def test_episode_sampling_and_workers_override_project_defaults(tmp_path,monkeypatch):
    from novel_manga.config import Settings
    from novel_manga.application import profiles
    from novel_manga.application.rendering import flow
    import os
    configuration(tmp_path);book=tmp_path/'outputs/adventure-new';book.mkdir(parents=True)
    fixture=read_json(ROOT/'tests/fixtures/interactive/minimal-material.json')
    atomic_write_json(book/'story_bible.json',fixture['bible'])
    profile={'native_h3_short_edge':544,'workers_by_lane':{'h3':3}}
    monkeypatch.setattr(runtime,'configure',lambda *a,**k:monkeypatch.setenv('NOVEL_H3_SHORT_EDGE','768'))
    monkeypatch.setattr(profiles,'load_profile',lambda *a,**k:profile)
    monkeypatch.setattr(Settings,'from_env',staticmethod(lambda **k:SimpleNamespace(native=os.environ['NOVEL_H3_SHORT_EDGE'])))
    monkeypatch.setattr(flow,'ThinMediaRunner',lambda **k:SimpleNamespace(**k))
    result=runtime.runner_for(tmp_path,book,'h3',project='adventure')
    assert result.settings.native=='544' and result.workers==3


def historical(root,project='suspense'):
    name=project+'-existing';book=root/'original-data'/name;ep=book/(name+'_1');ep.mkdir(parents=True)
    atomic_write_json(ep/'clip_plan.json',{'clips':[{'clip_id':'a','kind':'video'}]})
    atomic_write_json(ep/'intake.json',{'project':project})
    movie=book/'reviewed.mp4';movie.write_bytes(b'original movie')
    final={'complete':True,'passed':False,'movie':str(movie),'issues':[{'clip_ids':['a'],'problem':'unresolved'}]}
    atomic_write_json(ep/'final_cut_review.json',final)
    atomic_write_json(ep/'thin_media_report.json',{'status':'completed','final_cut_review':final,
                      'quality_review':{'passed':False,'content_passed':False}})
    state=history.load(ep);state['prior_generation_counts']={'clips':{'a':3}};history.save(ep,state)
    alias=root/'studio/outputs'/name;alias.parent.mkdir(parents=True);alias.symlink_to(book,target_is_directory=True)
    return name,book,ep


def test_historical_edition_resolves_original_directory_before_runner(tmp_path,monkeypatch):
    name,book,ep=historical(tmp_path);root=tmp_path/'studio';seen=[]
    before={str(p):p.read_bytes() for p in ep.rglob('*.json')}
    monkeypatch.setattr(production,'runner_for',lambda code_root,novel,*a,**k:seen.append(novel) or SimpleNamespace())
    monkeypatch.setattr('novel_manga.interactive.agent.run',lambda *a,**k:pytest.fail('existing status must not render'))
    monkeypatch.setattr('novel_manga.interactive.delivery.finish_reviewed_delivery',lambda *a,**k:{'status':'candidate'})
    assert production.run_episode(root,'sd25',project='suspense',edition=name)['status']=='candidate'
    assert seen==[book]
    assert history.generation_counts(ep)=={'a':3}
    assert {str(p):p.read_bytes() for p in ep.rglob('*.json')}==before


def cli(monkeypatch,root,args):
    monkeypatch.setattr(sys,'path',list(sys.path))
    spec=importlib.util.spec_from_file_location('unified_studio_cli',ROOT/'scripts/interactive_video.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',root);monkeypatch.setattr(sys,'argv',['interactive_video.py',*args])
    return module.main()


@pytest.mark.parametrize('project',['adventure','romance','suspense'])
def test_package_creation_entry_calls_single_author_and_preserves_resume(tmp_path,monkeypatch,project):
    task=tmp_path/'task.json';atomic_write_json(task,{'mode':'adaptation','project':project,'brief':'Adapt this complete package','source_archive':'source.zip'})
    config=tmp_path/'creator.json';atomic_write_json(config,{'backend':'codex','model':'selected'})
    feedback=tmp_path/'feedback.md';feedback.write_text('Keep approved facts')
    output=tmp_path/'author';seen=[]
    def author(path,folder,**kw):
        assert read_json(path)['project']==project
        seen.append((path,folder,kw));return {'status':'creative_candidate','new_video_generations':0}
    monkeypatch.setattr(creation,'run',author)
    monkeypatch.setattr(production,'run_episode',lambda *a,**k:pytest.fail('creation cannot submit production'))
    monkeypatch.setattr('novel_manga.interactive.preparation.prepare_episode',lambda *a,**k:pytest.fail('package creation must not enter legacy preparation'))
    assert cli(monkeypatch,tmp_path,['create','--task',str(task),'--output-dir',str(output),
                   '--config',str(config),'--resume','--feedback',str(feedback)])==0
    assert seen==[(task,output,{'resume':True,'feedback':feedback,'config':{'backend':'codex','model':'selected'}})]


def test_status_reads_original_history_without_repair_or_media(tmp_path,monkeypatch,capsys):
    name,book,ep=historical(tmp_path);before={str(p):p.read_bytes() for p in ep.rglob('*.json')}
    monkeypatch.setattr(production,'runner_for',lambda *a,**k:pytest.fail('status must not construct renderer'))
    assert cli(monkeypatch,tmp_path/'studio',['status','--project','suspense','--edition',name])==0
    result=json.loads(capsys.readouterr().out)
    assert result['episode_dir']==str(ep) and result['generation_counts']=={'a':3}
    assert result['status']=='candidate' and result['final_review_passed'] is False
    assert result['final_video']==str(book/'reviewed.mp4')
    assert {str(p):p.read_bytes() for p in ep.rglob('*.json')}==before


def test_edition_from_another_project_cannot_be_resumed(tmp_path):
    name,book,ep=historical(tmp_path)
    with pytest.raises(ValueError,match='project'):
        production.edition_directory(tmp_path/'studio','romance',name)


def test_resuming_custom_delivery_keeps_original_export_versions(tmp_path,monkeypatch):
    name,book,ep=historical(tmp_path);base=tmp_path/'old-delivery/backrooms';movie=base/'versions/export-010/episode01.mp4'
    movie.parent.mkdir(parents=True);movie.write_bytes(b'reviewed movie')
    report=read_json(ep/'thin_media_report.json');report['final_cut_review']['movie']=str(movie)
    atomic_write_json(ep/'thin_media_report.json',report);atomic_write_json(ep/'final_cut_review.json',report['final_cut_review'])
    atomic_write_json(base/'delivery.json',{'final_video':str(movie),'status':'candidate'})
    seen=[]
    monkeypatch.setattr(production,'runner_for',lambda *a,**k:SimpleNamespace())
    monkeypatch.setattr('novel_manga.interactive.delivery.finish_reviewed_delivery',lambda runner,report,destination:seen.append(destination) or {'status':'candidate'})
    production.run_episode(tmp_path/'studio','sd25',project='suspense',edition=name)
    assert seen==[base] and history.generation_counts(ep)=={'a':3}


def test_unreviewed_legacy_edition_does_not_report_zero_final_findings(tmp_path):
    name,book,ep=historical(tmp_path)
    (ep/'final_cut_review.json').unlink()
    report=read_json(ep/'thin_media_report.json');report.pop('final_cut_review')
    atomic_write_json(ep/'thin_media_report.json',report)
    result=production.edition_status(tmp_path/'studio','suspense',name)
    assert result['final_review_complete'] is None and result['remaining_findings'] is None
    assert result['generation_counts']=={'a':3} and result['status']=='candidate'
