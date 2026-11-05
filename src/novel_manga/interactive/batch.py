"""Bounded subprocess supervision of the ordinary frozen produce entry."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

from novel_manga.util import atomic_write_json, read_json


def produce_command(root, item, directory, resume):
    command = [sys.executable, str(Path(root)/'scripts/interactive_video.py'), 'produce',
               '--task', str(directory/'source-task.json'), '--output-dir', str(directory),
               '--edition', item['edition']]
    if item.get('lane'):
        command += ['--lane',item['lane']]
    if item.get('config'):
        command += ['--config', str(directory/'agent-config.json')]
    if resume:
        command.append('--resume')
    return command


def run(root, spec_path, directory, *, command=produce_command, poll_seconds=2, retry_seconds=30, resume=False):
    root=Path(root).resolve(); directory=Path(directory).resolve()
    spec=read_json(Path(spec_path))
    items=spec['episodes']; names=[item['edition'] for item in items]
    if not items or len(set(names))!=len(names) or any(Path(name).name!=name for name in names):
        raise ValueError('Batch needs unique edition directory names')
    parallel=int(spec.get('parallel', 3)); maximum=int(spec.get('max_launches', 3))
    if parallel<1 or maximum<1: raise ValueError('parallel and max_launches must be positive')
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'owner').write_text('Official produce-batch supervisor / preserve all runs and budgets\n')
    with (directory/'batch.lock').open('a') as lock:
        # Children retain this lock if the supervisor dies. A restarting service
        # waits for their normal receipt instead of launching duplicate workers.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest=directory/'manifest.json'
        if manifest.exists() and read_json(manifest)!=spec:
            raise ValueError('Keep the frozen batch manifest; changed inputs need a new batch')
        if not manifest.exists(): atomic_write_json(manifest,spec)
        state=read_json(directory/'state.json', {'episodes': {}})
        active={}; ready={}
        for item in items:
            name=item['edition']; run_dir=directory/name; run_dir.mkdir(exist_ok=True)
            row=state['episodes'].setdefault(name, {'status':'pending','launches':0})
            if resume and row['status']=='completed' and row.get('result',{}).get('status')=='blocked' and row['launches']<maximum:
                row['status']='pending'
            for key, filename in [('task','source-task.json'),('config','agent-config.json')]:
                if not item.get(key): continue
                source=Path(item[key]); source=source if source.is_absolute() else Path(spec_path).resolve().parent/source
                frozen=run_dir/filename
                value=read_json(source)
                if frozen.exists() and read_json(frozen)!=value:
                    raise ValueError(f'Frozen {name} {key} changed')
                if not frozen.exists(): atomic_write_json(frozen,value)
            # A dead supervisor may have missed the worker's final result.
            if row['status']=='running':
                result=read_json(run_dir/'state.json',{})
                if result.get('status') in {'accepted','candidate','blocked'}:
                    row.update(status='completed',result=result)
                else:
                    row.update(status='completed',result={'status':'blocked','retryable':True,
                        'reason':'worker exited without a terminal receipt; resume the same frozen run'})
        def save(): atomic_write_json(directory/'state.json',state)
        def eligible(row):
            return (row['status']=='pending' or
                    row['status']=='completed' and row.get('result',{}).get('retryable') is True
                    and row['launches']<maximum)
        state['status']='running'; save()
        while True:
            for item in items:
                name=item['edition']; row=state['episodes'][name]
                if len(active)>=parallel: break
                if name in active or not eligible(row) or time.monotonic()<ready.get(name,0): continue
                run_dir=directory/name
                worker_resume=row['launches']>0 or (run_dir/'state.json').exists()
                if row.get('result'):
                    row.setdefault('history',[]).append({'launch':row['launches'],'result':row['result'],
                                                        'exit_code':row.get('exit_code')})
                row.update(status='running',launches=row['launches']+1)
                save()  # A restart cannot erase a launch or grant another budget.
                log=(run_dir/f'worker-{row["launches"]:02d}.log').open('ab')
                env={**os.environ,'NOVEL_PROJECT_ROOT':str(root)}
                try:
                    process=subprocess.Popen(command(root,item,run_dir,worker_resume),stdout=log,stderr=subprocess.STDOUT,
                                             cwd=root,env=env,pass_fds=(lock.fileno(),))
                finally: log.close()
                active[name]=process
            for name, process in list(active.items()):
                code=process.poll()
                if code is None: continue
                result=read_json(directory/name/'state.json',{})
                if result.get('status') not in {'accepted','candidate','blocked'}:
                    result={'status':'blocked','retryable':True,'reason':f'worker exit {code} without terminal receipt'}
                state['episodes'][name].update(status='completed',exit_code=code,result=result)
                del active[name]; ready[name]=time.monotonic()+retry_seconds; save()
            if not active and not any(eligible(row) for row in state['episodes'].values()): break
            time.sleep(poll_seconds)
        state['status']='accepted' if all(row['result'].get('status')=='accepted' for row in state['episodes'].values()) else 'blocked'
        save()
        if spec.get('publication'):
            publication={k:v for k,v in spec['publication'].items() if k!='python'}
            atomic_write_json(directory/'publication-spec.json',publication)
            with (directory/'publication.log').open('ab') as log:
                result=subprocess.run([spec['publication']['python'],str(root/'scripts/publish_deliveries.py'),
                    '--spec',str(directory/'publication-spec.json'),'--state',str(directory/'publication/state.json')],
                    cwd=root,stdout=log,stderr=subprocess.STDOUT,pass_fds=(lock.fileno(),))
            state['publication']=read_json(directory/'publication/state.json',{'status':'failed','exit_code':result.returncode})
            if result.returncode not in {0,2}:
                state['publication']={**state['publication'],'status':'failed','exit_code':result.returncode}
            if state['publication'].get('status')!='complete':state['status']='blocked'
            save()
        return state
