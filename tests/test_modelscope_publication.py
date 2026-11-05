from pathlib import Path
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.interactive import publication


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
