import sys
import json
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json
from novel_manga.interactive import batch


def test_batch_resumes_retryable_worker_without_restarting_completed_neighbors(tmp_path):
    spec = {'parallel': 2, 'max_launches': 3, 'episodes': [
        {'edition': name, 'task': str(tmp_path/(name+'.json')), 'lane': 'h3'}
        for name in ('good', 'retry', 'blocked')]}
    for item in spec['episodes']:
        atomic_write_json(Path(item['task']), {'mode': 'original', 'brief': item['edition']})
    path = tmp_path/'batch.json'; atomic_write_json(path, spec)
    worker = tmp_path/'worker.py'
    worker.write_text('''import json,sys,pathlib
run=pathlib.Path(sys.argv[1]); name=sys.argv[2]
log=run/'calls'; n=int(log.read_text())+1 if log.exists() else 1; log.write_text(str(n))
result={'status':'accepted' if name=='good' or name=='retry' and n==2 else 'blocked',
        'retryable': name=='retry' and n==1}
(run/'state.json').write_text(json.dumps(result))
''')
    def command(root, item, directory, resume):
        return [sys.executable, str(worker), str(directory), item['edition']]
    result = batch.run(tmp_path, path, tmp_path/'run', command=command, poll_seconds=.01, retry_seconds=0)
    assert result['status'] == 'blocked'
    assert [result['episodes'][name]['launches'] for name in ('good','retry','blocked')] == [1,2,1]
    assert result['episodes']['retry']['result']['status'] == 'accepted'
    assert batch.run(tmp_path, path, tmp_path/'run', command=command)['episodes'] == result['episodes']
    resumed=batch.run(tmp_path,path,tmp_path/'run',command=command,resume=True,poll_seconds=.01,retry_seconds=0)
    assert resumed['episodes']['good']['launches']==1
    assert resumed['episodes']['blocked']['launches']==2
    assert resumed['episodes']['blocked']['history'][0]['result']['status']=='blocked'


def test_batch_keeps_interrupted_launch_and_reconciles_its_completed_film(tmp_path):
    task=tmp_path/'task.json'; atomic_write_json(task, {'brief':'original'})
    spec={'episodes':[{'edition':'one','task':str(task),'lane':'h3'}]}
    path=tmp_path/'batch.json'; atomic_write_json(path,spec)
    run=tmp_path/'run'
    atomic_write_json(run/'manifest.json',spec)
    atomic_write_json(run/'state.json',{'episodes':{'one':{'status':'running','launches':1}}})
    atomic_write_json(run/'one/state.json',{'status':'accepted'})
    result=batch.run(tmp_path,path,run,command=lambda *a: (_ for _ in ()).throw(AssertionError('no duplicate')))
    assert result['episodes']['one']['launches']==1
    assert result['status']=='accepted'


import pytest


@pytest.mark.parametrize('supervisor_status',['completed','running'])
def test_explicit_cold_resume_keeps_lifetime_launches_after_previous_cycle_exhausted(tmp_path,supervisor_status):
    task=tmp_path/'task.json';atomic_write_json(task,{'brief':'original'})
    spec={'max_launches':1,'episodes':[{'edition':'one','task':str(task)}]}
    path=tmp_path/'batch.json';atomic_write_json(path,spec);run=tmp_path/'run'
    atomic_write_json(run/'manifest.json',spec)
    atomic_write_json(run/'state.json',{'episodes':{'one':{'status':supervisor_status,'launches':1,
        'result':{'status':'blocked','retryable':False,'reason':'old host defect'}}}})
    atomic_write_json(run/'one/state.json',{'status':'blocked','remaining_seconds':17})
    worker=tmp_path/'worker.py';worker.write_text('''import pathlib,json,sys
p=pathlib.Path(sys.argv[1]);saved=json.loads(p.read_text())
assert saved['remaining_seconds']==17
p.write_text(json.dumps({'status':'accepted','remaining_seconds':17}))
''')
    result=batch.run(tmp_path,path,run,resume=True,poll_seconds=.01,
        command=lambda root,item,d,resume:[sys.executable,str(worker),str(d/'state.json')])
    row=result['episodes']['one']
    assert row['launches']==2 and row['history'][0]['launch']==1
    assert row['result']['remaining_seconds']==17 and result['status']=='accepted'


def test_live_worker_keeps_batch_lease_after_supervisor_is_killed(tmp_path):
    import subprocess,time
    import pytest
    task=tmp_path/'task.json';atomic_write_json(task,{'brief':'original'})
    spec={'episodes':[{'edition':'one','task':str(task)}]}
    path=tmp_path/'batch.json';atomic_write_json(path,spec)
    worker=tmp_path/'worker.py'
    worker.write_text('''import pathlib,sys,time,json
run=pathlib.Path(sys.argv[1]);(run/'started').write_text('started')
time.sleep(1)
(run/'state.json').write_text(json.dumps({'status':'accepted'}))
''')
    driver=tmp_path/'driver.py'
    driver.write_text('''from novel_manga.interactive.batch import run
from pathlib import Path
import sys
base=Path(sys.argv[1])
run(base,base/'batch.json',base/'run',poll_seconds=.01,
    command=lambda root,item,directory,resume:[sys.executable,str(base/'worker.py'),str(directory)])
''')
    parent=subprocess.Popen([sys.executable,str(driver),str(tmp_path)])
    try:
        deadline=time.monotonic()+5
        while not (tmp_path/'run/one/started').exists() and time.monotonic()<deadline:time.sleep(.02)
        assert (tmp_path/'run/one/started').exists()
        parent.kill();parent.wait(timeout=5)
        with pytest.raises(BlockingIOError):batch.run(tmp_path,path,tmp_path/'run')
        while not (tmp_path/'run/one/state.json').exists() and time.monotonic()<deadline:time.sleep(.02)
        time.sleep(.05)
        result=batch.run(tmp_path,path,tmp_path/'run',command=lambda *a:pytest.fail('duplicate worker'))
        assert result['status']=='accepted' and result['episodes']['one']['launches']==1
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=5)
def test_killed_creator_charges_interrupted_slice_instead_of_renewing_it():
    from novel_manga.interactive.creation import spent_seconds
    saved={'seconds_used':200,'inflight':{'started_at':1000,'timeout':1200}}
    assert spent_seconds(saved,200,1100)==300
    assert spent_seconds(saved,200,9000)==1400
    assert spent_seconds(saved,1500,1100)==1500


