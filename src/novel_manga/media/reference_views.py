"""Bind the supplied portrait and wardrobe views without inventing new assets."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from novel_manga.util import read_json


@dataclass(frozen=True)
class OpeningReference:
    path: str
    hash: str
    role: str
    reference_scope: str | None
    order: int
    name: str | None = None
    asset_id: str | None = None
    view: str | None = None
    wearers: object = None

    @property
    def scope(self):
        return self.reference_scope

    def as_dict(self):
        row={'path':self.path,'hash':self.hash,'role':self.role,
             'reference_scope':self.reference_scope,'order':self.order}
        for key in ('name','asset_id','view','wearers'):
            value=getattr(self,key)
            if value is not None:row[key]=value
        return row

    def __getitem__(self,key):
        return self.as_dict()[key]

    def get(self,key,default=None):
        return self.as_dict().get(key,default)


def compile_references(root,references,*,require_files=True):
    result=[]
    for order,ref in enumerate(references):
        path=ref['path'];disk=Path(path)
        if not disk.is_absolute():disk=Path(root)/disk
        digest=hashlib.sha256(disk.read_bytes()).hexdigest() if require_files or disk.is_file() else ''
        result.append(OpeningReference(path=path,hash=digest,
            role=ref.get('role','reference'),reference_scope=ref.get('reference_scope'),order=order,
            name=ref.get('name'),asset_id=ref.get('asset_id'),view=ref.get('view'),wearers=ref.get('wearers')))
    return tuple(result)


def canonical_paths(ctx,paths):
    catalog=read_json(Path(ctx.run_dir)/'catalog.json',[]) if getattr(ctx,'run_dir',None) else []
    if isinstance(catalog,dict):catalog=catalog.get('assets',[])
    aliases={}
    for row in catalog:
        if row.get('role') not in {'character','location','prop'} or not row.get('path') or not row.get('image'):continue
        aliases[row['image']]=row['path']
        aliases['/work/'+row['image'].lstrip('/')]=row['path']
    return [aliases.get(path,path) for path in paths]


def scope_of(root,ref):
    path=Path(ref.get('path') or '')
    if not path.name:return None
    if not path.is_absolute():path=Path(root)/path
    value=ref.get('reference_scope')
    if not isinstance(value,str) or not value:value=read_json(path.with_name(path.name+'.origin.json'),{}).get('reference_scope')
    if not isinstance(value,str):return None
    return {'identity-baseline':'identity_portrait','wardrobe-visual-card':'wardrobe_design'}.get(value,value)


def supplied_wardrobe_views(root,ref):
    root=Path(root)
    if ref.get('role')!='character' or not ref.get('asset_id'):return []
    expected=Path('series_assets/characters')/ref['asset_id']
    path=Path(ref.get('path') or '')
    if path.is_absolute():
        if not path.is_relative_to(root):return []
        path=path.relative_to(root)
    # A mixed refs folder has no reliable per-person owner. Only the existing
    # character's own asset directory establishes this association.
    if path.parent!=expected:return []
    result=[]
    for origin in sorted((root/expected).glob('*.origin.json')):
        record=read_json(origin,{})
        picture=origin.with_name(origin.name.removesuffix('.origin.json'))
        row={'role':'character','asset_id':ref['asset_id'],'name':ref.get('name'),
             'path':str(picture.relative_to(root)),'view':'wardrobe'}
        if record.get('kind')=='supplied-reference' and scope_of(root,row)=='wardrobe_design' and picture.is_file():
            result.append({**row,'reference_scope':'wardrobe_design'})
    return result


def opening_references(ctx,clip,paths):
    paths=canonical_paths(ctx,paths)
    root=Path(ctx.novel_dir);catalog=[]
    if getattr(ctx,'run_dir',None):catalog=read_json(Path(ctx.run_dir)/'catalog.json',[])
    if isinstance(catalog,dict):catalog=catalog.get('assets',[])
    known={r['path']:dict(r) for r in catalog if r.get('path')}
    for ref in clip.get('references',[]):
        if ref.get('path'):known[ref['path']]={**known.get(ref['path'],{}),**ref}
    for ref in list(known.values()):
        for body in supplied_wardrobe_views(root,ref):known.setdefault(body['path'],body)
    selected=[];seen=set()
    for path in paths:
        ref={**known.get(path,{}),'path':path}
        if scope:=scope_of(root,ref):ref['reference_scope']=scope
        if path not in seen:selected.append(ref);seen.add(path)
    # Keep the caller's existing order. Required source wardrobe views follow
    # it, with explicit labels in the real image request and review evidence.
    for ref in list(selected):
        if ref.get('reference_scope')!='identity_portrait':continue
        bodies=[body for body in known.values() if body.get('role')=='character'
                and body.get('asset_id')==ref.get('asset_id') and body.get('name')==ref.get('name')
                and scope_of(root,body)=='wardrobe_design']
        for body in bodies:
            if body['path'] not in seen:selected.append(body);seen.add(body['path'])
    return selected
