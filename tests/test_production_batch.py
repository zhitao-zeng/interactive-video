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


