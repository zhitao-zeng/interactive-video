from types import SimpleNamespace
import hashlib

from novel_manga.application.review import final_cut
from novel_manga.interactive import delivery
from novel_manga.review.acceptance import acceptance_status
from novel_manga.util import atomic_write_json


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _accepted_case(tmp_path, monkeypatch):
    episode = tmp_path/'book_1'
    run = tmp_path/'run'
    destination = tmp_path/'delivery/one'
    for path in (episode, run, destination):
        path.mkdir(parents=True)
    movie = destination/'new.mp4'
    movie.write_bytes(b'new export')
    cover = destination/'cover.jpeg'
    cover.write_bytes(b'cover')
    atomic_write_json(episode/'clip_plan.json', {'clips':[]})
    report = {
        'assembly':{'final_video':str(tmp_path/'native.mp4'),'thin_passed':True},
        'quality_review':{'passed':True,'content_passed':True},
    }
    final = {
        'policy':final_cut.POLICY,
        'movie':str(movie),
        'context':final_cut.context(episode,report,movie),
        'complete':True,'passed':True,'issues':[],'errors':[],
    }
    audio = {'movie':str(movie),'complete':True,'passed':True,'issues':[],'errors':[]}
    report.update(final_cut_review=final,final_audio_review=audio,
                  final_audio_review_required=True)
    atomic_write_json(episode/'thin_media_report.json',report)
    runner = SimpleNamespace(context=SimpleNamespace(
        episode_dir=episode,
        settings=SimpleNamespace(width=1080,height=1920,font_path=tmp_path/'font'),
    ))
    export_context = delivery.export_context(runner,report)
    receipt = {
        'status':'accepted','technical_passed':True,'content_passed':True,
        'final_video':str(movie),'export_context':export_context,
        'episode_dir':str(episode),'run_dir':str(run),
        'acceptance_protocol':'current-export-v1',
        'accepted_export':{
            'movie':str(movie),'video_sha256':_sha(movie),'cover_sha256':_sha(cover),
            'visual_context':final['context'],'audio_movie':str(movie),
        },
    }
    atomic_write_json(destination/'delivery.json',receipt)
    atomic_write_json(run/'interactive-delivery.json',{'destination':str(destination)})
    atomic_write_json(run/'final-candidate/manifest.json',{
        'passed':True,'technical_passed':True,'quality_review':report['quality_review'],
        'final_audio_review_required':True,'final_audio_review':audio,
    })
    atomic_write_json(run/'final-candidate/result.json',report)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *_:'done')
    return episode,run,destination,movie,cover,report,receipt


def test_interactive_acceptance_requires_formal_bundle_for_current_export(tmp_path,monkeypatch):
    episode,run,_,_,_,_,_ = _accepted_case(tmp_path,monkeypatch)
    (run/'final-candidate/manifest.json').unlink()
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_old_visual_audio_and_formal_receipts_cannot_accept_new_export(tmp_path,monkeypatch):
    episode,run,destination,movie,_,report,receipt = _accepted_case(tmp_path,monkeypatch)
    old = destination/'old.mp4'
    old.write_bytes(b'old export')
    old_final = {
        'policy':final_cut.POLICY,'movie':str(old),
        'context':final_cut.context(episode,report,old),
        'complete':True,'passed':True,'issues':[],'errors':[],
    }
    old_audio = {'movie':str(old),'complete':True,'passed':True,'issues':[],'errors':[]}
    report.update(final_cut_review=old_final,final_audio_review=old_audio)
    atomic_write_json(episode/'thin_media_report.json',report)
    atomic_write_json(run/'final-candidate/result.json',report)
    atomic_write_json(run/'final-candidate/manifest.json',{
        'passed':True,'technical_passed':True,'quality_review':report['quality_review'],
        'final_audio_review_required':True,'final_audio_review':old_audio,
    })
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_accepted_export_hash_is_rechecked_by_status_projection(tmp_path,monkeypatch):
    episode,run,_,movie,_,_,_ = _accepted_case(tmp_path,monkeypatch)
    movie.write_bytes(b'tampered!!')
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_legacy_accepted_delivery_does_not_impersonate_current_protocol(tmp_path,monkeypatch):
    episode,run,destination,_,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    receipt.pop('acceptance_protocol')
    receipt.pop('accepted_export')
    atomic_write_json(destination/'delivery.json',receipt)
    assert acceptance_status(episode,run)['status'] == 'candidate'
