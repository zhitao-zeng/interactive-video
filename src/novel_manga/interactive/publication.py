"""Publish accepted delivery receipts to an explicitly selected ModelScope dataset."""
from __future__ import annotations
import fcntl
import json
from pathlib import Path
import subprocess
from urllib.parse import quote

from novel_manga.util import atomic_write_json, read_json


def source(path):
    import hashlib
    stat=path.stat()
    return {'path':str(path),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def probe(movie):
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type',
        '-of','json',str(movie)],check=True,capture_output=True,text=True)
    data=json.loads(result.stdout)
    if not {'audio','video'}.issubset({s['codec_type'] for s in data['streams']}):
        raise ValueError('Delivery needs video and audio')
    return float(data['format']['duration'])


def publish_deliveries(root, spec, state_path, api):
    root=Path(root).resolve(); state_path=Path(state_path); run=state_path.parent
    run.mkdir(parents=True,exist_ok=True)
    with (run/'publish.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        repo=spec['repository']; stage=run/'stage'; stage.mkdir(exist_ok=True)
        frozen=run/'spec.json'
        if frozen.exists() and read_json(frozen)!=spec: raise ValueError('Keep the frozen publication target')
        if not frozen.exists(): atomic_write_json(frozen,spec)
        saved=read_json(state_path,{'uploads':{}}); rows=[]; media=[]
        saved['status']='verifying';atomic_write_json(state_path,saved)
        for item in spec['episodes']:
            name=item['edition']
            if Path(name).name!=name: raise ValueError('Invalid episode name')
            receipt_path=Path(item['receipt'])
            if not receipt_path.is_absolute():receipt_path=root/receipt_path
            receipt_path=receipt_path.resolve()
            receipt=read_json(receipt_path,{})
            if not (receipt.get('status')=='accepted' and receipt.get('technical_passed') is True
                    and receipt.get('content_passed') is True): continue
            movie=Path(receipt['final_video']).resolve(); cover=movie.parent/'cover.jpeg'
            if not movie.is_relative_to(root/'delivery'): raise ValueError('Publish only delivery exports')
            if not movie.is_file() or not cover.is_file() or not movie.stat().st_size or not cover.stat().st_size:
                raise ValueError('Accepted export or matching cover is missing')
            frozen_export=receipt.get('accepted_export') or {}
            if receipt.get('acceptance_protocol')!='current-export-v1' or not frozen_export:
                continue
            episode_dir=receipt.get('episode_dir');run_dir=receipt.get('run_dir')
            if not episode_dir or not run_dir:
                continue
            delivery_link=Path(run_dir)/'interactive-delivery.json'
            if not delivery_link.is_file():
                continue
            destination=read_json(delivery_link,{}).get('destination')
            if not isinstance(destination,str) or not destination:
                continue
            if (Path(destination)/'delivery.json').resolve()!=receipt_path:
                continue
            from novel_manga.review.acceptance import acceptance_status
            current=acceptance_status(episode_dir,run_dir)
            if not (current.get('passed') is True
                    and (current.get('final_visual') or {}).get('movie')==str(movie)):
                continue
            actual={key:source(path)['sha256'] for key,path in
                    [('video_sha256',movie),('cover_sha256',cover)]}
            if any(frozen_export.get(key)!=value for key,value in actual.items()):
                raise ValueError('Accepted export SHA256 mismatch')
            seconds=probe(movie)
            if abs(seconds-float(receipt['duration']))>.2:raise ValueError('Delivery duration mismatch')
            row={'episode':name,'title':item.get('title',name),'seconds':round(seconds,3),
                 'file':f'episodes/{name}.mp4','cover':f'covers/{name}.jpeg'}
            for path,original in [(row['file'],movie),(row['cover'],cover)]:
                media.append((path,original,source(original)))
            credits=[]
            for cue in (receipt.get('music_selection') or {}).get('cues',[]):
                asset=cue.get('asset') or {}
                credit={k:asset[k] for k in ('title','artist','license_id','license_url','source_url') if asset.get(k)}
                if credit and credit not in credits:credits.append(credit)
            if credits:
                row['music_credits']=f'credits/{name}.json'
                target=stage/row['music_credits']
                atomic_write_json(target,{'tracks':credits,'edits':'节选、调节音量、淡入淡出；部分片段循环。'})
            rows.append(row)
        if not rows:
            saved.update(status='waiting_for_acceptance',verified_episodes=0)
            atomic_write_json(state_path,saved);return saved
        if not api.repo_exists(repo,'dataset'):
            api.create_repo(repo,'dataset',visibility=spec.get('visibility','private'),license='other',
                chinese_name=spec.get('title','剧集'),description='剧集视频、封面与分集索引。')
        def inventory():return {f.path:int(f.size) for f in api.list_repo_files(repo,'dataset')}
        remote=inventory()
        for path,original,info in media:
            target=stage/path;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists() or not target.samefile(original):
                target.unlink(missing_ok=True);target.hardlink_to(original)
            if saved['uploads'].get(path)!=info:
                intent=saved.get('inflight') or {}
                reconciled=False
                if intent.get('path')==path and intent.get('source')==info and remote.get(path)!=info['bytes']:
                    raise ValueError('Remote upload outcome pending; wait for inventory before resubmission')
                if intent.get('path')==path and intent.get('source')==info and remote.get(path)==info['bytes']:
                    if intent.get('remote_bytes')!=info['bytes']:
                        reconciled=True
                    else:
                        # A same-size replacement is ambiguous from inventory.
                        # Compare actual remote bytes only for this interrupted case.
                        import filecmp
                        downloaded=Path(api.download_file(repo,'dataset',path,local_dir=run/'reconcile',force=True))
                        reconciled=filecmp.cmp(downloaded,original,shallow=False)
                if reconciled:
                    saved['uploads'][path]=info;saved.pop('inflight',None)
                    atomic_write_json(state_path,saved);continue
                saved['inflight']={'path':path,'source':info,'remote_bytes':remote.get(path)}
                atomic_write_json(state_path,saved)
                api.upload_folder(repo,'dataset',stage,allow_patterns=[path],commit_message='收录剧集资源',
                    max_workers=2,disable_tqdm=True,sync_remote_repo=False)
                if source(original)!=info:raise ValueError('Delivery changed during upload')
                # Save successful uploads before eventual inventory verification.
                # An index delay must not cause a second large upload on restart.
                saved['uploads'][path]=info;saved.pop('inflight',None);atomic_write_json(state_path,saved)
        remote=inventory()
        if any(remote.get(path)!=info['bytes'] for path,_,info in media):
            raise ValueError('Remote inventory has missing media')
        index={'title':spec.get('title','剧集'),'total_episodes':len(spec['episodes']),
               'uploaded_episodes':len(rows),'episodes':rows}
        atomic_write_json(stage/'episodes.json',index)
        lines=['---','license: other','language:','- zh','---','',f'# {index["title"]}', '',
               '| 集号 | 剧名 | 视频 | 封面 |','|---|---|---|---|']
        for row in rows:
            lines.append(f'| {row["episode"]} | {row["title"]} | [播放 / 下载]({quote(row["file"],safe="/")}) | [封面]({quote(row["cover"],safe="/")}) |')
        if any(row.get('music_credits') for row in rows):lines+=['','配乐署名见对应的 `credits/` 文件。']
        (stage/'README.md').write_text('\n'.join(lines)+'\n')
        if saved.get('verified_index')!=index:
            paths=['README.md','episodes.json']+[r['music_credits'] for r in rows if r.get('music_credits')]
            api.upload_folder(repo,'dataset',stage,allow_patterns=paths,commit_message='更新剧集索引',
                max_workers=2,disable_tqdm=True,sync_remote_repo=False)
        downloaded=Path(api.download_file(repo,'dataset','episodes.json',local_dir=run/'remote-index',force=True))
        if read_json(downloaded)!=index:raise ValueError('Remote episode index differs')
        saved.update(status='complete' if len(rows)==len(spec['episodes']) else 'waiting_for_acceptance',
                     verified_index=index,verified_episodes=len(rows),repository=repo)
        atomic_write_json(state_path,saved);return saved
