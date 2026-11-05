from novel_manga.story.voice_delivery import NATIVE_SPEECH_POLICY

def case(offscreen=False):
    return {'clip_id':'clip_01','request_seconds':5,'shot_indexes':[1],
        'shot_timing':[{'seconds':5}], 'h3_speech_policy':NATIVE_SPEECH_POLICY,
        'references':[{'role':'character','name':'A','path':'actor.png'}],
        'dialogue_bindings':[{'stage':1,'speaker_name':'A','delivery_mode':'offscreen_dialogue'if offscreen else'visible_dialogue',
            'text':'先等等。','emotion':'Her voice drops briefly as she notices the step, then ends firmly.'}]}

