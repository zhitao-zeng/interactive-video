"""Source audit selects actual production leaves without inventing JSON paths."""
import copy
import json

import pytest

from novel_manga.application.preparation import audit
from novel_manga.planning.preparation import _creative_evidence


def episode():
    return {'chapter_script': {'shots': [{'index': 7, 'start_state': '灯还没有亮起。',
        'event': '镜内点亮灯。', 'audio_plan': {'music_cue': 'quiet'},
        'visual_prompt': 'Derived, not evidence.', 'motion_prompt': 'Derived motion.',
        'props': ['木灯架'], 'empty': ' ', 'flag': True}],
        'music_plan': {'cues': [{'id': 'quiet', 'reason': '让对白清晰。',
                               'start': {'shot': 7, 'edge': 'start'}}]}},
        'clip_plan': {'derived': 'Excluded from the creative view.'}}


def finding(basis, **changes):
    return {'kind': 'action', 'stage': 7, 'source_segment': '', 'source_quote': '',
            'evidence_origin': 'authored_field', 'basis': basis,
            'observed': '', 'expected': '', 'observed_time': 'whole_shot',
            'expected_time': 'whole_shot', 'relation': 'contradiction',
            'problem': '核对音乐标注。', 'correction': '依据实际时间线判断。', **changes}


def answer(issue):
    return {'source_readable': True, 'source_problem': '', 'issues': [issue]}


def prompt_input(parts):
    text = parts[0]['text']
    result = json.JSONDecoder().raw_decode(text[text.index('{"episode":'):])[0]
    if isinstance(result.get('evidence_paths'), dict):
        result['evidence_paths'] = list(result['evidence_paths'].values())
    return result


def basis_schema(schema, origin):
    branches = schema['properties']['issues']['items']['anyOf']
    branch = next(row for row in branches if row['properties']['evidence_origin']['enum'] == [origin])
    basis = branch['properties']['basis']
    return basis.get('anyOf', [basis])[0]


def test_initial_and_correction_use_integer_catalog_and_preserve_raw_then_confirm(tmp_path, monkeypatch):
    original = episode()
    saved = copy.deepcopy(original)
    calls, raw_responses = [], []
    paths = ['chapter_script.shots[0].audio_plan.music_cue',
             'chapter_script.music_plan.cues[0].reason']

    def ask(parts, schema, **options):
        payload = prompt_input(parts)
        calls.append(options['name'])
        if options['name'] == 'pre_render_audit_confirmation':
            issue = payload['issues'][0]
            assert issue['basis'] == paths
            assert (issue['observed'], issue['expected']) == ('quiet', '让对白清晰。')
            return {'decisions': [{'issue_index': 0, 'decision': 'withdraw',
                                    'reason': '实际字段没有证实矛盾。'}]}
        catalog = payload['evidence_paths']
        assert len(catalog) == len(set(catalog))
        assert set(catalog) == {'chapter_script.shots[0].start_state', 'chapter_script.shots[0].event',
            'chapter_script.shots[0].audio_plan.music_cue', 'chapter_script.shots[0].props[0]',
            'chapter_script.music_plan.cues[0].id', 'chapter_script.music_plan.cues[0].reason',
            'chapter_script.music_plan.cues[0].start.edge'}
        assert 'music_plan.cues[0].reason' not in catalog
        for origin in ('authored_field', 'original_text'):
            assert basis_schema(schema, origin)['items'] == {
                'type': 'integer', 'minimum': 0, 'maximum': len(catalog)-1}
        basis = [catalog.index(path) for path in paths]
        if len(calls) == 1:
            basis[1] = len(catalog)  # Even a malformed service response must not be repaired by guessing.
        result = answer(finding(basis))
        if options['name'] == 'pre_render_audit_evidence':
            result['issues'][0]['issue_index'] = 0
        raw_responses.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = audit.creative_audit(original, {'source.md': '原始完整素材内容。'}, cache_path=tmp_path/'audit.json')
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence', 'pre_render_audit_confirmation']
    assert result['answer']['issues'] == []
    assert result['raw']['issues'][0]['basis'] == paths
    assert [row['response'] for row in result['raw_response_history']] == raw_responses
    assert original == saved


