from types import SimpleNamespace
from novel_manga.application.identity.context import prompt_rows


def test_phase_replaces_superseded_look_without_erasing_role():
    design={'role':'travelling companion','gender':'male','age':'young','appearance':'human in jacket'}
    phase={'from':1,'to':20,'appearance':'untransformed beast','gender':'','age':'adult beast'}
    catalog=SimpleNamespace(phases={'companion':[phase]},candidates=lambda *a:[
        {'id':'e1','name':'companion','forms':['companion'],'design':design}])
    row=prompt_rows(catalog,'passage',1)[0]
    assert row['design_reference_only']['appearance']=='untransformed beast'
    assert row['design_reference_only']['gender']==''
    assert row['design_reference_only']['role']=='travelling companion'
    assert design['appearance']=='human in jacket'
