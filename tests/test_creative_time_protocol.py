"""B13 null times must be rejected by the wire schema for every field-based finding."""
import copy

import pytest

from novel_manga.application.preparation import audit
from novel_manga.planning.preparation import CREATIVE_TIMES, audit_schema


def episode():
    return {'chapter_script':{'shots':[{
        'index':14,'start_state':'The record is on the wall.',
        'event':'She takes the record and reads the repair.',
        'end_state':'She holds the record.',
        'camera':'Begin outside and follow her inside.',
        'turns':[{'text':'The support was replaced with wood.'}]}],
        'music_plan':{'cues':[{'reason':'Play after the repair is complete.'}]}}}


def finding(origin,paths,basis,**changes):
    return {'kind':'action','stage':None,'source_quote':'',
        'source_segment':'source.md' if origin=='original_text' else '',
        'problem':'The record is claimed to be held too early.',
        'correction':'Check the original action and current time.',
        'evidence_origin':origin,'basis':[paths.index(path)for path in basis],
        'observed':'','expected':'','observed_time':None,'expected_time':None,
        'relation':'contradiction',**changes}


def branches_for(schema,issue):
    """Inspect the applicable existing issue branches, without a new schema engine."""
    result=[]
    shape='array' if isinstance(issue.get('basis'),list) else 'null'
    for branch in schema['properties']['issues']['items']['anyOf']:
        properties=branch['properties']
        if issue['evidence_origin'] not in properties['evidence_origin']['enum']:continue
        if issue['kind'] not in properties['kind']['enum']:continue
        field=properties['basis']
        if any(option.get('type')==shape for option in field.get('anyOf',[field])):
            result.append(branch)
    return result


@pytest.mark.parametrize('origin,second',[('original_text','chapter_script.shots[0].turns[0].text'),
                                        ('authored_field','chapter_script.music_plan.cues[0].reason')])
def test_b13_two_failure_classes_require_nonnull_enum_times_in_every_applicable_branch(origin,second):
    data=episode();paths=audit._creative_evidence_paths(data)
    issue=finding(origin,paths,['chapter_script.shots[0].event',second])
    applicable=branches_for(audit._creative_schema(paths,data,{'source.md':'原始完整素材内容。'}),issue)
    assert applicable
    for branch in applicable:
        for name in ('observed_time','expected_time'):
            assert branch['properties'][name]=={'type':'string','enum':sorted(CREATIVE_TIMES)}
            assert issue[name] not in branch['properties'][name]['enum']
    with pytest.raises(ValueError,match='invalid evidence time'):
        audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[copy.deepcopy(issue)]},
            data,{'source.md':'原始完整素材内容。'},evidence_paths=paths)


@pytest.mark.parametrize('origin',['authored_field','original_text'])
def test_legal_unknown_times_stay_model_selected_and_fixed_times_still_come_from_host(origin):
    data=episode();paths=audit._creative_evidence_paths(data);sources={'source.md':'原始完整素材内容。'}
    issue=finding(origin,paths,['chapter_script.shots[0].camera','chapter_script.music_plan.cues[0].reason'],
                  observed_time='during',expected_time='whole_shot')
    before=copy.deepcopy(issue)
    audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[issue]},data,sources,evidence_paths=paths)
    assert (issue['observed_time'],issue['expected_time'])==('during','whole_shot')
    assert issue['stage']==14
    assert all(issue[key]==before[key] for key in ('problem','correction','relation'))
    fixed=finding(origin,paths,['chapter_script.shots[0].start_state','chapter_script.shots[0].end_state'],
                  observed_time='during',expected_time='t=0')
    audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[fixed]},data,sources,evidence_paths=paths)
    assert (fixed['observed_time'],fixed['expected_time'])==('t=0','t=end')


def test_source_only_optional_times_and_original_grounding_are_unchanged():
    data=episode();paths=audit._creative_evidence_paths(data);source='原包完整文本明确要求这个动作。'
    issue={'kind':'missing_event','stage':0,'source_quote':'','source_segment':'source.md',
        'problem':'A source event has no shot.','correction':'Preserve the source event.',
        'evidence_origin':'original_text','basis':None,'observed':None,'expected':None,
        'observed_time':None,'expected_time':None,'relation':None}
    original=audit_schema(creative=True)['properties']['issues']['items']['anyOf'][1]
    applicable=branches_for(audit._creative_schema(paths,data,{'source.md':source}),issue)
    assert applicable
    for branch in applicable:
        assert branch['properties']['basis']=={'type':'null'}
        for name in ('observed_time','expected_time'):
            assert branch['properties'][name]==original['properties'][name]
    audit._ground_creative_answer({'source_readable':True,'source_problem':'','issues':[issue]},
                                 data,{'source.md':source},evidence_paths=paths)
    assert issue['stage']==0 and issue['source_quote']==source and 'basis' not in issue
