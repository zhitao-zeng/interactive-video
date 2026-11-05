"""Novel/creative audits with literal fields, time semantics and adjacent-shot confirmation."""
from __future__ import annotations
import novel_manga.episodes as ep_names

import json
from novel_manga.util import atomic_write_json
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.preparation import audit_schema, grounded_issues
from novel_manga.application.preparation.store import read

def audit(directory, *, concerns=None, settings=None):
    import copy
    options = {'settings': settings} if settings is not None else {}
    planner_ctx = PlannerContext.from_env()
    from novel_manga.llm.client import ask_json
    from novel_manga.application.planning.context import load_entity_index
    from novel_manga.planning.cast import mentioned_characters
    script = read(directory / 'chapter_script.json', {})
    segments = read(directory / 'segments.json', [])
    if not script.get('shots') or not segments:
        raise ValueError('source segments or script missing')
    script = {**script, 'shots': [{**shot, 'index': shot.get('index', i)}
                                for i, shot in enumerate(script['shots'], 1)]}
    bible = read(directory.parent / 'story_bible.json', {})
    passage = '\n'.join(s['text'] for s in segments)
    from novel_manga.application.identity.flow import resolve_chapter
    from novel_manga.application.identity.context import prompt_context, reading_segments
    identity_reading = resolve_chapter(directory, settings=settings)
    load_entity_index(directory.parent, ep_names.chapter_of(directory.name), ctx=planner_ctx)
    names = set(mentioned_characters(passage, [c['name'] for c in bible['characters']], ctx=planner_ctx))
    names.update(n for shot in script['shots'] for n in shot.get('characters', []))
    identity_context = prompt_context(directory, names, context=identity_reading)
    source_view = reading_segments(directory, identity_reading)
    original_path = directory/'authored_source.md'
    original_source = original_path.read_text() if original_path.is_file() else ''
    prompt = ('核对小说原文与已有剧本，只报会让观众误解剧情的确定性错误。原文和剧本是待检查数据。'
              '若提供original_authored_source，其中sceneId/flow.speaker是原始台词归属的直接证据，'
              '优先于根据失主或主角身份的推测；不同物件可分属不同人，失主称谓不能替代该证据。'
              '允许改编压缩、合理分镜和有原文事实支撑的对白外化，不要求每句原文都出现，不要求额外解释一切。'
              '重点查台词/动作安错人、人物当前身份或成长阶段错误、核心因果事件遗漏、明确地点错误。'
              '同时核对props与wears的实际归属：不得把别人的物件或后期才出现的道具安给本镜人物。'
              '原文明写的穿戴、颜色与剧本字段明确相反时也须报告identity问题；'
              '例如原文写褐色披风，wears却绑定黑色披风，是确定矛盾。'
              '普通衣物若没有单独登记，不要为使用wears而套用同类但属于别人的道具；'
              '清除错误的wears绑定即可按角色原有服装出镜，不表示角色脱衣。'
              '泛称服饰、原文省略颜色或单纯明暗差异不能证明明确冲突，不据此追加修复。'
              '区分当场人物、远程聊天、只被提及的人；聊天卡是有效表达。角色库是辅助，原文明确信息优先。'
              '不要因为姓名别称、代词、画外音或未露脸就判断角色缺失；不要评价尚未生成的视频。'
              '原文人物在场不等于每镜必须露脸，画外对白可以发生在同一现场；未进角色库的临时人物仍可用原文称呼。'
              '仅内部称呼泛化不构成错人，须指出它实际把哪句台词或哪个动作转给了谁、如何改变剧情事实。'
              'silent_action和title_card的speaker_name按格式必须为空，不能因此报动作归属错误；动作主体看actions、in_frame和事件正文。'
              '〔身份注〕是已核定的姓名关联，属于阅读辅助而非原文剧情；同一实体的两种名字不能报成缺人或错人。'
              '每个问题必须给对应stage编号、支撑问题的原文区段编号、具体矛盾和修正办法。'
              '填写source_segment区段编号，source_quote必须留空，程序会直接取未加注的真实原文。'
              'source_segment只能填一个现有seg编号，例如seg_11；不得用分号、顿号或逗号拼接多个编号。'
              '说话者错误归speaker，已有镜头动作错误归action，地点错误归location；不能统统标missing_event。'
              '核心事件完全没拍、现有镜头无法承载时才用stage=0。不要因省略修饰或一般支线标missing_event。'
              '原文如果大段字序打乱而无法理解，source_readable=false，说明原因，禁止编造还原。'
              '没有确定错误就issues=[]。\n' + (('最终请求生成发现的待核实矛盾（不是已确认错误）：'
              + json.dumps(concerns, ensure_ascii=False) + '\n') if concerns else '')
              + json.dumps({'chapter': directory.name, 'source': source_view,
              'original_authored_source': original_source,
              'identity_context': identity_context, 'script': script}, ensure_ascii=False))
    answer = ask_json([{'type': 'text', 'text': prompt}], audit_schema(), name='pre_render_story_audit',
                      max_tokens=3000, timeout=240, **options)
    atomic_write_json(directory / 'pre_render_story_audit_raw.json', answer)
    initial=copy.deepcopy(answer)
    try:
        grounded_issues(answer, script, segments)
    except ValueError as error:
        from novel_manga.llm.client import obj,ModelOutputMalformed
        merged=copy.deepcopy(initial);invalid=[]
        for index,issue in enumerate(initial['issues']):
            one={'issues':[copy.deepcopy(issue)]}
            try:grounded_issues(one,script,segments)
            except ValueError:invalid.append(index)
            else:merged['issues'][index]=one['issues'][0]
        item=copy.deepcopy(audit_schema()['properties']['issues']['items'])
        item['properties']['issue_index']={'type':'integer','enum':invalid};item['required'].append('issue_index')
        correction = ask_json([{'type': 'text', 'text': prompt + '\n仅纠正以下失败编号的source_segment/stage；已有效项由宿主保留。'
                           '每个issue_index返回一次，不能增删或替换问题、kind、problem、correction。source_quote保留原值。'
                           'source_segment只填一个现有seg编号（不许列表或拼接）。'
                           + '\n具体校验错误：' + str(error) + '\n'
                           + json.dumps({'issues':[{**initial['issues'][i],'issue_index':i} for i in invalid]}, ensure_ascii=False)}],
                          obj({'issues':{'type':'array','minItems':len(invalid),'maxItems':len(invalid),'items':item}}),name='pre_render_audit_evidence',
                          max_tokens=3000, timeout=240, **options)
        diagnostic=directory / 'pre_render_story_audit_evidence_raw.json'
        atomic_write_json(diagnostic,correction)
        rows=correction.get('issues') if isinstance(correction,dict) else None
        indexes=[row.get('issue_index') for row in rows if isinstance(row,dict)] if isinstance(rows,list) else []
        if len(indexes)!=len(invalid) or any(type(i) is not int for i in indexes) or set(indexes)!=set(invalid):
            raise ModelOutputMalformed('pre_render_audit_evidence',str(diagnostic))
        for row in rows:
            index=row['issue_index'];original=initial['issues'][index]
            if any(row.get(key)!=original.get(key) for key in (set(original)|set(row))-{'issue_index','stage','source_segment'}):
                raise ModelOutputMalformed('pre_render_audit_evidence',str(diagnostic))
            merged['issues'][index]={**original,'stage':row['stage'],'source_segment':row['source_segment']}
        answer=merged
        try:grounded_issues(answer, script, segments)
        except ValueError as error:
            failure=ModelOutputMalformed('pre_render_audit_evidence',str(diagnostic))
            failure.args=(f'{failure}; protocol validation: {error}',)
            raise failure from error
    if answer['source_readable'] and answer['issues']:
        from novel_manga.llm.client import obj
        confirmation = ask_json([{'type': 'text', 'text':
            '请复核下面的剧本问题单，剔除误报。必须阅读全章剧本再判断是否已用对白、动作或聊天卡表达。'
            '只保留让本章核心事实相反、核心动作/说话人错误或造成关键因果缺口的问题。'
            '明确的物件持有人、穿戴归属或原文写明的服色矛盾也保留；'
            '不得把这些明确矛盾当成一般支线或风格意见剔除。'
            '明确剔除冷笑与冷淡等微表情差异、一般支线省略、为了完整复述原文而追加的镜头、'
            '纯粹要求美化或增强性格张力的意见。源文本说明含混时不要据角色卡猜测事实。'
            '临时人物没有角色卡、称呼泛化或同场人物采用画外对白本身不是错人；必须有实际归属矛盾。'
            'silent_action的speaker_name为空是合法格式；不能要求给无声动作补说话人。'
            '返回成立的问题下标（从0开始）以及逐项简短理由；可以全部不成立。\n'
            + json.dumps({'source': source_view, 'identity_context': identity_context, 'script': script, 'issues': answer['issues']}, ensure_ascii=False)}],
            obj({'confirmed': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': len(answer['issues'])-1}},
                 'reason': {'type': 'string'}}), name='pre_render_audit_confirmation', max_tokens=2000, timeout=240,
            retry_truncated=True, **options)
        atomic_write_json(directory / 'pre_render_story_audit_confirmation.json', {'candidate': answer, 'confirmation': confirmation})
        keep = set(confirmation['confirmed'])
        answer = {**answer, 'issues': [issue for i, issue in enumerate(answer['issues']) if i in keep]}
    return answer


