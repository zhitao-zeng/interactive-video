from pathlib import Path
import json
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.interactive import publication
from support.accepted_export import accepted_case as _accepted_case


class API:
    def __init__(self): self.files={}; self.uploads=[]; self.stale=True
    def repo_exists(self,*a): return True
    def list_repo_files(self,*a):
        from types import SimpleNamespace
        return [SimpleNamespace(path=p,size=len(v)) for p,v in self.files.items()]
    def upload_folder(self,repo,kind,stage,**kw):
        self.uploads.append(kw['allow_patterns'])
        for p in kw['allow_patterns']: self.files[p]=(stage/p).read_bytes()
    def download_file(self,repo,kind,path,**kw):
        target=kw['local_dir']/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'{}' if self.stale else self.files[path]);self.stale=False
        return target


def freeze_accepted(receipt,movie,root,monkeypatch):
    import hashlib
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    cover=movie.parent/'cover.jpeg'
    atomic_write_json(root/'run/interactive-delivery.json',{'destination':str(movie.parent)})
    atomic_write_json(receipt,{'status':'accepted','technical_passed':True,'content_passed':True,
        'final_video':str(movie),'duration':12,'episode_dir':str(root/'episode'),
        'run_dir':str(root/'run'),'acceptance_protocol':'current-export-v1',
        'accepted_export':{'movie':str(movie),'video_sha256':digest(movie),'cover_sha256':digest(cover)}})
    monkeypatch.setattr('novel_manga.review.acceptance.acceptance_status',lambda *_:{
        'status':'accepted','passed':True,'final_visual':{'movie':str(movie)}})


def test_stale_remote_index_retry_does_not_upload_media_again(tmp_path,monkeypatch):
    movie=tmp_path/'delivery/episode.mp4';movie.parent.mkdir();movie.write_bytes(b'video')
    (movie.parent/'cover.jpeg').write_bytes(b'cover')
    receipt=tmp_path/'delivery/delivery.json'
    freeze_accepted(receipt,movie,tmp_path,monkeypatch)
    spec={'repository':'owner/validation','visibility':'private','title':'Films',
          'episodes':[{'edition':'one','title':'One','receipt':str(receipt)}]}
    monkeypatch.setattr(publication,'probe',lambda p:12)
    api=API();state=tmp_path/'publication/state.json'
    with pytest.raises(ValueError,match='index differs'): publication.publish_deliveries(tmp_path,spec,state,api)
    result=publication.publish_deliveries(tmp_path,spec,state,api)
    assert result['status']=='complete'
    assert sum('episodes/one.mp4' in paths for paths in api.uploads)==1
    assert read_json(state)['verified_episodes']==1
    movie.write_bytes(b'new version different size')
    with pytest.raises(ValueError,match='SHA256'):
        publication.publish_deliveries(tmp_path,spec,state,api)
    assert sum('episodes/one.mp4' in paths for paths in api.uploads)==1


