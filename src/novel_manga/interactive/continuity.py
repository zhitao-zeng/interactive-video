"""Apply source-backed room states to the existing screenplay fields."""
from __future__ import annotations

import copy

from novel_manga.models.directing import HandoffState


def physical_text(state: HandoffState) -> str:
    return '；'.join(name + description for name, description in state.physical.items())


def repair_room_shots(script: dict, edits: dict[int, dict]) -> dict:
    """Keep room membership independent from the cut's visible cast.

    The caller supplies source-backed start/end states and actions. This does
    not infer arrivals, seat assignments, exits, or prop removal from names.
    """
    result = copy.deepcopy(script)
    for shot in result['shots']:
        edit = edits.get(shot['index'])
        if edit is None:
            continue
        start, end = edit['start'], edit['end']
        if not isinstance(start, HandoffState) or not isinstance(end, HandoffState):
            raise TypeError('Room states must use the existing HandoffState contract')
        visible = list(edit['visible'])
        props = list(edit['props'])
        occupants = [name for name in dict.fromkeys([*start.physical, *end.physical]) if name not in props]
        if set(visible) - set(occupants):
            raise ValueError('A visible person must be present in the supplied room state')
        shot['characters'] = occupants
        shot['in_frame'] = visible
        shot['listeners'] = []
        shot['props'] = props
        shot['visual_prompt'] = ('房间状态：' + physical_text(start)
            + '。本镜取景：' + ('、'.join(visible) or '无人')
            + '。未入镜者仍在原位置；镜外不等于离场，不能清空其座位或移走行李')
        shot['motion_prompt'] = edit['event']
        shot['end_state'] = physical_text(end)
    return result
