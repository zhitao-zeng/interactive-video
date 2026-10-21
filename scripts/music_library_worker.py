"""CPU audio evidence/index work in the existing SoulTuner environment."""
from pathlib import Path
import json
import os
import sys
import time


def main():
    job=json.loads(Path(sys.argv[1]).read_text());config=job['runtime']
    os.environ.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    import torch
    torch.set_num_threads(8)
    if job['action']=='audio':
        sys.path.insert(0,config['audio_source_root'])
        import torchaudio,soundfile
        def load(path,*args,**kwargs):
            data,sr=soundfile.read(path,dtype='float32',always_2d=True)
            return torch.from_numpy(data.T.copy()),sr
        torchaudio.load=load
        from eureka_infer.api import EurekaAudio
        model=EurekaAudio(config['audio_model_dir'],device='cpu')
        for row in job['jobs']:
            output=Path(row['output'])
            if output.exists():continue
            started=time.monotonic()
            response=model.generate([{'role':'user','content':[{'type':'audio_url','audio_url':{'url':row['audio']}},
                {'type':'text','text':row['question']}]}],max_new_tokens=row.get('max_new_tokens',240),do_sample=False)
            temporary=output.with_suffix('.partial.json')
            temporary.write_text(json.dumps({'model':'Eureka-Audio-Instruct','device':'cpu','actual_audio':row['audio'],
                'source_windows':row['source_windows'],'scope':row.get('scope','three6second samples; not full-song or film acceptance'),
                'inference_seconds':round(time.monotonic()-started,3),'response':response},ensure_ascii=False,indent=2)+'\n')
            temporary.replace(output)
            print(row['id'],flush=True)
        return
    sys.path.insert(0,config['source_root'])
    os.environ.update(GOOGLE_BERT_BERT_BASE_UNCASED_PATH=config['bert_path'],M2D_CLAP_CHECKPOINT=config['m2d_checkpoint'],
        M2D_CLAP_WEIGHT_DIR=str(Path(config['m2d_checkpoint']).parent),DENSE_TEXT_AUDIO_BACKEND='m2d',
        NEO4J_URI=config['neo4j_uri'],NEO4J_USER='neo4j',NEO4J_PASSWORD='unused-loopback')
    import numpy as np,soundfile
    from neo4j import GraphDatabase
    from retrieval.audio_embedder import encode_audio_to_embedding
    rows=job['tracks'];written=[];encoded=0
    with GraphDatabase.driver(config['neo4j_uri'],auth=None)as db:
        before=db.execute_query('MATCH(s:Song) RETURN s.music_id AS id').records
        for row in rows:
            stat=Path(row['local_audio']).stat();state=[stat.st_size,stat.st_mtime_ns]
            old=db.execute_query('MATCH(s:Song {music_id:$id}) RETURN s.m2d2_embedding AS vector,s.source_asset_state AS state,s.m2d_checkpoint AS checkpoint',id=row['song_id']).records
            if old and old[0]['state']==state and old[0]['checkpoint']==config['m2d_checkpoint']and old[0]['vector']:
                vector=old[0]['vector']
            else:
                data,sr=soundfile.read(row['audio_audit_sample'],dtype='float32')
                vector=encode_audio_to_embedding(data,sr);encoded+=1
            assert len(vector)==768 and np.isfinite(vector).all()
            audit=row.get('audio_audit')or{};vocal=audit.get('has_human_vocals');known=isinstance(vocal,bool)and audit.get('uncertain')is False
            props={'music_id':row['song_id'],'title':row['title'],'artist':row['artist'],'album':'User supplied BGM library',
                'audio_url':Path(row['local_audio']).as_uri(),'unplayable_stub':False,'source_url':row.get('source_url'),
                'license_url':row.get('license_url'),'attribution':row.get('attribution'),'catalog_source':'user-supplied-library',
                'audio_audit_scope':row.get('vocal_evidence_scope'),'m2d2_embedding':vector,
                'source_asset_state':state,'m2d_checkpoint':config['m2d_checkpoint'],
                'is_instrumental':known and not vocal,'has_vocal':vocal if known else None,
                'language':'Instrumental'if known and not vocal else'Unknown','audio_duration':row.get('duration'),
                'library_scene':row.get('scene'),'library_subscene':row.get('subscene'),
                'library_advice':row.get('advice'),'library_fit_status':row.get('fit_status'),
                'trial_history_json':json.dumps(row.get('trial_history',[]),ensure_ascii=False)}
            db.execute_query('MERGE(s:Song {music_id:$id}) SET s+=$props MERGE(a:Artist {name:$artist}) MERGE(s)-[:PERFORMED_BY]->(a)',
                id=row['song_id'],props=props,artist=row['artist'])
            db.execute_query('MATCH(s:Song {music_id:$id}) UNWIND $scenes AS name MERGE(t:Scenario {name:name}) MERGE(s)-[:FITS_SCENARIO]->(t)',
                id=row['song_id'],scenes=row.get('scenarios',[]))
            written.append(row['song_id']);print('indexed',row['song_id'],flush=True)
        after={r['id']for r in db.execute_query('MATCH(s:Song) RETURN s.music_id AS id').records}
        assert all(r['id']in after for r in before),'An existing music id was removed'
        assert set(written)<=after
        for sid in written:
            found=db.execute_query("MATCH(s:Song {music_id:$id}) CALL db.index.vector.queryNodes('song_m2d2_index',3,s.m2d2_embedding) YIELD node,score RETURN node.music_id AS id",id=sid).records
            assert sid in {r['id']for r in found},'Indexed source failed its audio self-recall'
    Path(job['output']).write_text(json.dumps({'indexed_ids':written,'dimensions':768,'existing_ids_preserved':True,
        'before_count':len(before),'after_count':len(after),'new_ids':len(after)-len(before),
        'encoded_count':encoded,'audio_self_recall_verified':len(written)},indent=2)+'\n')


if __name__=='__main__':main()