def test_candidate_is_not_published(tmp_path):
    receipt=tmp_path/'receipt.json';atomic_write_json(receipt,{'status':'candidate','final_video':'somewhere'})
    api=API()
    result=publication.publish_deliveries(tmp_path,{'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(receipt)}]},tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance' and api.uploads==[]


def test_upload_committed_then_connection_lost_resumes_from_inventory(tmp_path,monkeypatch):
    movie=tmp_path/'delivery/episode.mp4';movie.parent.mkdir();movie.write_bytes(b'video')
    (movie.parent/'cover.jpeg').write_bytes(b'cover')
    receipt=movie.parent/'delivery.json'
    freeze_accepted(receipt,movie,tmp_path,monkeypatch)
    spec={'repository':'owner/validation','episodes':[{'edition':'one','receipt':str(receipt)}]}
    monkeypatch.setattr(publication,'probe',lambda p:12)
    class Interrupted(API):
        hidden=False
        def list_repo_files(self,*args):
            return [] if self.hidden else super().list_repo_files(*args)
        def upload_folder(self,*args,**kw):
            super().upload_folder(*args,**kw)
            if len(self.uploads)==1:raise ConnectionError('response lost after server committed')
    api=Interrupted();api.stale=False;state=tmp_path/'publication/state.json'
    with pytest.raises(ConnectionError):publication.publish_deliveries(tmp_path,spec,state,api)
    api.hidden=True
    with pytest.raises(ValueError,match='outcome pending'):publication.publish_deliveries(tmp_path,spec,state,api)
    assert len(api.uploads)==1
    api.hidden=False
    assert publication.publish_deliveries(tmp_path,spec,state,api)['status']=='complete'
    assert sum('episodes/one.mp4' in paths for paths in api.uploads)==1
    uploads_before = len(api.uploads)
    movie.write_bytes(b'replacement bytes')
    with pytest.raises(ValueError,match='SHA256'):
        publication.publish_deliveries(tmp_path,spec,state,api)
    assert len(api.uploads)==uploads_before
    assert read_json(state)['status']!='complete'


def test_same_size_same_mtime_replacement_is_not_trusted_for_resume(tmp_path,monkeypatch):
    import hashlib
    import os
    movie=tmp_path/'delivery/episode.mp4';movie.parent.mkdir();movie.write_bytes(b'video')
    cover=movie.parent/'cover.jpeg';cover.write_bytes(b'cover')
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    receipt=movie.parent/'delivery.json'
    atomic_write_json(tmp_path/'run/interactive-delivery.json',{'destination':str(movie.parent)})
    atomic_write_json(receipt,{'status':'accepted','technical_passed':True,'content_passed':True,
        'final_video':str(movie),'duration':12,'episode_dir':str(tmp_path/'episode'),
        'run_dir':str(tmp_path/'run'),'acceptance_protocol':'current-export-v1',
        'accepted_export':{'movie':str(movie),'video_sha256':digest(movie),'cover_sha256':digest(cover)}})
    spec={'repository':'owner/validation','episodes':[{'edition':'one','receipt':str(receipt)}]}
    monkeypatch.setattr(publication,'probe',lambda p:12)
    monkeypatch.setattr('novel_manga.review.acceptance.acceptance_status',lambda *_:{
        'status':'accepted','passed':True,'final_visual':{'movie':str(movie)}})
    api=API();api.stale=False;state=tmp_path/'publication/state.json'
    publication.publish_deliveries(tmp_path,spec,state,api)
    uploads=len(api.uploads)
    stat=movie.stat();movie.write_bytes(b'other');os.utime(movie,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    with pytest.raises(ValueError,match='SHA256'):
        publication.publish_deliveries(tmp_path,spec,state,api)
    assert len(api.uploads)==uploads


def test_publication_rechecks_current_acceptance_before_upload(tmp_path,monkeypatch):
    import hashlib
    movie=tmp_path/'delivery/episode.mp4';movie.parent.mkdir();movie.write_bytes(b'video')
    cover=movie.parent/'cover.jpeg';cover.write_bytes(b'cover')
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    receipt=movie.parent/'delivery.json'
    atomic_write_json(tmp_path/'run/interactive-delivery.json',{'destination':str(movie.parent)})
    atomic_write_json(receipt,{'status':'accepted','technical_passed':True,'content_passed':True,
        'final_video':str(movie),'duration':12,'episode_dir':str(tmp_path/'episode'),
        'run_dir':str(tmp_path/'run'),'acceptance_protocol':'current-export-v1',
        'accepted_export':{'movie':str(movie),'video_sha256':digest(movie),'cover_sha256':digest(cover)}})
    monkeypatch.setattr(publication,'probe',lambda p:12)
    monkeypatch.setattr('novel_manga.review.acceptance.acceptance_status',lambda *_:{
        'status':'candidate','passed':False,'final_visual':{'movie':str(movie)}})
    api=API()
    result=publication.publish_deliveries(tmp_path,{'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(receipt)}]},tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance' and api.uploads==[]


def test_versioned_accepted_export_publishes_with_real_acceptance(tmp_path,monkeypatch):
    _,run,destination,movie,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    spec={'repository':'owner/validation','visibility':'private','title':'Films',
          'episodes':[{'edition':'one','title':'One','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API();api.stale=False
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='complete' and result['verified_episodes']==1
    assert sum('episodes/one.mp4' in paths for paths in api.uploads)==1
    assert api.files['episodes/one.mp4']==movie.read_bytes()
    assert json.loads(api.files['episodes.json'])==result['verified_index']
    assert result['verified_index']['episodes'][0]['file']=='episodes/one.mp4'
    assert Path(receipt['run_dir'])==run
    assert (run/'final-candidate/manifest.json').is_file()


@pytest.mark.parametrize('mutation',['run_dir','video_sha256','cover_sha256'])
def test_unbound_or_changed_current_export_is_not_published(tmp_path,monkeypatch,mutation):
    _,_,destination,movie,cover,_,receipt = _accepted_case(tmp_path,monkeypatch)
    if mutation=='run_dir':
        receipt.pop('run_dir')
        atomic_write_json(destination/'delivery.json',receipt)
    elif mutation=='video_sha256':
        movie.write_bytes(b'changed current export')
    else:
        cover.write_bytes(b'changed current cover')
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API()
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]


def test_old_layout_current_export_publishes_with_real_acceptance(tmp_path,monkeypatch):
    _,run,destination,movie,_,_,receipt = _accepted_case(
        tmp_path,monkeypatch,versioned=False,assembly_matches_export=True)
    assert movie.parent == destination
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API();api.stale=False
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='complete' and result['verified_episodes']==1
    assert Path(receipt['run_dir']) == run
    assert sum('episodes/one.mp4' in paths for paths in api.uploads)==1


def test_missing_run_dir_is_blocked_even_when_legacy_projection_accepts(tmp_path,monkeypatch):
    from novel_manga.review.acceptance import acceptance_status

    episode,_,destination,movie,_,_,receipt = _accepted_case(
        tmp_path,monkeypatch,versioned=False,assembly_matches_export=True)
    assert movie.parent == destination
    assert acceptance_status(episode)['status'] == 'accepted'
    receipt.pop('run_dir')
    atomic_write_json(destination/'delivery.json',receipt)
    assert acceptance_status(episode)['status'] == 'accepted'
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API();api.stale=False
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]


def test_missing_episode_dir_is_blocked_before_acceptance(tmp_path,monkeypatch):
    _,_,destination,_,_,_,receipt = _accepted_case(
        tmp_path,monkeypatch,versioned=False,assembly_matches_export=True)
    receipt.pop('episode_dir')
    atomic_write_json(destination/'delivery.json',receipt)
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API()
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]


@pytest.mark.parametrize('mutation',[
    'source','model','report_identity','policy',
    'missing_policy','missing_identity','missing_source_identity',
])
def test_stale_or_missing_audio_freshness_blocks_acceptance_and_upload(tmp_path,monkeypatch,mutation):
    from novel_manga.review.acceptance import acceptance_status

    episode,run,destination,_,_,report,receipt = _accepted_case(tmp_path,monkeypatch)
    audio=report['final_audio_review']
    if mutation=='source':
        atomic_write_json(episode/'music_policy.json',{'intent':'changed'})
    elif mutation=='model':
        model_dir=Path(audio['identity']['model']['path'])
        (model_dir/'weights.bin').write_bytes(b'weights-v2-longer')
    elif mutation=='report_identity':
        audio['identity']['model']['files']=[]
        atomic_write_json(episode/'thin_media_report.json',report)
    elif mutation=='policy':
        audio['policy']='stale-audio-policy'
        atomic_write_json(episode/'thin_media_report.json',report)
    else:
        audio.pop(mutation.removeprefix('missing_'))
        atomic_write_json(episode/'thin_media_report.json',report)
    assert acceptance_status(episode,run)['status'] == 'candidate'
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API()
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]


@pytest.mark.parametrize('binding',['missing_file','missing_destination'])
@pytest.mark.parametrize('audio_state',['fresh','stale'])
def test_missing_delivery_binding_blocks_fresh_and_stale_audio_upload(
        tmp_path,monkeypatch,binding,audio_state):
    from novel_manga.review.acceptance import acceptance_status

    episode,run,destination,_,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    if audio_state=='stale':
        atomic_write_json(episode/'music_policy.json',{'intent':'changed'})
    if binding=='missing_file':
        (run/'interactive-delivery.json').unlink()
    else:
        atomic_write_json(run/'interactive-delivery.json',{})
    assert acceptance_status(episode,run)['status'] == 'accepted'
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API();api.stale=False
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]


def test_delivery_binding_must_resolve_to_the_current_receipt(tmp_path,monkeypatch):
    from novel_manga.review.acceptance import acceptance_status

    episode,run,destination,_,_,_,receipt = _accepted_case(tmp_path,monkeypatch)
    other=tmp_path/'delivery/other'
    atomic_write_json(other/'delivery.json',receipt)
    atomic_write_json(run/'interactive-delivery.json',{'destination':str(other)})
    assert acceptance_status(episode,run)['status'] == 'accepted'
    spec={'repository':'owner/validation','episodes':[
        {'edition':'one','receipt':str(destination/'delivery.json')}]}
    monkeypatch.setattr(publication,'probe',lambda path:12)
    api=API();api.stale=False
    result=publication.publish_deliveries(tmp_path,spec,tmp_path/'publication/state.json',api)
    assert result['status']=='waiting_for_acceptance'
    assert result['verified_episodes']==0 and api.uploads==[]
