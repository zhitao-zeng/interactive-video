"""Generate, review and freeze interaction artwork before movie export."""
from pathlib import Path
from PIL import Image, ImageOps
from novel_manga.util import read_json, atomic_write_json


def content(interaction):
    return {**{key:interaction.get(key) for key in ('series_title','question','selected')},
            'options':[{key:option[key] for key in ('id','label','reason') if key in option}
                       for option in interaction['options']]}


def check(path, interaction):
    from novel_manga.llm.client import ask_json, image_part, obj
    from novel_manga.review.endpoints import judge_settings
    import json
    instructions=('逐字核对这张互动尾卡的节目名、问题、选项编号及全部选项文字、人名，不靠提示脑补错字。'
        '允许按原词分行；冒号和姓名之间的×可作为排版分隔符。选项必须清楚、等权，不得暗示选中或胜出。'
        '主问题和选项在手机竖屏应清楚可读。底部应为“评论区留下你的选择”。'
        '先写实际观察，再判断。原文：'+json.dumps(content(interaction),ensure_ascii=False))
    return ask_json([{'type':'text','text':instructions},image_part(path,1920)],
        obj({'consistent':{'type':'boolean'},'observations':{'type':'array','items':{'type':'string'}},
             'problems':{'type':'array','items':{'type':'string'}}}),
        name='interaction_card',max_tokens=1800,timeout=240,settings=judge_settings())


def prepare(root, novel, prompt, *, provider=None, checker=None):
    from novel_manga.application.profiles import frame_spec
    from novel_manga.config import Settings
    from novel_manga.providers.phanrouter import PhanRouterMediaProvider
    import json
    episode=novel/(novel.name+'_1');interaction=read_json(episode/'interaction.json')
    ratio=frame_spec(read_json(novel/'profile.json',{}))['video_ratio']
    request=prompt.strip()+'\n准确呈现以下互动原文，所有选项等权，不添加胜者。允许分行，不改人名与题意。底部文字：评论区留下你的选择。\n'+json.dumps(content(interaction),ensure_ascii=False)
    identity={'prompt':request,'content':content(interaction),'aspect_ratio':ratio}
    folder=novel/'interaction_cards'/episode.name;folder.mkdir(parents=True,exist_ok=True)
    (folder/'owner').write_text(f'Interactive API interaction-card artwork / {novel}\n')
    candidates=sorted(folder.glob('candidate_*'))
    candidate=next((p for p in candidates if read_json(p/'input.json',{})==identity),None)
    if candidate is None:
        candidate=folder/f'candidate_{len(candidates)+1:02d}';candidate.mkdir()
        atomic_write_json(candidate/'input.json',identity)
    image=candidate/'card.png'
    if not image.is_file():
        provider=provider or PhanRouterMediaProvider(Settings.from_env(provider='phanrouter',output_root=novel.parent,admission_mode='preview'))
        provider.create_image(request,image,aspect_ratio=ratio)
    with Image.open(image) as opened:opened.load()
    review=read_json(candidate/'review.json',{})
    if not review:
        review=(checker or check)(image,interaction);atomic_write_json(candidate/'review.json',review)
    if review.get('consistent') is not True or review.get('problems'):
        raise ValueError(f'API interaction-card review rejected; inspect {candidate}')
    interaction['artwork']={'path':str(image.relative_to(novel)),'content':content(interaction),
        'review':review,'candidate':candidate.name,'input':str((candidate/'input.json').relative_to(novel))}
    atomic_write_json(episode/'interaction.json',interaction)
    return interaction['artwork']


def render(path, interaction, novel, width, height):
    artwork=interaction['artwork']
    if artwork.get('content')!=content(interaction):raise ValueError('Interaction artwork stale after question/options changes')
    review=artwork.get('review',{})
    if review.get('consistent') is not True or review.get('problems'):
        raise ValueError('Interaction artwork requires an accepted image review')
    image=novel/artwork['path']
    with Image.open(image) as opened:
        opened=opened.convert('RGB')
        if abs(opened.width/opened.height-width/height)>.03:
            raise ValueError('Interaction artwork aspect ratio differs from the production frame')
        ImageOps.fit(opened,(width,height),method=Image.Resampling.LANCZOS).save(path,quality=95)
