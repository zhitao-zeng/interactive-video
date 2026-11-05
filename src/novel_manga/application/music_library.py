"""Register a supplied library while retaining its files, IDs and trial history."""
from pathlib import Path
import json
from zipfile import ZipFile
import re
import shutil
import subprocess

from novel_manga.util import atomic_write_json,read_json


def import_archive(archive,destination):
    archive=Path(archive).resolve();destination=Path(destination).resolve()
    info=archive.stat();state=[info.st_size,info.st_mtime_ns]
    receipt=destination/'import.json'
    if receipt.exists():
        previous=read_json(receipt,{})
        if previous.get('archive')!=str(archive)or previous.get('archive_state')!=state:
            raise ValueError('Keep the existing library; import a changed archive into a new directory')
        library=Path(previous['library_dir'])
    else:
        if destination.exists():raise ValueError('Library destination already exists without an import receipt; preserve its files')
        with ZipFile(archive)as z:
            catalogs=[f for f in z.namelist()if f.endswith('/catalog.json')and not f.startswith('__MACOSX/')]
            if len(catalogs)!=1:raise ValueError('Library archive requires one catalog.json')
            prefix=Path(catalogs[0]).parent
            destination.mkdir(parents=True)
            (destination/'owner').write_text('Interactive studio / supplied music library; original workbook, audio, attribution and trials retained\n')
            for name in z.namelist():
                if not Path(name).is_relative_to(prefix):continue
                if not(destination/name).resolve().is_relative_to(destination):raise ValueError('Archive path leaves library directory')
                z.extract(name,destination)
        library=destination/prefix
    catalog=read_json(library/'catalog.json',{});ledger=read_json(library/'维护记录.json',{})
    tracks=[]
    for row in catalog['tracks']:
        audio=library/row['local_file'];credit=library/row['credit_file']
        if not audio.is_file()or not credit.is_file():raise ValueError('Library audio/attribution file is missing')
        tracks.append({'song_id':row['id'],'title':row['title'],'artist':row['artist'],
            'local_audio':str(audio),'source_url':row.get('source_url'),'license_url':row.get('license_url'),
            'license_id':row.get('license'),'attribution':row['attribution'],'credit_file':str(credit),
            'scene':row.get('scene'),'subscene':row.get('subscene'),'scenarios':[v for v in(row.get('scene'),row.get('subscene'))if v],
            'advice':row.get('advice'),'dialogue_advice':row.get('dialogue_advice'),'intensity':row.get('intensity'),
            'fit_status':row.get('fit_status'),'feedback':row.get('feedback',''),
            'trial_history':[t for t in ledger.get('trials',[])if t['track_id']==row['id']],
            'source_record':row,'source_trial_ledger':str(library/'维护记录.json'),
            'usage_policy':{'requires_visible_attribution':True},
            'source_catalog':str(library/'catalog.json'),'review_status':'unreviewed'})
    result={'archive':str(archive),'archive_state':state,'library_dir':str(library),
        'source_version':catalog.get('version'),'tracks':tracks,'original_trials':ledger.get('trials',[])}
    atomic_write_json(receipt,result);return result


def merge_catalog(target,tracks):
    target=Path(target);rows=read_json(target,[]);by_id={r['song_id']:i for i,r in enumerate(rows)}
    for row in tracks:
        if row['song_id']in by_id:
            i=by_id[row['song_id']]
            if rows[i]['local_audio']!=row['local_audio']:raise ValueError('Same music id points to a different original audio file')
            rows[i]={**rows[i],**row}
        else:by_id[row['song_id']]=len(rows);rows.append(row)
    atomic_write_json(target,rows);return rows


