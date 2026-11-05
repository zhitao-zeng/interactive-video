"""Bind the supplied portrait and wardrobe views without inventing new assets."""
from pathlib import Path
from novel_manga.util import read_json


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
        for body in supplied_wardrobe_views(root,ref):
            if body['path'] not in seen:selected.append(body);seen.add(body['path'])
    return selected
