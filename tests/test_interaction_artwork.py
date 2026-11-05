from types import SimpleNamespace as NS
from PIL import Image
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.interactive.interaction_card import prepare,render
from test_interactive_delivery import labelled_delivery


def test_episode_label_is_part_of_the_reviewed_card_content():
    from novel_manga.interactive.interaction_card import content
    interaction={'series_title':'《心动待定》','question':'选择哪组？','selected':None,
        'options':[{'id':'A','label':'早市'}],'episode_label':'EP02-A'}
    accepted=content(interaction)
    assert accepted['episode_label']=='EP02-A'
    interaction['episode_label']='EP02-B'
    assert content(interaction)!=accepted


def test_api_card_is_reviewed_cached_exported_and_invalidated_by_text_change(tmp_path):
    book=tmp_path/'romance-card';episode=book/(book.name+'_1');episode.mkdir(parents=True)
    atomic_write_json(book/'profile.json',{'frame':'9:16'})
    interaction={'series_title':'《心动待定》','question':'首张双人加场卡，给哪一组？','selected':None,
        'options':[{'id':'A','label':'早市搭档：夏宁×程越','next':'not rendered'},
                   {'id':'B','label':'旧物搭档：乔伊×陆川'}]}
    atomic_write_json(episode/'interaction.json',interaction);calls=[]
    def image(prompt,path,**kwargs):
        assert 'not rendered' not in prompt
        calls.append('image');Image.new('RGB',(180,320),'ivory').save(path)
    def check(*args):calls.append('review');return {'consistent':True,'problems':[]}
    provider=NS(create_image=image)
    prepare(tmp_path,book,'Agent visual design',provider=provider,checker=check)
    prepare(tmp_path,book,'Agent visual design',provider=provider,checker=check)
    assert calls==['image','review']
    accepted=read_json(episode/'interaction.json');path=tmp_path/'card.jpeg'
    render(path,accepted,book,108,192)
    with Image.open(path) as opened:assert opened.size==(108,192)
    accepted['options'][0]['label']='different couple'
    with pytest.raises(ValueError,match='stale'):render(path,accepted,book,108,192)


def test_delivery_uses_reviewed_artwork_instead_of_solid_card(labelled_delivery,monkeypatch):
    from novel_manga.interactive import delivery
    case=labelled_delivery;path=case.ctx.episode_dir/'interaction.json'
    case.ctx.novel_dir=case.ctx.episode_dir.parent
    interaction=read_json(path);interaction['artwork']={'path':'reviewed.png'};atomic_write_json(path,interaction)
    seen=[]
    monkeypatch.setattr('novel_manga.interactive.interaction_card.render',lambda *args:seen.append(args))
    monkeypatch.setattr(delivery,'_card',lambda *args:pytest.fail('Use selected API artwork'))
    delivery.deliver(NS(context=case.ctx),case.report,case.destination)
    assert seen and seen[0][1]['artwork']['path']=='reviewed.png'


def test_authored_art_direction_prepares_the_card_in_normal_delivery(labelled_delivery,monkeypatch):
    from novel_manga.interactive import delivery
    case=labelled_delivery;case.ctx.novel_dir=case.ctx.episode_dir.parent
    path=case.ctx.episode_dir/'interaction.json';interaction=read_json(path)
    interaction['art_direction']='Approved Agent design';atomic_write_json(path,interaction);seen=[]
    def prepare_art(root,book,prompt):
        seen.append(prompt);saved=read_json(path);saved['artwork']={'path':'api.png'};atomic_write_json(path,saved)
    monkeypatch.setattr('novel_manga.interactive.interaction_card.prepare',prepare_art)
    monkeypatch.setattr('novel_manga.interactive.interaction_card.render',lambda *args:seen.append('render accepted artwork'))
    monkeypatch.setattr(delivery,'_card',lambda *args:pytest.fail('Do not use solid card'))
    delivery.deliver(NS(context=case.ctx),case.report,case.destination)
    assert seen==['Approved Agent design','render accepted artwork']