def register(root,archive,destination,work):
    """Import, sample-analyse and index using the existing isolated CPU runtime."""
    from novel_manga.application.music_selection import configuration,local_index
    from novel_manga.util import media_duration,run
    root=Path(root).resolve();work=Path(work).resolve();work.mkdir(parents=True,exist_ok=True)
    (work/'owner').write_text('Interactive studio / user-authorized supplied music registration; preserve originals/trials; CPU only\n')
    config=configuration(root);result=import_archive(archive,destination);tracks=result['tracks']
    worker=root/'scripts/music_library_worker.py';analysis=Path(result['library_dir']).parent/'analysis';analysis.mkdir(exist_ok=True)
    batches=[[]for _ in range(3)]
    for i,row in enumerate(tracks):
        duration=media_duration(Path(row['local_audio']));starts=[0,max(0,duration/2-3),max(0,duration-6)]
        if duration<6:raise ValueError('Music audit requires at least six seconds of real audio')
        sample=analysis/(row['song_id']+'-sample.wav');windows=[{'start':round(s,6),'end':round(s+6,6)}for s in starts]
        if not sample.is_file():
            graph=[f'[0:a]atrim=start={s:.6f}:duration=6,asetpts=PTS-STARTPTS[a{n}]'for n,s in enumerate(starts)]
            graph.append('[a0][a1][a2]concat=n=3:v=0:a=1[a]')
            run(['ffmpeg','-nostdin','-y','-v','error','-i',row['local_audio'],'-filter_complex',';'.join(graph),'-map','[a]','-ar','16000','-ac','1',str(sample)])
        report=analysis/(row['song_id']+'-audio.json')
        row.update(duration=duration,audio_audit_sample=str(sample),audio_audit_report=str(report),
            audio_audit_source_windows=windows,vocal_evidence_scope='18s from three6s source windows; not full-track or film acceptance')
        batches[i%3].append({'id':row['song_id'],'audio':str(sample),'output':str(report),'source_windows':windows,
            'question':'These are three six-second samples from start, middle and end of one music track. Describe actual audible instrumentation and human singing, wordless vocals or spoken words. Respond as JSON with has_human_vocals boolean, uncertain boolean, audible_instruments array and description. Do not infer visuals or dramatic suitability; samples do not certify the whole song.'})
    processes=[];logs=[]
    try:
        for i,jobs in enumerate(batches):
            jobs=[j for j in jobs if not Path(j['output']).is_file()]
            if not jobs:continue
            job=work/f'audio-job-{i}.json';atomic_write_json(job,{'action':'audio','runtime':config,'jobs':jobs})
            log=(work/f'audio-{i}.log').open('a');logs.append(log)
            processes.append(subprocess.Popen([config['python'],str(worker),str(job)],stdout=log,stderr=subprocess.STDOUT))
        failed=[p.wait()for p in processes]
        if any(failed):raise RuntimeError('Music sample analysis failed; original library and logs retained')
    finally:
        for p in processes:
            if p.poll()is None:p.terminate();p.wait()
        for log in logs:log.close()
    for row in tracks:
        report=read_json(Path(row['audio_audit_report']),{});response=report.get('response','')
        match=re.search(r'\{[\s\S]*\}',str(response))
        try:parsed=json.loads(match[0])if match else{}
        except ValueError:parsed={}
        row['audio_audit']=parsed # Raw model response remains in the evidence file, including uncertainty.
    job=work/'index-job.json';output=work/'index-result.json'
    atomic_write_json(job,{'action':'index','runtime':config,'tracks':tracks,'output':str(output)})
    with local_index(config,work),(work/'index.log').open('a')as log:
        done=subprocess.run([config['python'],str(worker),str(job)],stdout=log,stderr=subprocess.STDOUT)
        if done.returncode:raise RuntimeError('Music index failed; library and logs retained, active catalogue not changed')
    target=root/'.runtime/music-selection/catalog.json'
    if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(config['catalog'],target)
    combined=merge_catalog(target,tracks)
    config.update(catalog=str(target),catalogue_scope=f'{len(combined)} local tracks; original IDs/attribution/trials retained; sampled audio evidence; popularity not verified')
    config_file=root/read_json(root/'configs/interactive.json',{}).get('services',{}).get('music_selection_config','.runtime/music-selection/runtime.json')
    atomic_write_json(config_file,config)
    result.update(tracks=tracks,total_registered=len(combined),index=read_json(output),analysis_scope='18seconds sampled per track, not full-song listening/acceptance')
    atomic_write_json(work/'result.json',result);return result