def test_catalog_keeps_every_addressable_nonempty_string_in_supported_json_structures():
    data = episode()
    data['design'] = {'中文名字': ['外观', {'description': '木制'}],
                      'not.a.path': '不可寻址', 'matrix': [['当前路径语法不支持双下标']],
                      'none': None, 'number': 4, 'empty': ''}
    view = audit._creative_view(data)
    paths = audit._creative_evidence_paths(view)
    assert 'design.中文名字[0]' in paths
    assert 'design.中文名字[1].description' in paths
    assert not any('not.a.path' in path or 'matrix' in path for path in paths)
    assert all(isinstance(_creative_evidence(view, path), str) and
               _creative_evidence(view, path).strip() for path in paths)


@pytest.mark.parametrize('changes', [{'stage': 8}, {'observed_time': 'during'}])
def test_selected_real_leaf_derives_known_metadata_before_independent_confirmation(tmp_path, monkeypatch, changes):
    raw = []
    def ask(parts, schema, **options):
        data = prompt_input(parts)
        if options['name'] == 'pre_render_audit_confirmation':
            issue = data['issues'][0]
            assert issue['stage'] == 7
            assert issue['observed_time'] == issue['expected_time'] == 't=0'
            return {'decisions':[{'issue_index':0,'decision':'withdraw','reason':'同一真实字段没有矛盾。'}]}
        assert options['name'] == 'pre_render_story_audit'
        paths = data['evidence_paths']
        result = answer(finding([paths.index('chapter_script.shots[0].start_state')]*2,
                               **{'observed_time':'t=0', 'expected_time':'t=0', **changes}))
        raw.append(copy.deepcopy(result))
        return result
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = audit.creative_audit(episode(), {'source.md':'原始完整素材内容。'}, cache_path=tmp_path/'audit.json')
    assert result['answer']['issues'] == []
    assert [row['response'] for row in result['raw_response_history']] == raw


def test_selected_real_leaf_does_not_relax_original_source(tmp_path, monkeypatch):
    calls = []
    def ask(parts, schema, **options):
        calls.append(options['name'])
        paths = prompt_input(parts)['evidence_paths']
        result = answer(finding([paths.index('chapter_script.shots[0].start_state')]*2,
                              observed_time='t=0', expected_time='t=0',
                              evidence_origin='original_text', source_segment='missing.md'))
        if options['name'] == 'pre_render_audit_evidence':
            result['issues'][0]['issue_index'] = 0
        return result
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    with pytest.raises(ValueError, match='ungrounded source'):
        audit.creative_audit(episode(), {'source.md': '原始完整素材内容。'}, cache_path=tmp_path/'audit.json')
    assert calls == ['pre_render_story_audit', 'pre_render_audit_evidence']
    assert not (tmp_path/'audit.json').exists()


def test_source_only_finding_still_works_without_production_text_leaves(tmp_path, monkeypatch):
    calls = []
    def ask(parts, schema, **options):
        calls.append(options['name'])
        if options['name'] == 'pre_render_audit_confirmation':
            return {'decisions': [{'issue_index': 0, 'decision': 'keep', 'reason': '原包明确要求此动作。'}]}
        assert prompt_input(parts)['evidence_paths'] == []
        assert basis_schema(schema, 'original_text') == {'type': 'null'}
        return answer(finding(None, evidence_origin='original_text', source_segment='source.md',
            observed=None, expected=None, observed_time=None, expected_time=None, relation=None))
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    result = audit.creative_audit({'chapter_script': {'shots': [{'index': 7}]}},
        {'source.md': '原包明确要求此处捞起背包。'}, cache_path=tmp_path/'audit.json')
    assert result['answer']['issues'][0]['source_quote'] == '原包明确要求此处捞起背包。'
    assert 'basis' not in result['answer']['issues'][0]
    assert len(calls) == 2
