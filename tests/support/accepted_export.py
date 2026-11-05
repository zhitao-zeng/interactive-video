"""Current export acceptance fixture shared by status and publication tests."""
from types import SimpleNamespace

from novel_manga.application.review import final_cut
from novel_manga.interactive import delivery
from novel_manga.util import atomic_write_json


def accepted_case(tmp_path, monkeypatch, *, versioned=True, assembly_matches_export=False):
    from novel_manga.application.review import final_audio

    episode = tmp_path/'book_1'
    run = tmp_path/'run'
    destination = tmp_path/'delivery/one'
    version = destination/'versions/export-001' if versioned else destination
    for path in (episode, run, version):
        path.mkdir(parents=True)
    movie = version/'episode01.mp4'
    movie.write_bytes(b'new export')
    cover = version/'cover.jpeg'
    cover.write_bytes(b'cover')
    if versioned:
        (destination/'cover.jpeg').write_bytes(b'stale root cover')
    atomic_write_json(episode/'clip_plan.json', {'clips':[]})
    atomic_write_json(episode/'music_policy.json', {'intent':'quiet'})
    report = {
        'assembly':{'final_video':str(movie if assembly_matches_export else tmp_path/'native.mp4'),
                    'thin_passed':True},
        'quality_review':{'passed':True,'content_passed':True},
    }
    final = {
        'policy':final_cut.POLICY,
        'movie':str(movie),
        'context':final_cut.context(episode,report,movie),
        'complete':True,'passed':True,'issues':[],'errors':[],
    }
    model_dir = tmp_path/'audio-model'
    model_dir.mkdir()
    (model_dir/'weights.bin').write_bytes(b'weights-v1')
    source_identity, _ = final_audio._source_facts(episode)
    audio = {
        'movie':str(movie),'complete':True,'passed':True,'issues':[],'errors':[],
        'policy':final_audio.POLICY,
        'identity':{
            'movie':final_audio._file_state(movie),
            'model':final_audio._model_state(model_dir),
            'audio_source_root':str(tmp_path/'audio-source'),
        },
        'source_identity':source_identity,
    }
    report.update(final_cut_review=final,final_audio_review=audio,
                  final_audio_review_required=True)
    atomic_write_json(episode/'thin_media_report.json',report)
    runner = SimpleNamespace(context=SimpleNamespace(
        episode_dir=episode,run_dir=run,
        settings=SimpleNamespace(width=1080,height=1920,font_path=tmp_path/'font'),
    ))
    atomic_write_json(destination/'delivery.json',{
        'status':'candidate','technical_passed':True,'content_passed':False,
        'final_video':str(movie),'export_context':delivery.export_context(runner,report),
        'duration':12,
    })
    receipt = delivery.finish_reviewed_delivery(runner,report,destination)
    assert receipt['status'] == 'accepted'
    assert receipt['run_dir'] == str(run)
    atomic_write_json(run/'interactive-delivery.json',{'destination':str(destination)})
    atomic_write_json(run/'final-candidate/manifest.json',{
        'passed':True,'technical_passed':True,'quality_review':report['quality_review'],
        'final_audio_review_required':True,'final_audio_review':audio,
    })
    atomic_write_json(run/'final-candidate/result.json',report)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *_:'done')
    return episode,run,destination,movie,cover,report,receipt


