"""Observed host compatibility failures eligible for one existing-candidate continuation."""
import json,os
from .input_effects import _phase_groups

SOURCE_REVIEW_POLICY=3


def asset_image_recoverable(record):
    """Resume an unchanged source-approved picture failure, not its text allowance."""
    return (record.get('status') in {'rejected', 'incomplete'}
            and record.get('step') in {'character_assets', 'draft_assets', 'assets'}
            and not record.get('published') and bool(record.get('decision'))
            and (record.get('source') or {}).get('consistent') is True
            and not (record.get('source') or {}).get('problems')
            and not (record.get('asset_image_review') or {}).get('exhausted')
            and 'generated asset failed image review' in str(record.get('error', '')))

def asset_configuration_recoverable(ctx, record):
    """One unchanged continuation for the observed pre-request image-port failure."""
    if (record.get('status') != 'incomplete' or record.get('step') != 'assets'
            or record.get('published') or record.get('asset_configuration_recovery')
            or (record.get('source') or {}).get('consistent') is not True
            or 'image helper exited' not in str(record.get('error', ''))):
        return False
    names=set((_phase_groups(record.get('decision') or {}).get('characters') or {}))
    if not names:
        return False
    from novel_manga.llm.resources import _endpoint
    base=os.environ.get('NOVEL_LOCAL_QWEN_IMAGE_URL','')
    try:
        if base:_endpoint(base)
    except ValueError:
        return False
    log=ctx.run_dir/'input-assets.log'
    if not log.is_file():return False
    prefix=f'-agent-{ctx.episode_dir.name}-{record["clip_id"]}-'
    latest={}
    for line in log.read_text().splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if (isinstance(row,dict) and row.get('name') in names
                and prefix in str(row.get('asset_id',''))):
            latest[row['name']]=row
    return names==set(latest) and all(
        'model resource endpoint has an invalid port' in str(row.get('status',''))
        and row.get('seconds') == 0 for row in latest.values())


def source_review_recoverable(record):
    """Only a saved rejection from before defect confirmation needs rechecking."""
    return (record.get('status') == 'rejected' and record.get('step') == 'source'
            and bool(record.get('decision')) and not record.get('published')
            and int(record.get('source_review_policy', 1)) < SOURCE_REVIEW_POLICY)


def posture_protocol_recoverable(record):
    """One same-candidate continuation after replacing the roster-echo protocol."""
    error = str(record.get('error', ''))
    return (record.get('status') == 'incomplete' and record.get('step') == 'compile'
            and not record.get('published') and not record.get('posture_protocol_recovery')
            and bool(record.get('decision')) and (record.get('source') or {}).get('consistent') is True
            and '姿态台账镜' in error and '入镜名单不符' in error)



def first_frame_recoverable(ctx,record):
    """Continue once when the old host translated an opening awaiting replacement."""
    from novel_manga.media.first_frames import enabled, OPENING_FIELDS
    from novel_manga.util import read_json
    if (record.get('status')!='incomplete' or record.get('published')
            or record.get('first_frame_recovery')
            or (record.get('source')or{}).get('consistent')is not True
            or (record.get('source')or{}).get('problems')
            or not enabled(ctx)):
        return False
    opening_compile=(record.get('step')=='compile' and
        'input correction did not reach the compiled request'in str(record.get('error','')))
    translation=(str(record.get('step','')).startswith('translate:') and
        'input repair H3 translation rejected'in str(record.get('error','')))
    if not (opening_compile or translation):return False
    cid=record.get('clip_id')
    clip=next((c for c in read_json(ctx.episode_dir/'clip_plan.json',{}).get('clips',[])if c['clip_id']==cid),{})
    if not opening_compile and not clip.get('first_frame'):return False
    shots={s['index']:s for s in read_json(ctx.episode_dir/'chapter_script.json',{}).get('shots',[])}
    opening=(clip.get('shot_indexes')or[None])[0]
    fields=('opening_frame',)if opening_compile else OPENING_FIELDS
    return any(p.get('index')==opening and any(k in p and p[k]!=shots.get(opening,{}).get(k)
        for k in fields) for p in (record.get('decision')or{}).get('shots',[]))
