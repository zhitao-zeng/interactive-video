"""Source checks distinguish actual contradictions from positive judge commentary."""
import pytest

from novel_manga.application.agents.episode import input_repair


SOURCE = '甲穿黑披风。甲说：小心，有人修复了防御系统，我们已进入防御区域！'
PATCHES = [{'index': 1, 'turns': [{'speaker_name': '甲', 'text': '小心，我们已进入防御区域！'}]}]


def invoke(monkeypatch, answers):
    calls = []
    def ask(content, schema, **kwargs):
        calls.append((content, schema, kwargs))
        assert len(calls) <= len(answers), 'source checker repeated its bounded confirmation'
        answer = answers[len(calls)-1]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr('novel_manga.llm.client.ask_json', ask)
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings', lambda: {})
    return calls


@pytest.mark.parametrize('consistent', [True, False])
def test_positive_problem_text_is_confirmed_before_rejecting(monkeypatch, consistent):
    initial = {'consistent': consistent, 'problems': ['符合允许缩写对白的要求，所有修改均有原文依据。']}
    calls = invoke(monkeypatch, [initial, {'conflicts': []}])
    answer = input_repair.source_check(SOURCE, '甲说', PATCHES, {}, scoped_shots=PATCHES)
    assert answer['consistent'] is True and answer['problems'] == []
    assert answer['initial'] == initial and answer['confirmation'] == {'conflicts': []}
    assert len(calls) == 2
    confirmation_prompt = str(calls[-1][0])
    assert SOURCE in confirmation_prompt and 'speaker_name' in confirmation_prompt


def test_supported_input_does_not_add_another_model_call(monkeypatch):
    calls = invoke(monkeypatch, [{'consistent': True, 'problems': []}])
    assert input_repair.source_check(SOURCE, '甲说', PATCHES, {})['consistent'] is True
    assert len(calls) == 1


def test_real_source_conflict_remains_blocked_with_evidence(monkeypatch):
    conflict = {'source_quote': '甲穿黑披风', 'problem': '候选将黑披风明确改成白披风'}
    calls = invoke(monkeypatch, [{'consistent': False, 'problems': ['披风错误']}, {'conflicts': [conflict]}])
    with pytest.raises(input_repair.UnsupportedInput, match='白披风') as caught:
        input_repair.source_check(SOURCE, '甲说', PATCHES, {})
    assert caught.value.review['confirmation']['conflicts'] == [conflict]
    assert len(calls) == 2


def test_source_review_receives_before_changes_and_native_siblings(monkeypatch):
    import json
    from novel_manga.application.agents.episode.input_repair import _source_review_shot_context

    before = [
        {'index': 18, 'source_shot': 12, 'source_kind': 'native', 'turns': [
            {'speaker_name': '甲', 'text': '原句', 'emotion': '平静'}]},
        {'index': 19, 'source_shot': 12, 'source_kind': 'native', 'turns': [
            {'speaker_name': '甲', 'text': '保留句', 'emotion': '平静'}]},
        {'index': 24, 'source_shot': 16, 'source_kind': 'native', 'action': '先拆机'},
        {'index': 25, 'source_shot': 16, 'source_kind': 'native', 'action': '再探水'},
    ]
    after = {s['index']: dict(s) for s in before}
    after[19] = {**after[19], 'turns': [{'speaker_name': '甲', 'text': '保留句', 'emotion': '焦急'}]}
    after[24] = {**after[24], 'action': '先拆机', 'opening_frame': {'image_prompt': 'phone in hand'}}
    context = _source_review_shot_context(before, after, {19, 24})
    assert context[0]['changed_fields'] == ['turns[0].emotion']
    assert context[0]['before']['turns'][0]['text'] == context[0]['after']['turns'][0]['text']
    assert [s['index'] for s in context[0]['source_group']] == [18, 19]
    assert [s['index'] for s in context[1]['source_group']] == [24, 25]
    calls = invoke(monkeypatch, [{'consistent': True, 'problems': []}])
    input_repair.source_check(SOURCE, '甲说', PATCHES, {}, scoped_shots=context,
                              review_reason='稳定前置表达意图')
    prompt = calls[0][0][0]['text']
    payload_start = prompt.rfind('\n{') + 1
    payload = json.loads(prompt[payload_start:])
    assert payload['reason'] == '稳定前置表达意图'
    assert payload['affected_shots'][0]['changed_fields'] == ['turns[0].emotion']
    assert payload['affected_shots'][0]['source_group'][0]['index'] == 18


def test_source_policy_rechecks_only_old_saved_rejection_once():
    from novel_manga.application.agents.episode.input_records import SOURCE_REVIEW_POLICY
    from novel_manga.application.agents.episode.input_recovery_policy import source_review_recoverable
    saved = {'status': 'rejected', 'step': 'source', 'decision': {'shots': []},
             'published': False, 'source_review_policy': 2}
    assert SOURCE_REVIEW_POLICY == 3
    assert source_review_recoverable(saved)
    saved['source_review_policy'] = SOURCE_REVIEW_POLICY
    assert not source_review_recoverable(saved)


@pytest.mark.parametrize('confirmation', [TimeoutError('judge unavailable'),
    {'conflicts': [{'source_quote': '不存在的原文', 'problem': '衣服矛盾'}]}])
def test_missing_confirmation_or_false_quote_cannot_approve(monkeypatch, confirmation):
    invoke(monkeypatch, [{'consistent': True, 'problems': ['矛盾']}, confirmation])
    with pytest.raises((TimeoutError, ValueError)):
        input_repair.source_check(SOURCE, '甲说', PATCHES, {})
