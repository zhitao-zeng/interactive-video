"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations


from novel_manga.util import read_json


def initial_prompt(ctx):
    drafted = ((ctx.work_dir/'output/episode.json').is_file()
               and read_json(ctx.run_dir/'source-draft-call.json', {}).get('status') == 'candidate')
    common = ((
        'Use the supplied check results and existing production draft. Consult the relevant '
        'episode-production contract sections when a repair requires them. '
        'Inspect actual reference images relevant to the shots you change. ' if drafted else
        'Read input/manifest.json and the episode-production skill/output contract before writing requests. '
        'Inspect the actual reference images you select. ') +
        'Prefer structured H3 shot bodies: the host composes all fixed syntax. '
        'Use the retained production profile, '
        'speech rules, source evidence and remaining generation budgets. '
        'Commands are python3 /work/tools/episode.py check, render, apply, review, mix_audio, select_music, finish. '
        'status.music_selection exposes optional SoulTuner search and explicit adoption. Search returns unreviewed local candidates; '
        'rank1 is not acceptance. Read supplied audio evidence and its coverage, keep uncertain/rejected candidates unapplied, '
        'and use select_music action=apply only with an accepted reason, source quote, source interval and gain. '
        'Keep no-music source constraints. Music adoption preserves native dialogue/video and requires actual mixed-movie review; '
        'sampled audio analysis does not certify full-song or full-film listening. '
        'For story-timed music, follow status.music_selection.authored_plan and use action=apply_timeline with shot start/end anchors, '
        'source intervals, cue fades/gains and explicit silence windows. Reassembly resolves these anchors to actual cut timings. '
        'Music gaps mute only the added music, never native dialogue or environment sound. Assembly music failures go back to select_music; '
        'they do not themselves justify video retakes. Postmix clips keep native non_diegetic_music=None; assess the added music on the assembled film. '
        'audio_mix in current status lists source-reviewed speech-free environmental assets. '
        'For an assembly sound defect, mix_audio output/audio-mix.json can adopt an additive room-tone bed '
        'with candidate_id, gain_db, verbatim source_quote and reason. The host source-checks it, keeps native speech, '
        'copies the video stream and reruns media QC. This is an acoustic design decision, not permission to add arbitrary noise just to pass a meter. '
        'Use supported audio assembly for genuine ambience gaps; preserve passed native takes. '
        'check/render take an optional file; apply defaults to output/repair.json. '
        'Use doctor to request host service diagnostics when an endpoint fails; '
        'yield for its result like other host jobs. Diagnostics do not prove content acceptance. '
        'Use these commands to check, render, review, apply supported repairs, '
        'and finish this episode. When a tool returns yield=true, end this CLI turn immediately. '
        'The host will execute it and resume this same session with the result; it owns waiting. '
        'The current host state and previous operation result are supplied below; do not query '
        'status again unless you need a later state. Render validates your draft and returns '
        'the existing independent review with its evidence; use check for draft diagnostics, '
        'and review when evidence is missing/stale or full media context is needed. '
        'Automatic render evidence expands attention_clips only; retained_clips already '
        'pass current checks and need no repair decision. If episode_technical_issues is present, locate the responsible '
        'clips and repair only those; the technical inspection targets do not all need keep decisions. '
        'Use the complete screenplay '
        'and all-clip summary for adjacent context; explicit review exports all clips. '
        'When render returns clips_ready with assembly_pending=true, use its clip evidence '
        'to repair or finish; whole-episode encoding waits for repairs, so a missing movie '
        'alone is not a reason to render again. Finish assembles the final candidate and '
        'runs technical QC; content_passed alone does not mean final acceptance. '
        'If a prepared plan or previous takes exist, reuse that plan and media; '
        'Follow status.retry_policy and remaining_takes: once an allowed correction or recheck '
        'is exhausted, stop that clip and finish with the actual unresolved result; do not loop '
        'on the same rejection or reopen a finished episode. '
        'Use remaining_input_attempts, remaining_rechecks, conditional_extra_takes and input_repairs for current permissions; '
        'old blocked messages are history. Historical tool_error records do not spend input attempts. '
        'remaining_takes includes unspent conditional extras, which require a validated repair and input preflight before generation. '
        'Review the current evidence first: keep requests an independent recheck if a verdict may be wrong. '
        'For a real defect, submit a concrete edit/input/retake; an extra take activates only after actual-input preflight. '
        'Zero new input attempts can still allow an explicitly offered continuation or one revision. '
        'For argument_errors, correct the named JSON fields using the returned shape/scope; '
        'resuming or rendering cannot fix malformed arguments. Keep existing revision and take limits. '
        'Batch related file reads and independent image inspections. Save the draft before testing. '
        'Use one check for all clips, fix reported issues together, then submit render. '
        'Do not probe the checker with many alternate phrasings: use structured h3.shots and '
        '<DIALOGUE_n> slots so the host supplies protected bindings and timing. '
        'review and apply supported repairs instead of resubmitting a whole episode file. '
        'Read the actual latest frames, raw/corrected ASR and '
        'independent verdicts. Finish with the actual accepted or blocked outcome.\n'
    )
    if ctx.input_mode == 'source' and (ctx.work_dir/'input/reading/character-phases.json').is_file():
        common += ('Use input/reading/character-phases.json for current chapter appearance and clothing; '
                   'these explicit phase fields override the base Bible look. Inspect the corresponding '
                   'selected cards rather than reverting a current phase to its older base design.\n')
    if ctx.input_mode == 'script':
        from .local_preparation import active
        if active(ctx):
            return common + (
                'This is an existing provisional screenplay with a local preparation failure. '
                'Read input/preparation/exception.json and input/latest-preparation.json when present. '
                'Keep its cuts, durations, dialogue and valid choices. Do not write a new episode.json. '
                'The host stages the existing inputs without video; use apply action=input for '
                'source-backed speaker, scene, character/reference and wearing corrections, '
                'action=edit for one invalid English request, or accept_risk for an explicitly '
                'offered reference uncertainty. Input may build needed scene/character cards. '
                'render rechecks the current source, fills missing references and translates only '
                'missing/invalid requests before normal H3 and media gates. Old preliminary findings '
                'are evidence, not proof of a current defect. Use current results and finish normally.\n')
        return common + (
            'This is script input mode. The existing approved chapter_script.json, clip_plan.json '
            'and selected assets are authoritative. Preserve every shot, story event, exact dialogue '
            'and speaker, timing, camera, reference selection/order and source binding. '
            'Keep these accepted choices except for a concrete source/input defect corrected through the input tool. '
            'Do not replan the whole episode or impose a new episode length. '
            'For a confirmed source/input conflict, use apply with action=input, a verbatim source_quote '
            'and supported cast/speaker/scene/appearance/wearing/sound corrections. '
            'shots can correct in_frame/extras/listeners/actions, event/start_state/end_state, '
            'speaker_map and sfx. characters requests a missing source actor card after adoption too. '
            'The host checks source support, builds scoped cards and recompiles dependent clips retaining cuts and words. '
            'output/requests.json is already seeded with the existing final requests. '
            'Read manifest.request_draft: reusable lists structurally valid requests and needs_work '
            'names missing or invalid clips. Preserve reusable text unless you find a concrete '
            'source, image or request defect. Edit only those defects and missing requests; '
            'style_warnings alone do not require a rewrite. Use the host check diagnostics '
            'instead of writing duplicate validators or shell probes. Perform your semantic/image '
            'review, and execute the existing episode through the supplied tools. '
            'Keep repairs within the existing protected request contract.\n'
        )
    if (ctx.run_dir/'prepared.json').is_file():
        return common + (
            'This source episode already has an adopted plan. You are handling an exception '
            'in that existing episode. Read the current status and '
            'input/latest-render.json and input/latest-review.json when present; they describe '
            'the current requests and actual takes. Do not redo initial source drafting or '
            'treat an old source-draft-check as a new failure. Preserve existing media and its '
            'physical-take budget. If can_adopt_draft is false, use apply for remaining issues '
            'instead of rewriting the screenplay or resubmitting the initial draft. '
            'Use source evidence for cast/speaker/appearance/wardrobe/scene/sound corrections. '
            'Input can correct references and request missing source actor cards with characters. '
            'Use shots.in_frame/extras/listeners/actions, speaker_map and sfx as needed; '
            'unnamed crowds remain extras unless an actual speech owner requires a card. '
            'accept_risk can explicitly acknowledge a '
            'current wearer-card or scene-people uncertainty for one clip before rendering; '
            'it does not pass any media gate. keep is only for an actual existing video.\n'
        )
    if read_json(ctx.work_dir/'input/manifest.json', {}).get('preparation_exception'):
        return common + (
            'This unfilmed chapter failed normal planning or preparation. Read '
            'input/preparation/exception.json and its listed original candidate and audit files. '
            'The failed screenplay is provisional, not an approved frozen script. Diagnose and '
            'repair the recorded cause, preserving its valid story choices instead of repeating '
            'the normal writer. Use input/reading/locations.json when present: these names and '
            'chapter memberships come from the book reading; their descriptions are evidence, '
            'not ready-made empty-scene prompts. A missing picture is work to perform: write '
            'the corrected provisional output/episode.json and request source-backed missing '
            'characters/locations through apply action=input using the draft-assets contract. '
            'Do not substitute an unrelated available scene or remove a required source actor '
            'to avoid creating a reference. Use the existing source output contract, check and '
            'render; the host retains source, reference, request and media acceptance gates.\n')
    if (ctx.run_dir/'source-draft-call.json').is_file():
        return common + (
            'This is source input mode with a normal production draft already attempted. '
            'You are handling an exception. Read input/source-draft-check.json when present '
            'and output/episode.json; correct the reported issues while keeping valid story '
            'choices. The draft is provisional: you may repair its screenplay, references, '
            'cutting and H3 bodies against the complete original source and actual assets. '
            'input/source-draft-raw.json, when present, preserves the normal writer response '
            'as ordered clip/shot beats; reuse its valid choices if host binding failed. '
            'If the model call produced no usable draft, author it from the supplied source. '
            'Preserve essential causes, dialogue ownership and the ending within the manifest '
            'duration target. Use the existing source output contract and execute the tools.\n'
        )
    target = ctx.target_seconds
    duration = (f'{target[0]:g}–{target[1]:g} seconds' if target else
                'the duration specified in input/manifest.json')
    return common + (
        'This is source input mode. Read the complete original chapter. Create the episode '
        'screenplay, ordered shots and English shot directions yourself from that source and '
        f'the supplied assets. Aim for {duration}, with each clip no longer than '
        f'{ctx.clip_seconds_max:g} seconds. Preserve source-to-shot evidence and meaningful '
        'adaptation choices. Prefer clips[].h3={shots:[English body with <DIALOGUE_n> slots],soundscape:English}. '
        'The host fills reference definitions, dialogue, voice IDs, durations and cuts. '
        'Write output/episode.json using the output contract, self-review '
        'the complete episode, and execute it through the supplied tools.\n'
    )


