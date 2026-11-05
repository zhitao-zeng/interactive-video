"""Optional request batch: owned subprocesses, direct wait, per-request receipts.

python tools/batch.py requests/*.json --workers 3 --report output/batch-01.json
This calls media.py; the Agent still chooses requests, concurrency and follow-up.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def batch(requests, workers, report, timeout=1800, command=None):
    report=Path(report).resolve()
    if report.exists():raise FileExistsError('Keep prior report; choose a new report path.')
    report.parent.mkdir(parents=True,exist_ok=True)
    logs=report.with_suffix('.logs');logs.mkdir(exist_ok=True)
    command=command or [sys.executable,str(Path(__file__).with_name('media.py'))]
    rows=[];started=time.time()
    def one(index, filename):
        request=Path(filename).resolve();log=logs/f'{index:03d}-{request.stem}.log'
        row={'request':str(request),'log':str(log),'started_at':time.time()}
        deadline=os.getenv('PI_DEADLINE_EPOCH')
        if deadline and time.time()>=float(deadline):return {**row,'status':'deadline_before_dispatch'}
        with log.open('w')as stream:
            process=subprocess.Popen([*command,str(request)],stdout=stream,stderr=subprocess.STDOUT)
            row['pid']=process.pid
            try:
                code=process.wait(timeout=timeout)
                row.update(status='completed'if code==0 else'failed',returncode=code)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                row.update(status='client_timeout',note='Keep media task receipt; resume the same request to query/download.')
        row['elapsed_seconds']=round(time.time()-row['started_at'],3)
        if row['status']!='completed':row['error_tail']=log.read_text(errors='replace')[-1600:]
        return row
    with ThreadPoolExecutor(max_workers=workers)as pool:
        futures=[pool.submit(one,i,name)for i,name in enumerate(requests)]
        for future in as_completed(futures):
            row=future.result();rows.append(row)
            snapshot={'started_at':started,'elapsed_seconds':round(time.time()-started,3),'results':rows}
            temp=report.with_suffix(report.suffix+'.partial');temp.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n');temp.replace(report)
            print(json.dumps({k:row[k]for k in ('request','status','log')},ensure_ascii=False),flush=True)
    return snapshot


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('requests',nargs='+')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--report',required=True)
    parser.add_argument('--timeout',type=float,default=1800)
    args=parser.parse_args()
    if args.workers<1:parser.error('workers must be positive')
    result=batch(args.requests,args.workers,args.report,args.timeout)
    sys.exit(0 if all(row['status']=='completed'for row in result['results'])else 1)