def test_batch_worker_preserves_lane_selected_by_task(tmp_path):
    command=batch.produce_command(tmp_path,{'edition':'one'},tmp_path/'one',False)
    assert '--lane' not in command


def test_scoped_resume_preserves_unselected_blocked_episode_and_then_resumes_it(tmp_path):
    import copy
    names=('canary','later')
    spec={'max_launches':1,'episodes':[{'edition':name,'task':str(tmp_path/(name+'.json'))} for name in names]}
    path=tmp_path/'batch.json';atomic_write_json(path,spec);run=tmp_path/'run'
    atomic_write_json(run/'manifest.json',spec)
    rows={name:{'status':'completed','launches':3,'cycle_start':2,
        'result':{'status':'blocked','reason':'same old host defect'},
        'history':[{'launch':2,'result':{'status':'blocked'}}]} for name in names}
    original=copy.deepcopy(rows)
    atomic_write_json(run/'state.json',{'episodes':rows})
    for name in names:
        atomic_write_json(tmp_path/(name+'.json'),{'brief':name})
        atomic_write_json(run/name/'state.json',{'status':'blocked','remaining_seconds':17})
    worker=tmp_path/'worker.py';worker.write_text('''import pathlib,json,sys
p=pathlib.Path(sys.argv[1]);saved=json.loads(p.read_text())
assert saved['remaining_seconds']==17
p.write_text(json.dumps({'status':'accepted','remaining_seconds':17}))
''')
    launched=[]
    def command(root,item,directory,resume):
        assert resume
        launched.append(item['edition'])
        return [sys.executable,str(worker),str(directory/'state.json')]
    result=batch.run(tmp_path,path,run,resume=True,editions=['canary'],command=command,poll_seconds=.01)
    assert launched==['canary']
    assert result['episodes']['later']==original['later']
    assert result['episodes']['canary']['launches']==4
    assert result['episodes']['canary']['history'][-1]['launch']==3
    assert result['status']=='blocked'
    assert read_json(run/'manifest.json')==spec
    result=batch.run(tmp_path,path,run,resume=True,command=command,poll_seconds=.01)
    assert launched==['canary','later']
    assert result['episodes']['canary']['launches']==4
    assert result['episodes']['later']['launches']==4
    assert result['status']=='accepted'


def test_scoped_first_launch_leaves_other_entries_pending(tmp_path):
    spec={'episodes':[{'edition':name,'task':str(tmp_path/(name+'.json'))} for name in ('one','later')]}
    for item in spec['episodes']:atomic_write_json(Path(item['task']),{'brief':item['edition']})
    path=tmp_path/'batch.json';atomic_write_json(path,spec)
    worker=tmp_path/'worker.py';worker.write_text('''import pathlib,sys
pathlib.Path(sys.argv[1]).write_text('{"status":"accepted"}')
''')
    def command(root,item,directory,resume):
        assert item['edition']=='one' and not resume
        return [sys.executable,str(worker),str(directory/'state.json')]
    result=batch.run(tmp_path,path,tmp_path/'run',editions=['one'],command=command,poll_seconds=.01)
    assert result['episodes']['later']=={'status':'pending','launches':0}
    assert result['episodes']['one']['result']['status']=='accepted'
    assert result['status']=='blocked'


def test_scoped_batch_rejects_unknown_edition_before_writing(tmp_path):
    path=tmp_path/'batch.json';atomic_write_json(path,{'episodes':[{'edition':'one','task':'task.json'}]})
    with pytest.raises(ValueError,match='Selected editions'):
        batch.run(tmp_path,path,tmp_path/'run',editions=['typo'])
    assert not (tmp_path/'run').exists()


def test_batch_cli_passes_repeatable_edition_scope(monkeypatch,tmp_path):
    import runpy
    seen={}
    def run(root,spec,directory,**kwargs):
        seen.update(kwargs)
        return {'status':'accepted'}
    monkeypatch.setattr(batch,'run',run)
    monkeypatch.setattr(sys,'argv',['interactive_video.py','produce-batch','--batch',str(tmp_path/'batch.json'),
        '--output-dir',str(tmp_path/'run'),'--resume','--edition','one','--edition','two'])
    script=Path(__file__).resolve().parents[1]/'scripts/interactive_video.py'
    namespace=runpy.run_path(str(script))
    assert namespace['main']()==0
    assert seen=={'resume':True,'editions':['one','two']}