def continuation_prompt():
    return (
        'Continue this same saved episode session and existing workflow. The previous CLI turn '
        'ended and the host operation has now settled. The current host state and operation '
        'result are supplied below; continue directly from them. Read the saved evidence '
        'when making a creative or repair decision. Render already exports its independent '
        'review evidence; call review again only for missing/stale evidence or when full media context is needed. '
        'Current status.audio_mix exposes mix_audio for a source-backed additive environmental bed while preserving native speech/video and rerunning media checks. '
        'Current repair API: an edit may use h3={shots:[English bodies with <DIALOGUE_n> slots], '
        'soundscape:English} instead of prompt_h3. Prefer this short form; the host preserves '
        'reference bindings, original speech, durations, cuts and music. Read only the evidence '
        'needed for the current unresolved clips. Use attention_clips for repair decisions; '
        'retained_clips need no decision or repeated inspection. '
        'For episode_technical_issues, use the inspection targets to locate and repair only responsible clips; '
        'do not submit keep decisions merely to cover all technical inspection targets. '
        'Batch independent decisions in one apply. A clip with no remaining take allowance '
        'does not cancel eligible repairs for other clips. '
        'Current remaining_input_attempts, remaining_rechecks, conditional_extra_takes and input_repairs override historical blocked messages. '
        'repair_actions lists apply operations permitted NOW, including edit/retake/input preparation for a pending extra. '
        'Submit a listed supported correction; the host handles later activation. A missing planned line can be repaired '
        'with edit or a justified retake while preserving its words; it does not require a source rewrite. '
        'A previous stopped result is not acceptance and does not revoke a current allowance. '
        'Use current repair_capabilities: submit a supported correction/resume, or identify the exact '
        'missing capability, rejected source decision, exhausted allowance or external dependency. '
        'Do not repeat the old completion narrative instead of an operation. '
        'remaining_takes includes unspent conditional extras; prepare a validated repair before those can generate. '
        'Inspect current evidence; use keep for an independent recheck when a verdict is doubtful, '
        'otherwise submit a concrete edit/input/retake. Extra generation activates only after input preflight; '
        'risk acceptance alone is not a repair plan. First run python3 /work/tools/episode.py check output/repair.json to catch '
        'argument errors in this turn without spending an input attempt or handing off to a slow tool. '
        'A historical tool_error did not spend an input attempt. Use an offered continuation or revision '
        'when available; do not repeat a missing or malformed saved decision through resume. '
        'For an already registered character state, inspect input/phases.json and its actual card; '
        'input phases.characters.<name>={reuse_asset_id:<existing phase asset ID>} reuses that saved design. '
        'Do not rewrite appearance or wardrobe merely to apply the same state to another clip. '
        'After adoption, action=input supports scoped shots.in_frame/extras/listeners/actions and '
        'event/start_state/end_state/sfx corrections, plus speaker_map from old to source-correct speech owners. '
        'characters can request a missing named visible actor or actual speech owner with appearance/wardrobe. '
        'For source-authored speech_overflow, input shots[].turn_texts may shorten each existing turn in order; '
        'the host preserves the speaker/mode and independently checks essential meaning against the source. '
        'Aim for 4–5 Chinese characters per second, never more than 6. This permission does not apply to frozen script input. '
        'A character missing from the old selected references is not prohibited when corrected shots '
        'and original source establish that identity. Existing cards use phases for changed clothing. '
        'Do not put a missing identity into phases; request characters instead. Unpictured crowds can remain extras. '
        'Before source adoption, missing assets can be requested with apply action=input, '
        'a verbatim source_quote, and characters (name to appearance/wardrobe/gender/age) '
        'or locations (name to empty-setting description). Read the returned actual images '
        'and bind their IDs in the provisional draft; asset creation does not start video. '
        'When a tool returns yield=true, end this turn so the host can execute it. '
        'Complete the remaining checks, review or supported repair within the existing budget '
        'and input authority. Run finish when accepted or when the episode must stop, and '
        'end this turn after its acceptance; the host will complete it.\n'
    )