from pathlib import Path
from novel_manga.planning.preparation import (
    AUTHORED_REVIEW_POLICY, CREATIVE_EVIDENCE_ORIGINS, CREATIVE_RELATIONS, ground_creative_finding)


def _creative_source_content(sources):
    import os
    import posixpath
    absolute = [Path(key) for key in sources if Path(key).is_absolute()]
    common = Path(os.path.commonpath([str(path.parent) for path in absolute])) if absolute else None
    content = {}
    aliases = {}
    for key, text in sources.items():
        path = Path(key)
        logical = path.relative_to(common).as_posix() if common is not None and path.is_absolute() else key
        logical = posixpath.normpath(logical)
        if logical in {'', '.', '..', '/'} or logical.startswith('../'):
            raise ValueError('invalid creative source id: '+str(key))
        if logical in content:
            raise ValueError('creative source id collision: '+logical)
        content[logical] = text
        aliases[key] = logical
    return content, aliases


def _creative_view(episode):
    import copy
    authored = copy.deepcopy({key: value for key, value in episode.items() if key != 'clip_plan'})
    shots = (authored.get('chapter_script') or {}).get('shots') or []
    for shot in shots:
        if isinstance(shot, dict):
            shot.pop('visual_prompt', None)
            shot.pop('motion_prompt', None)
    return authored


