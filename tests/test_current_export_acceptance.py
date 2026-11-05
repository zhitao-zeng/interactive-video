from types import SimpleNamespace
import hashlib

from novel_manga.application.review import final_cut
from novel_manga.interactive import delivery
from novel_manga.review.acceptance import acceptance_status
from novel_manga.util import atomic_write_json


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


from support.accepted_export import accepted_case as _accepted_case


def test_versioned_export_acceptance_uses_real_run_and_movie_parent_cover(tmp_path,monkeypatch):
    episode,run,destination,movie,cover,_,receipt = _accepted_case(tmp_path,monkeypatch)
    assert movie.parent == destination/'versions/export-001'
    assert cover.parent == movie.parent
    assert receipt['run_dir'] == str(run)
    assert receipt['accepted_export']['video_sha256'] == _sha(movie)
    assert receipt['accepted_export']['cover_sha256'] == _sha(cover)
    assert (run/'final-candidate/manifest.json').is_file()
    current = acceptance_status(episode,receipt['run_dir'])
    assert current['status'] == 'accepted' and current['passed'] is True


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


def test_incomplete_final_audio_cannot_accept_current_export(tmp_path,monkeypatch):
    episode,run,_,movie,_,report,_ = _accepted_case(tmp_path,monkeypatch)
    report['final_audio_review'] = {'movie':str(movie),'complete':False,'passed':False,
                                    'issues':[],'errors':['one window missing']}
    atomic_write_json(episode/'thin_media_report.json',report)
    atomic_write_json(run/'final-candidate/result.json',report)
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_missing_run_dir_cannot_accept_current_export(tmp_path,monkeypatch):
    episode,_,destination,_,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    receipt.pop('run_dir')
    atomic_write_json(destination/'delivery.json',receipt)
    assert acceptance_status(episode,receipt.get('run_dir'))['status'] == 'candidate'


def test_accepted_export_hash_is_rechecked_by_status_projection(tmp_path,monkeypatch):
    episode,run,_,movie,_,_,_ = _accepted_case(tmp_path,monkeypatch)
    movie.write_bytes(b'tampered!!')
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_accepted_cover_hash_is_rechecked_by_status_projection(tmp_path,monkeypatch):
    episode,run,_,_,cover,_,_ = _accepted_case(tmp_path,monkeypatch)
    cover.write_bytes(b'tampered cover')
    assert acceptance_status(episode,run)['status'] == 'candidate'


def test_legacy_accepted_delivery_does_not_impersonate_current_protocol(tmp_path,monkeypatch):
    episode,run,destination,_,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    receipt.pop('acceptance_protocol')
    receipt.pop('accepted_export')
    atomic_write_json(destination/'delivery.json',receipt)
    assert acceptance_status(episode,run)['status'] == 'candidate'
