"""Select existing asset pixels for a single-view input; never render or alter budgets."""
from copy import deepcopy
from pathlib import Path
from PIL import Image
from novel_manga.llm import client
from novel_manga.review.endpoints import judge_settings
from novel_manga.application.profiles import h3_stamp
from novel_manga.repair.proposal import RepairProposal
from novel_manga.util import read_json, atomic_write_json

SCHEMA = client.obj({
    'multiple_views': {'type':'boolean'},
    'bounds': {'type':'array','items':{'type':'number','minimum':0,'maximum':1},'minItems':4,'maxItems':4},
    'reason': {'type':'string'},
})


def select_view(source, target, desired):
    answer=client.ask_json([client.image_part(source,1024),{'type':'text','text':
        '这是给视频模型的场景或道具参考图。判断它是否把多个独立视图拼在一起。'
        '若multiple_views=true，请选出符合当前镜头的一个完整单视图，返回归一化边界[left,top,right,bottom]，范围0到1。'
        '选择整块场景或完整主体，边界落在分隔线内侧，不能留下另一视图的小条。不要只截局部细节。不重新绘图。'
        '若本身是一个连续相机视图，multiple_views=false并返回[0,0,1,1]。'
        '不同房间相连、镜面与透视不算多个视图；上下/左右重复场景与道具多角度设计图算。'
        '当前镜头要求：'+desired}],SCHEMA,name='repair_reference_view',max_tokens=500,settings=judge_settings())
    if not answer['multiple_views']:
        return None
    left,top,right,bottom=answer['bounds']
    if not (0<=left<right<=1 and 0<=top<bottom<=1):
        raise ValueError('Reference view bounds are invalid')
    with Image.open(source) as image:
        width,height=image.size
        box=(round(left*width),round(top*height),round(right*width),round(bottom*height))
        if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Selected reference view is empty')
        target.parent.mkdir(parents=True,exist_ok=True)
        selected=image.crop(box).convert('RGB')
        selected.save(target,quality=97)
    refine=client.ask_json([client.image_part(target,1024),{'type':'text','text':
        '核对这张刚选出的参考图是否只含一个完整视图。若边缘仍混入另一视图的小条，multiple_views=true，返回只保留完整主视图的归一化bounds。'
        '单幅完整画面填multiple_views=false和[0,0,1,1]。保留整个主体和场景，不重新绘图。'}],
        SCHEMA,name='repair_reference_view_check',max_tokens=300,settings=judge_settings())
    if refine['multiple_views']:
        l,t,r,b=refine['bounds']
        if not (0<=l<r<=1 and 0<=t<b<=1):raise ValueError('Refined view bounds are invalid')
        local=(round(l*selected.width),round(t*selected.height),round(r*selected.width),round(b*selected.height))
        if local[2]<=local[0] or local[3]<=local[1]:raise ValueError('Refined reference view is empty')
        selected.crop(local).save(target,quality=97)
        box=(box[0]+local[0],box[1]+local[1],box[0]+local[2],box[1]+local[3])
    origin={'kind':'supplied-reference-view','source':str(source),'source_rectangle':list(box),
            'reference_scope':'setting_design','selection_reason':answer['reason'],'redrawn':False}
    atomic_write_json(target.with_suffix('.jpeg.origin.json'),origin)
    return origin


def propose(directory, clip, verdict):
    if verdict.get('visual_defect_kind')!='split_view':return None
    script=read_json(directory/'chapter_script.json',{})
    shots=[s for s in script.get('shots',[]) if s.get('index') in clip.get('shot_indexes',[])]
    desired='\n'.join(str(s.get('source_quote') or s.get('motion_prompt') or '') for s in shots)
    plan=deepcopy(read_json(directory/'clip_plan.json',{}));target=next(c for c in plan['clips'] if c['clip_id']==clip['clip_id'])
    views=[]
    for number,ref in enumerate(target.get('references',[]),1):
        if ref.get('role') not in {'location','prop'}:continue
        source=directory.parent/ref['path']
        derived=directory.parent/'series_assets/repair_views'/clip['clip_id']/f'reference-{number}.jpeg'
        origin=select_view(source,derived,desired)
        if origin is None:continue
        origin['reference_scope']='setting_design' if ref['role']=='location' else 'prop_design'
        atomic_write_json(derived.with_suffix('.jpeg.origin.json'),origin)
        views.append({**origin,'role':ref['role'],'name':ref['name'],'path':str(derived.relative_to(directory.parent))})
        ref['path']=str(derived.relative_to(directory.parent))
    if not views:return None
    notes=read_json(directory/'review_feedback.json',{})
    if target.get('prompt_h3'):target['prompt_h3_of']=h3_stamp(target,notes.get(clip['clip_id'],''))
    return RepairProposal(result={'changed':[clip['clip_id']],'why':'Choose single-view pixels from multi-view references'},
        script=script,plan=plan,notes=notes,changes={clip['clip_id']:{'policy':'single-reference-view-v1','views':views}})