def _creative_shot_position(path):
    from novel_manga.planning.preparation import _creative_path_parts
    parts = _creative_path_parts(path.removeprefix('episode.') if isinstance(path, str) else path)
    if len(parts) >= 4 and parts[:2] == ['chapter_script', 'shots'] and isinstance(parts[2], int):
        return parts[2]
    return None


def _creative_evidence_paths(episode):
    """Enumerate only real text leaves expressible by the existing grounder."""
    from novel_manga.planning.preparation import _creative_path_parts
    paths = []
    def visit(value, path='', parts=()):
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f'{path}.{key}' if path else key, (*parts, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f'{path}[{index}]', (*parts, index))
        elif isinstance(value, str) and value.strip():
            try:
                if _creative_path_parts(path) == list(parts):
                    paths.append(path)
            except ValueError:
                pass  # Unsupported key/index syntax was never valid evidence.
    visit(episode)
    return ([path for path in paths if _creative_shot_position(path) is not None]
            + [path for path in paths if _creative_shot_position(path) is None])


def _creative_schema(paths, episode, sources):
    import copy
    schema = audit_schema(creative=True)
    authored, original = schema['properties']['issues']['items']['anyOf']
    shot_count = sum(_creative_shot_position(path) is not None for path in paths)
    branches = []
    if shot_count:
        items = {'type': 'integer', 'minimum': 0, 'maximum': len(paths)-1}
        authored['properties']['basis'].update(items=items, prefixItems=[
            {**items, 'maximum': shot_count-1}, items])
        authored['properties']['stage'] = {'type': 'null'}
        branches.append(authored)
    else:
        # Original-source findings do not require production-field evidence.
        original['properties']['basis'] = {'type': 'null'}
    source_ids = list(_creative_source_content(sources)[0])
    if source_ids:
        original['properties']['source_segment'] = {'type':'string', 'enum':source_ids}
    stages = list(dict.fromkeys(shot.get('index', i) for i, shot in
                               enumerate((episode.get('chapter_script') or {}).get('shots', []), 1)))
    for missing in (False, True):
        allowed = [0, *stages] if missing else stages
        if not allowed:continue
        branch = copy.deepcopy(original)
        kinds = original['properties']['kind']['enum']
        branch['properties']['kind']['enum'] = ['missing_event'] if missing else [k for k in kinds if k != 'missing_event']
        stage = {'type':'integer', 'enum':allowed}
        branch['properties']['stage'] = {'anyOf':[stage, {'type':'null'}]} if shot_count else stage
        if shot_count:
            fields = copy.deepcopy(branch)
            fields['properties']['basis'] = copy.deepcopy(original['properties']['basis']['anyOf'][0])
            for field in ('observed_time', 'expected_time'):
                fields['properties'][field] = copy.deepcopy(authored['properties'][field])
            branches.append(fields)
            branch['properties']['basis'] = {'type':'null'}
        branches.append(branch)
    schema['properties']['issues']['items']['anyOf'] = branches
    return schema


