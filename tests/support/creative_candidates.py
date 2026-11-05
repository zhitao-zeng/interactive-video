"""Small complete creative candidates shared by source-contract tests."""

def episode():
    shots = [{'index': i, 'scene_id': 'entry', 'location': '入口',
              'duration_seconds': 15, 'purpose': '核对入口规则',
              'start_state': '站在入口', 'event': '读牌后停步', 'end_state': '停在牌旁',
              'camera': '固定中景，牌和旅客同框', 'characters': ['旅客'], 'in_frame': ['旅客'],
              'turns': [], 'sfx': '风声', 'audio_plan': {'music_cue': '无配乐'},
              'source_quote': '入口有一块牌，旅客看完后停步。'} for i in range(1, 13)]
    return {'title': '测试', 'logline': '旅客核对入口规则。', 'adaptation_notes': ['保留原始事实'],
            'chapter_script': {'shots': shots},
            'clips': [{'shot_indexes': [i], 'request_seconds': 15} for i in range(1, 13)],
            'interaction': {'question': '下一步？', 'selected': None, 'options': [
                {'id': 'A', 'label': '查入口', 'reason': '入口有牌'},
                {'id': 'B', 'label': '看周围', 'reason': '周围还没核对'}]},
            'spatial_plan': {'scenes': [{'scene_id': 'entry', 'anchors': ['入口', '牌'],
                'placed': ['旅客'], 'shot_indexes': list(range(1, 13)),
                'assumptions': ['距离是调度方案，未经测量']}]},
            'self_review': {'source': '已核对', 'open_issues': []}}

