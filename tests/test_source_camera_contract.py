from novel_manga.application.repair import source_support


def test_source_checker_treats_camera_and_scale_as_implementation_fields(monkeypatch):
    prompts = []
    def judge(messages, *_args, **_kwargs):
        prompts.append(messages[0]['text'])
        return {'consistent': True, 'problems': []}
    monkeypatch.setattr('novel_manga.llm.client.ask_json', judge)
    monkeypatch.setattr('novel_manga.review.endpoints.judge_settings', lambda: {})

    source = '她仍站在走廊里，把手机举在胸前，随后停下。'
    result = source_support.source_check(source, source, [{'index':1,'camera':'手机自拍，胸像固定'}], {}, {}, [
        {'index':1,'source_shot':4,'source_kind':'dialogue',
         'before':{'camera':'手持全景','shot_scale':'wide'},
         'after':{'camera':'手机自拍，胸像固定','shot_scale':'medium'},
         'changed_fields':['camera','shot_scale'],'source_group':[]}])

    prompt = prompts[0]
    assert result['consistent'] is True
    assert 'camera和shot_scale是摄制实现字段' in prompt
    assert '可在不改变原文事实、对白归属、动作先后及持续时间时调整' in prompt
    assert '不能用来补写动作、改动人物行为或重排源镜顺序' in prompt
