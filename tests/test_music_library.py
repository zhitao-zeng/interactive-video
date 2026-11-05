import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from novel_manga.application.music_library import import_archive,merge_catalog
from novel_manga.util import atomic_write_json


def package(tmp_path):
    catalog={'version':'1.1','tracks':[{'id':'BGM-009','title':'Long Note Two','artist':'Artist',
        'scene':'悬疑后室','subscene':'安静观察','local_file':'audio/BGM-009.mp3','credit_file':'credits/BGM-009.txt',
        'attribution':'Original exact attribution\n','license':'source licence','fit_status':'已生成试听稿','feedback':'待评价'}]}
    trials={'trials':[{'trial_id':'TRIAL-001','track_id':'BGM-009','status':'已生成试听稿','feedback':'待用户'}]}
    archive=tmp_path/'library.zip'
    with ZipFile(archive,'w')as z:
        z.writestr('BGM曲库/catalog.json',json.dumps(catalog,ensure_ascii=False))
        z.writestr('BGM曲库/维护记录.json',json.dumps(trials,ensure_ascii=False))
        z.writestr('BGM曲库/BGM曲库.xlsx',b'user workbook')
        z.writestr('BGM曲库/audio/BGM-009.mp3',b'ID3original audio bytes')
        z.writestr('BGM曲库/credits/BGM-009.txt','Original exact attribution\n')
    return archive,catalog,trials


def test_import_keeps_original_ids_attribution_trial_and_workbook_bytes(tmp_path):
    archive,catalog,trials=package(tmp_path)
    result=import_archive(archive,tmp_path/'library')
    assert result['tracks'][0]['song_id']=='BGM-009'
    assert result['tracks'][0]['attribution']==catalog['tracks'][0]['attribution']
    assert result['tracks'][0]['trial_history']==trials['trials']
    assert result['tracks'][0]['source_record']==catalog['tracks'][0]
    assert Path(result['tracks'][0]['local_audio']).read_bytes()==b'ID3original audio bytes'
    before=Path(result['library_dir'])/'BGM曲库.xlsx'
    before.write_bytes(b'user edited workbook')
    assert import_archive(archive,tmp_path/'library')['tracks'][0]['song_id']=='BGM-009'
    assert before.read_bytes()==b'user edited workbook'


def test_merge_adds_library_without_replacing_other_library_or_feedback(tmp_path):
    archive,_,_=package(tmp_path);imported=import_archive(archive,tmp_path/'library')
    original=[{'song_id':'sdd-1','title':'Original','feedback':'retained'}]
    target=tmp_path/'combined.json';atomic_write_json(target,original)
    rows=merge_catalog(target,imported['tracks'])
    assert len(rows)==2 and rows[0]==original[0]
    assert len(merge_catalog(target,imported['tracks']))==2
    with pytest.raises(ValueError,match='different'):
        merge_catalog(target,[{**imported['tracks'][0],'local_audio':'different-file.mp3'}])