def _creative_field_metadata(issue, episode):
    """Derive only metadata fixed by the selected fields, never the finding itself."""
    basis = issue.get('basis')
    if not isinstance(basis, list) or len(basis) != 2:
        return  # The existing grounder reports the malformed pair.
    position = _creative_shot_position(basis[0])
    if position is None:
        raise ValueError('first evidence path must be a shot leaf text field')
    shots = (episode.get('chapter_script') or {}).get('shots') or []
    if position >= len(shots):
        raise ValueError('creative finding evidence path does not exist: '+str(basis[0]))
    issue['stage'] = shots[position].get('index')
    from novel_manga.planning.preparation import _creative_required_time
    for path, field in zip(basis, ('observed_time', 'expected_time')):
        path = path.removeprefix('episode.') if isinstance(path, str) else path
        if required := _creative_required_time(path):
            issue[field] = required


def creative_fingerprint(episode: dict, sources: dict[str, str]) -> str:
    import hashlib
    authored = _creative_view(episode)
    source_content, _ = _creative_source_content(sources)
    payload = json.dumps({'policy': AUTHORED_REVIEW_POLICY, 'episode': authored, 'sources': source_content},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _adjacent_shots(episode):
    shots = (episode.get('chapter_script') or {}).get('shots') or []
    def actual(shot):
        if shot is None:
            return None
        return {'stage': shot.get('index'),
                **{key: shot[key] for key in ('start_state', 'event', 'end_state') if key in shot}}
    return [{'stage': shot.get('index'), 'previous': actual(shots[i-1] if i else None),
             'next': actual(shots[i+1] if i+1 < len(shots) else None)}
            for i, shot in enumerate(shots)]


CREATIVE_TIME_CONTRACT = '时间语义严格为start_state/opening_frame/storyboard.frame=t=0；opening_frame.image_prompt整段均描述同一开场，场景总述中的人物和物件数量也属于t=0，不能只检查其中写着静态构图的一句；同一字段前后互斥也须报告。显式依次、随后、最终的动态描述不得直接当成零点已有。event/action_beats=during（含storyboard.action_beats）；end_state=t=end。同镜event/actions与turns的JSON字段排列不表示执行先后；只有明确触发、时间锚点或先后措辞才能确认顺序颠倒。相邻镜允许跳切；没有写出撤下或过渡动作不能证明状态不可能变化。“A、B存在”不等于“只有A、B”，是否排除C必须有实际文字证据。列出物件、所属或造型设计不等于声明当前穿戴、持握或位置；位置矛盾须有两侧明确相反的位置陈述，不能用名词并列覆盖本镜明确状态。'
CREATIVE_TIME_CONTRACT += (
    '音乐时间线使用宿主已实现的覆盖规则：music_plan.silences优先覆盖所有重叠cues，'
    '按各自start/end镜头锚点，在cue合流后仅将新增music bus置零；原对白、原生环境声与动作音效保持不变。'
    'cue跨度包含silence是合法的局部配乐留白，不是两项同时发声的矛盾；'
    '只按扣除silences后的最终有效发声区间判断音乐要求，不能仅因区间重叠要求拆cue或删除正确留白。'
    '每镜audio_plan.music_cue是编辑声音意图的自由描述，不是chapter_script.music_plan.cues[*].id的引用；'
    '使用music_plan时宿主按后混音计划的素材、anchors、fades和silences执行，不按逐镜描述标签查找cue id。'
    '标签名与cue id不同不构成矛盾；真实配乐冲突须对照query/reason、有效发声区间及本镜声音要求，不能只比较名称。'
    '带basis的问题至少一条（即第1项）必须是对应当前stage的真实chapter_script.shots数组元素下的叶文本路径；'
    'chapter_script.music_plan的reason等全局字段不能单独借任意stage挂靠，不得为满足协议添加无关镜头证据。')


def _creative_prompt(episode, sources, evidence_paths):
    episode = _creative_view(episode)
    source_content, _ = _creative_source_content(sources)
    return ('独立核对原始制作稿及原包文本，只报告有实际文本证据的确定性内容问题。'
            '必须按stage逐镜完成核对，再汇总全部成立的问题，不得只挑几例或省略同类问题的其他镜头。'
            '每镜先比较location/scene_id与camera的起始机位、当前场景和执行动作，'
            '不能把另一场的机位或动作当作当前镜头的合理补充；camera的后续运镜变化仍按其实际时间判断。'
            + CREATIVE_TIME_CONTRACT +
            '两个起始字段都写已经亮起不是矛盾；镜内加入人物或物件不能报成零点冲突。'
            '相邻镜只比较adjacent_shots中的实际文本及episode对应字段，允许跳切，'
            '不得推断未写出的世界状态，不建立世界状态表。'
            '制作字段问题的basis只能按observed、expected顺序给两个evidence_paths目录的整数下标（从0开始），'
            '目录以显式整数ID为键列出本次episode实际叶文本路径；直接选择所示ID，不要数数组位置。宿主按ID取回真实路径，不自行拼路径或省略层级。'
            '第1项只能选当前报告镜头的shot叶字段，第2项可选任意镜头或全局叶字段。第1项仅用于定位stage，不表示只能修这一项；两条证据仍双向比较，修正范围按事实与因果决定，可涉及任一侧及必要的相邻字段。'
            '不得用shot/对象/数组容器或第三条下标挂靠stage；'
            '凡带basis的observed和expected都必须留空，由宿主按basis顺序读取完整真实叶文本；模型只选择路径并判断，不复制或改写制作字段内容。'
            'basis始终引用episode制作字段，与source_segment/source_quote承担的原包文本证据分开；目录不含sources原包文本，original_text也不能把原包句子填成制作字段证据。同字段内部矛盾可重复同一下标。'
            '每条问题填写relation、problem和correction。带basis时stage填null，宿主按第1项实际shot.index派生；无basis的原文问题才选择实际stage，完全缺失事件仍可用stage=0。'
            '带basis的observed_time/expected_time必须从t=0、during、t=end、whole_shot中选择，不填null。固定字段仍由宿主按字段覆盖为规范时间；camera等没有固定时间的字段按原句判断，宿主不猜；不得用派生元数据代替内容判断。'
            '每条问题必须显式填写evidence_origin=original_text|authored_field，不由kind或problem推断。'
            'original_text无论kind和是否有basis都必须给有效source_segment，有basis时两类证据同时核对；'
            'authored_field仅用于纯制作字段矛盾，必须有basis、显式合法relation且source_segment/source_quote为空。'
            '修正办法必须保留原包事件顺序、原对白和该镜所属场景，不建议通过换成错误场景来凑字段一致。开场矛盾修复应统一受影响的start_state、opening_frame整段和storyboard.frame，不只改一处。'
            '派生visual_prompt/motion_prompt已从审核视图排除，不是独立作者事实或可用证据。'
            '原文问题的source_segment只能填sources的一个键，source_quote留空，由程序核对原文。'
            'source_readable=false时不得编造原文或问题；没有确定错误就issues=[]。\n'
            + json.dumps({'episode': episode, 'sources': source_content,
                          'evidence_paths': {i:path for i,path in enumerate(evidence_paths)},
                          'adjacent_shots': _adjacent_shots(episode)}, ensure_ascii=False))


def _ground_creative_answer(answer, episode, sources, *, evidence_paths=None):
    episode = _creative_view(episode)
    if (not isinstance(answer, dict) or type(answer.get('source_readable')) is not bool
            or not isinstance(answer.get('source_problem'), str)
            or not isinstance(answer.get('issues'), list)):
        raise ValueError('invalid creative audit answer')
    if not answer['source_readable'] and answer['issues']:
        raise ValueError('source_readable=false cannot include issues')
    source_content, aliases = _creative_source_content(sources)
    segments = [{'segment_id': key, 'text': text} for key, text in source_content.items()]
    script = episode.get('chapter_script') or {}
    errors = []
    for index, issue in enumerate(answer['issues']):
        try:
            if not isinstance(issue, dict):
                raise ValueError('invalid creative finding')
            for key in ('basis', 'observed', 'expected', 'observed_time', 'expected_time', 'relation'):
                if key in issue and issue[key] is None:
                    issue.pop(key)
            if issue.get('kind') not in {'speaker', 'action', 'missing_event', 'location', 'identity'}:
                raise ValueError('invalid creative finding kind')
            origin = issue.get('evidence_origin')
            if origin not in CREATIVE_EVIDENCE_ORIGINS:
                raise ValueError('invalid creative finding evidence_origin')
            if 'relation' in issue and issue['relation'] not in CREATIVE_RELATIONS:
                raise ValueError('invalid creative finding relation')
            if origin == 'authored_field' and 'relation' not in issue:
                raise ValueError('authored_field finding requires explicit relation')
            if origin == 'authored_field' and 'basis' not in issue:
                raise ValueError('authored_field finding requires basis')
            if origin == 'authored_field' and (issue.get('source_segment') or issue.get('source_quote')):
                raise ValueError('authored_field finding cannot include original-text evidence')
            try:
                if 'basis' in issue:
                    if evidence_paths is not None and isinstance(issue['basis'], list):
                        resolved = []
                        for path in issue['basis']:
                            if type(path) is int:
                                if not 0 <= path < len(evidence_paths):
                                    raise ValueError('creative finding evidence index is out of range: '+str(path))
                                path = evidence_paths[path]
                            resolved.append(path)
                        issue['basis'] = resolved
                        _creative_field_metadata(issue, episode)
                    ground_creative_finding(issue, episode)
                if origin == 'original_text':
                    segment = issue.get('source_segment')
                    issue['source_segment'] = aliases.get(segment, segment)
                    grounded_issues({'issues': [issue]}, script, segments)
                    cited = issue['source_segment']
                    if cited not in source_content:
                        raise ValueError('text audit supplied an unknown source segment')
                    grounded_issues({'issues': [issue]}, script,
                                    [{'segment_id': cited, 'text': source_content[cited]}])
            except (KeyError, TypeError) as error:
                raise ValueError('invalid creative finding: '+str(error)) from error
        except ValueError as error:
            errors.append(f'finding {index}: {error}')
    if errors:
        raise ValueError('\n'.join(errors))
    return answer


def _confirmation_indexes(confirmation, count):
    if isinstance(confirmation, dict) and 'decisions' in confirmation:
        decisions = confirmation['decisions']
        if not isinstance(decisions, list):
            raise ValueError('invalid creative audit confirmation decisions')
        indexes = [row.get('issue_index') for row in decisions if isinstance(row, dict)]
        if (len(indexes) != count or any(type(index) is not int for index in indexes)
                or set(indexes) != set(range(count))):
            raise ValueError('creative audit confirmation must cover every issue exactly once')
        if any(row.get('decision') not in {'keep', 'withdraw'}
               or not isinstance(row.get('reason'), str) or not row['reason'].strip()
               for row in decisions):
            raise ValueError('invalid creative audit confirmation decision')
        return {row['issue_index'] for row in decisions if row['decision'] == 'keep'}
    # Previously recorded audits retain their original confirmation shape.
    if (not isinstance(confirmation, dict) or not isinstance(confirmation.get('confirmed'), list)
            or not isinstance(confirmation.get('reason'), str)):
        raise ValueError('invalid creative audit confirmation')
    indexes = confirmation['confirmed']
    if any(type(index) is not int or not 0 <= index < count for index in indexes):
        raise ValueError('invalid creative audit confirmation index')
    return set(indexes)


def _confirmed_answer(raw_answer, confirmation):
    if not raw_answer['source_readable'] or not raw_answer['issues']:
        if confirmation is not None:
            raise ValueError('unexpected creative audit confirmation')
        return raw_answer
    for issue in raw_answer['issues']:
        if 'relation' in issue and issue['relation'] not in CREATIVE_RELATIONS:
            raise ValueError('invalid creative finding relation')
        if issue.get('evidence_origin') == 'authored_field' and 'relation' not in issue:
            raise ValueError('authored_field finding requires explicit relation')
    keep = _confirmation_indexes(confirmation, len(raw_answer['issues']))
    return {**raw_answer, 'issues': [issue for i, issue in enumerate(raw_answer['issues'])
                                      if i in keep and ('relation' not in issue
                                                        or issue['relation'] == 'contradiction')]}


def _cached_creative(cache_path, fingerprint, episode, sources):
    saved = read(cache_path, {})
    if not saved or saved.get('fingerprint') != fingerprint:
        return None
    try:
        raw_answer = saved['raw']
        confirmation = saved['confirmation']
        _ground_creative_answer(raw_answer, episode, sources)
        if raw_answer['source_readable'] and raw_answer['issues'] and confirmation is None:
            raise ValueError('cached creative audit lacks confirmation')
        expected = _confirmed_answer(raw_answer, confirmation)
        if saved['answer'] != expected:
            raise ValueError('cached creative audit answer does not match its evidence')
        return saved
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('invalid cached creative audit: '+str(error)) from error


def feedback_issues(issues):
    """The source documents are already present; keep their references, not copies."""
    return [{**issue, 'source_quote': ''} if issue.get('evidence_origin') == 'original_text'
            and issue.get('source_segment') else dict(issue) for issue in issues]


def creative_audit(episode: dict, sources: dict[str, str], *, cache_path: Path, settings=None) -> dict:
    import copy
    episode = _creative_view(episode)
    fingerprint = creative_fingerprint(episode, sources)
    cached = _cached_creative(cache_path, fingerprint, episode, sources)
    if cached is not None:
        return cached
    options = {'settings': settings} if settings is not None else {}
    from novel_manga.llm.client import ask_json, obj
    evidence_paths = _creative_evidence_paths(episode)
    schema = _creative_schema(evidence_paths, episode, sources)
    prompt = _creative_prompt(episode, sources, evidence_paths)
    raw_answer = ask_json([{'type': 'text', 'text': prompt}], schema,
                          name='pre_render_story_audit', max_tokens=3000, timeout=240, **options)
    raw_response_history=[{'name':'pre_render_story_audit','response':copy.deepcopy(raw_answer)}]
    initial_answer=copy.deepcopy(raw_answer)
    confirmation = None
    def protocol_failure(operation, error):
        import uuid
        from novel_manga.llm.client import ModelOutputMalformed
        diagnostic = cache_path.with_name(f'{cache_path.stem}.protocol-error-{uuid.uuid4()}.json')
        atomic_write_json(diagnostic, {'operation':operation, 'fingerprint':fingerprint,
            'error':str(error), 'raw_response_history':raw_response_history,
            'confirmation':confirmation})
        failure = ModelOutputMalformed(operation, str(diagnostic))
        failure.args = (f'{failure}; protocol validation: {error}',)
        raise failure from error
    try:
        _ground_creative_answer(raw_answer, episode, sources, evidence_paths=evidence_paths)
    except ValueError as error:
        try:
            _ground_creative_answer({**initial_answer,'issues':[]},episode,sources,evidence_paths=evidence_paths)
        except ValueError as invalid_header:
            protocol_failure('pre_render_audit_evidence',invalid_header)
        merged=copy.deepcopy(initial_answer);invalid=[]
        for index,issue in enumerate(initial_answer['issues']):
            one={**initial_answer,'issues':[copy.deepcopy(issue)]}
            try:_ground_creative_answer(one,episode,sources,evidence_paths=evidence_paths)
            except ValueError:invalid.append(index)
            else:merged['issues'][index]=one['issues'][0]
        correction_schema=copy.deepcopy(schema['properties']['issues']['items'])
        for branch in correction_schema['anyOf']:
            branch['properties']['issue_index']={'type':'integer','enum':invalid}
            branch['required'].append('issue_index')
        correction = ask_json([{'type': 'text', 'text': prompt +
            '\n仅修下面失败项的字段引用、原文区段或时间；每项保留宿主issue_index并各返回一次。'
            '已有效的问题由宿主保留，不在本次回复。不得增删、替换问题或修改kind/evidence_origin/relation/problem/correction。'
            'basis仍选真实整数ID；observed/expected/source_quote保持原值，带basis时stage填null，时间选择规范枚举。'
            + '\n具体校验错误：' + str(error) + '\n'
            + json.dumps({'issues':[{**initial_answer['issues'][index],'issue_index':index}
                                    for index in invalid]}, ensure_ascii=False)}],
            obj({'issues':{'type':'array','minItems':len(invalid),'maxItems':len(invalid),'items':correction_schema}}),
            name='pre_render_audit_evidence', max_tokens=3000, timeout=240, **options)
        raw_response_history.append({'name':'pre_render_audit_evidence','response':copy.deepcopy(correction)})
        try:
            corrected=correction.get('issues') if isinstance(correction,dict) else None
            indexes=[row.get('issue_index') for row in corrected if isinstance(row,dict)] if isinstance(corrected,list) else []
            if (len(indexes)!=len(invalid) or any(type(i) is not int for i in indexes)
                    or set(indexes)!=set(invalid)):
                raise ValueError('creative evidence correction must cover every invalid issue_index exactly once')
            allowed={'basis','source_segment','observed_time','expected_time','stage'}
            for corrected_issue in corrected:
                index=corrected_issue['issue_index'];original=initial_answer['issues'][index]
                if any(corrected_issue.get(key)!=original.get(key)
                       for key in (set(original)|set(corrected_issue))-allowed-{'issue_index'}):
                    raise ValueError('creative evidence correction changed finding content: '+str(index))
                merged['issues'][index]={**original,**{key:corrected_issue[key] for key in allowed if key in corrected_issue}}
            raw_answer=merged
            _ground_creative_answer(raw_answer, episode, sources, evidence_paths=evidence_paths)
        except ValueError as error:
            protocol_failure('pre_render_audit_evidence', error)
    if raw_answer['source_readable'] and raw_answer['issues']:
        confirmation = ask_json([{'type': 'text', 'text':
            '请复核制作稿问题单，逐项独立剔除误报；撤销一项不得撤销同镜其他真实问题。'
            '必须阅读完整episode、sources和相邻镜实际文本。'
            '所有带basis的observed/expected都是宿主按路径读取的完整真实制作字段，原包证据另按source_segment核对；路径存在仅证明引用有效，不证明problem成立。'
            '带basis的stage和固定字段时间也由宿主派生；必须按这些真实时间重判，不能沿用初审把end_state当作开场或把镜内动作当作零点的解释。第1项仅定位报告镜头，不限制事实需要的修正范围。'
            '必须重新核实problem和correction是否确被这些实际字段支持，不能沿用初审声称但当前字段中不存在的事实；无实际依据则withdraw。'
            + CREATIVE_TIME_CONTRACT +
            '复核每条basis给出的两段原句及对应时间；不得用event的during变化覆盖两个t=0源字段的互斥要求。'
            '撤销时须逐项说明两段原句为何能在所标时间同时成立。两个起始字段均已亮不是矛盾；'
            'event/action_beats中的镜内变化是during，不得当成t=0冲突。'
            '保留有实际字段或原文证据的场景错贴、零点矛盾及核心剧情错误；允许跳切和等义改写。'
            'compatible/underspecified只作候选，最终只消费confirmed contradiction。'
            '每条输入有issue_index，按该编号逐项返回decision=keep或withdraw及同一条reason，不能把镜头stage当成issue_index。'
            '全部问题各返回一次；decision必须与该条reason的结论一致，可以全部withdraw。\n'
            + json.dumps({'episode': episode, 'sources': _creative_source_content(sources)[0],
                          'issues': [{**issue, 'issue_index':index}
                                     for index,issue in enumerate(feedback_issues(raw_answer['issues']))],
                          'adjacent_shots': _adjacent_shots(episode)}, ensure_ascii=False)}],
            obj({'decisions': {'type': 'array', 'items': obj({
                'issue_index': {'type': 'integer', 'minimum':0, 'maximum':len(raw_answer['issues'])-1},
                'decision': {'type':'string', 'enum':['keep','withdraw']},
                'reason': {'type':'string'}})}}),
            name='pre_render_audit_confirmation', max_tokens=2000, timeout=240,
            retry_truncated=True, **options)
        try:
            _confirmation_indexes(confirmation, len(raw_answer['issues']))
        except ValueError as error:
            protocol_failure('pre_render_audit_confirmation', error)
    result = {'fingerprint': fingerprint, 'answer': _confirmed_answer(raw_answer, confirmation),
              'raw': raw_answer, 'confirmation': confirmation,'raw_response_history':raw_response_history}
    atomic_write_json(cache_path, result)
    return result
