"""Shared source support checks for episode and clip repair hosts."""
import json
from novel_manga.application.agents.episode.arguments import UnsupportedInput


def source_check(source, quote, shots, phases, locations=None, scoped_shots=None, *, characters=None,
                 review_reason=None):
    from novel_manga.llm.client import ask_json, obj
    from novel_manga.review.endpoints import judge_settings
    prompt = ('核对原文与一份局部输入修复候选。判断修改后的入镜人物、群演、听者、地点、光线、外貌、穿戴和姿态衔接是否有原文依据，'
              '只允许原文已有事件及归属的纠正，不得新增动作事件或改变时长。camera和shot_scale是摄制实现字段；'
              '可在不改变原文事实、对白归属、动作先后及持续时间时调整机位、景别与切点实现。'
              '这些实现字段本身不算新增剧情，也不能用来补写动作、改动人物行为或重排源镜顺序。'
              'speaker_map只能纠正原文能明确核实的旧说话人错误；无证据不能换说话人。'
              '只有shots显式包含turn_texts时，允许对超出发声时长的改编对白缩写；必须保留关键问答、决定、否定、条件、因果、数字含义及说话人，不能只因非逐字引用而拒绝。其他对白不得更改。'
              '若shots显式包含turns，则本次申请是语义修复：可按原文纠正、删除错误改编对白，补回必要问答，纠正说话者及内心/公开发声方式。'
              '必须保留原文关键事实、人物知识边界、立场和因果；不能把猜测变成事实，不能让旁人听见原文中的内心想法。'
              '不得用旧错误设定否定原文明确事实，也不得凭引用真实就默认修改正确。'
              '允许纠正旧稿错误的演员绑定和声景发声暗示、登记原文已有但素材库缺失的人物；不得虚构原文没有的人物或事件。'
              'characters是缺失人物素材设计；群体必须保留原文人数和各自身份，不能用无关主角替代，不把群体换装误当单人的状态。'
              '人物与穿戴归属必须对应；常规美术设计可合理补足，不能改变明确的室内外位置。'
              'locations 是无人场景参考资产的说明，不是成片镜头；参考图无人不代表成片无人，成片入镜人物以 affected_shots 为准。'
              '资产名称是用于区分地点的标签，不要求逐字出现在原文；窗户、材质等常规美术细节只在与明确原文冲突时否定。'
              '仅核对候选实际修改的字段及其直接依赖，不因未修改的旧稿其他问题否决这项局部修复。'
              'affected_shots中的每项包含before/after/changed_fields及source_group。before是修改前已批准镜头，after是当前候选；'
              'changed_fields是宿主比较结果，必须据此判断真实变化。turns.emotion单独变化不是删改对白；只有turns.text、speaker_name、delivery_mode等语义/归属字段变化才需要审查对白。'
              'source_group列出同source_shot且source_kind相同的原有拆镜及前后顺序；把它们作为上下文，保留未修改拆镜中的对白和动作。'
              '不要把未改字段、拆镜保留对白或父source_shot时长预算当成本次修改；仍须拒绝真实改动人物、剧情、对白或确实反转动作先后的候选。'
              'reason是修复意图提示，不是原文证据，也不授权新增剧情。'
              '原文未逐字描述的正常衣料声、呼吸声、站位、长袍长度、发型等美术实现不是新增剧情；'
              '只拦截明确事实矛盾，不因“未明确说到”推定矛盾。称谓或同义地点标签也不等于换了地点。'
              '不支持或与原文相反的修改给出具体problems；所有修改有依据才consistent=true。'
              'problems仅填写确定的事实矛盾，不能填写“有依据”“符合要求”等肯定说明；没有矛盾必须problems=[]。\n'
              + json.dumps({'source': source, 'source_quote': quote, 'shots': shots,
                            'phases': phases, 'locations': locations or {}, 'characters': characters or {},
                            'affected_shots': scoped_shots or [],
                            'reason': review_reason or next((s.get('reason') for s in (scoped_shots or [])
                                                             if s.get('reason')), '')}, ensure_ascii=False))
    result = ask_json([{'type': 'text', 'text': prompt}],
        obj({'consistent': {'type': 'boolean'}, 'problems': {'type': 'array', 'items': {'type': 'string'}}}),
        name='episode_input_source', max_tokens=1200, timeout=180, settings=judge_settings())
    if result.get('consistent') is not True or result.get('problems'):
        # A judge may put support for the candidate in its problems array, or
        # contradict its own boolean. Confirm actual defects once, not the prose
        # shape, against the original source and the same scoped candidate.
        confirmation = ask_json([{'type': 'text', 'text': prompt + '\n'
            '现在独立核实这份初审结论，初审文字不是事实。只返回实际成立的原文与候选冲突。'
            '剔除肯定说明、合法缩写、旧稿未改字段和无证据推断；不能因初审不通过而维持拒绝。'
            '每项conflicts必须给逐字source_quote和具体problem，说明候选哪个字段与原文哪项事实相反。'
            '如果全部有据或只有肯定说明，conflicts=[]；无确定矛盾不补造问题。\n'
            + json.dumps({'initial': result}, ensure_ascii=False)}],
            obj({'conflicts': {'type': 'array', 'items': obj({
                'source_quote': {'type': 'string'}, 'problem': {'type': 'string'}})}}),
            name='episode_input_source_confirmation', max_tokens=1200, timeout=180, settings=judge_settings())
        conflicts = confirmation['conflicts']
        if any(not c['source_quote'].strip() or c['source_quote'] not in source
               or not c['problem'].strip() for c in conflicts):
            raise ValueError('input source confirmation lacks a verbatim source quote or concrete conflict')
        result = {'consistent': not conflicts, 'problems': [c['problem'] for c in conflicts],
                  'initial': result, 'confirmation': confirmation}
        if conflicts:
            error = UnsupportedInput('input correction lacks source support: ' + '; '.join(result['problems']))
            error.review = result
            raise error
    return result