def task_instructions(ctx):
    """Attached by the controller once on every initial or resumed production turn."""
    return native_speech_instructions(ctx)+first_frame_instructions(ctx)


def first_frame_instructions(ctx):
    from novel_manga.media.first_frames import enabled
    if not enabled(ctx) and read_json(getattr(ctx,'source_novel_dir',ctx.novel_dir)/'profile.json',{}).get('h3_input_mode')!='first-frame':return ''
    speech=native_speech_instructions(ctx)
    return (
        'This edition uses OPTIONAL FIRST-FRAME MODE. Every authored shot needs a newly API-generated opening image. '
        'Use the existing first_frames host command BEFORE render. Do not use old video frames or derived pictures. '
        'For creator-authored opening_frame, use its image_prompt and visible_characters directly; omit those fields in the tool row to inherit them. '
        'Your production job is binding actual registered cards, executing the image API, reviewing and repairing. '
        'The host appends the selected style rules to the image request and supplies them to image review; '
        'keep the creator opening_frame.image_prompt unchanged and correct source conflicts through input repair. '
        'If the opening needs a creative change, update source opening_frame through input repair first; do not silently replace the creator prompt. '
        'Read first_frames in input/current-status.json for the exact schema, per-clip remaining image candidates and current/stale state. '
        'Write output/first-frames.json with frames:[{clip_id,image_prompt,image_references,visible_characters,extra_references,h3,source_quote}]. '
        'PREFER h3={shots:[English body with <DIALOGUE_1>, <DIALOGUE_2> in original order],soundscape:English environment sound}; '
        +('the existing host compiler fills protected dialogue, Subject definitions, actual Picture mapping and timing. 'if speech else
          'the existing host compiler fills protected dialogue, Subject/Audio definitions, actual Picture mapping and timing. ') +
        'Each clip has exactly one shot here, so h3.shots is a one-item array. Do not spend turns reconstructing fixed six-section syntax or auditing source code. '
        'Call python3 /work/tools/episode.py first_frames output/first-frames.json; end the turn on yield=true. '
        'For the user-selected per-shot image-to-video stream, set top-level render_ready:true in that file. '
        'Then each accepted opening immediately queues its first H3 take while later images continue; '
        'rejected openings block only their own clips. Existing videos, including failed takes, are never automatically reshot by this stream. '
        'When the streamed batch returns, use review on existing videos and apply confirmed repairs within retained budgets; '
        'do not run render merely to collect the same first takes again. Normal render remains available after an approved repair. '
        'image_references are registered ORIGINAL source image paths from input/catalog.json, ordered to match your image prompt; '
        'inspect the actor/scene cards and source/layout before authoring. Keep the face, stable clothing, readable geometry and opening camera/held props correct. '
        'Make one single cinematic frame, never a collage or storyboard. New image for each shot, not a crop/reuse of another generated shot. '
        'visible_characters lists people actually visible at time zero, not merely present or speaking offscreen. '
        'extra_references selects only original clip image paths for new people/content NOT covered by the opening; empty is normal when all needed content is visible. '
        'The actual H3 Picture 1 is ALWAYS the complete generated opening, also native keyframe at frame_index=0. '
        +('Extra video pictures follow in listed order as Picture 2 onward. Preserve logical Subject and speaker IDs; no Audio IDs are supplied. 'if speech else
          'Extra video pictures follow in listed order as Picture 2 onward. Preserve the ORIGINAL logical Subject IDs and Audio IDs. ') +
        'All subjects covered by the opening bind to Picture 1; a new actor absent at time zero binds to its supplied extra picture. '
        'An offscreen source speaker absent from the frame and not supplied an extra card must have a Subject definition without a Picture tag; do not invent their face in the frame. '
        'Alternatively a complete six-section prompt_h3 is supported with that ACTUAL image numbering, exact original Chinese <d> speech/speaker IDs and existing shot timing; '
        'all direction outside <d> is English. Preserve source events and do not copy obsolete card Picture numbers. '
        'The opening image need not contain future actions; video moves from its stated opening. Reference retention must respect the opening composition. '
        'Batch all independent shot candidates in one first_frames call. Rejected images and supplier receipts remain; revise only the failed opening image_prompt/source inputs. '
        'Identical image inputs reuse/resume the original candidate, never spend another paid attempt; max three image candidates per clip. '
        'The host independently checks actual pixels and refuses video until the accepted opening is current. '
        'A source opening/camera/asset repair requires first_frames again. A motion/dialogue-only repair keeps the current opening. '
        'After successful frames, render without a requests file to retain the adopted source-bound plan. Review and repair through the existing tools; '
        'whole-film independent failures returned by finish must be repaired through apply/first_frames/render within retained limits. '
        'Old versions/counts stay unchanged. A 3-second authored shot gets a 4-second native request while retaining its 3-second source timing. '
        'Read input/latest-first-frames.json for actual image review evidence and per-clip next actions. '
    )


def native_speech_instructions(ctx):
    from novel_manga.story.voice_delivery import native_speech_selected
    profile=read_json(ctx.novel_dir/'profile.json',{})or read_json(getattr(ctx,'source_novel_dir',ctx.novel_dir)/'profile.json',{})
    if not native_speech_selected(profile):return ''
    return ('TASK SPEECH POLICY: no audio reference, with performance before each dialogue. '
        'Voice files in the asset library do not authorize sending them. The actual native audio-reference list must be empty. '
        'Keep dialogue, picture references and speaker IDs; omit Audio definitions, retention and audio-reference labels. '
        'For every source turn, emotion must hold one usable ENGLISH performance direction tied to that event and utterance: '
        'useful pitch, pace, volume, breath, emphasis or change after a discovery. Ordinary lines need only the detail that matters. '
        'A repeated age/gender/words-per-minute specification is not per-line performance. '
        'The host binds this emotion direction immediately before its unchanged utterance; do not duplicate it in h3.shots or put it in soundscape. '
        'If the adopted screenplay has only generic or untranslated emotion, correct its source turns through the existing input tool before first_frames/render. '
        'Retain words, owners, timing and music/physical-sound plan. No ASR or picture check certifies natural acting; listening remains a separate acceptance scope. ')
