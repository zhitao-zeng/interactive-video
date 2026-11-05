"""The explicit Codex endpoint uses the existing logged-in sandbox executor."""
from __future__ import annotations
import base64
import copy
import json
import uuid
from pathlib import Path


def completion(request,*,timeout):
    from novel_manga.application.configuration import project_root
    from novel_manga.application.agents.sandbox import load_config
    from novel_manga.application.agents.episode.codex_session import run_session
    from novel_manga.util import atomic_write_json,read_json
    if timeout<=0:raise TimeoutError('Codex model request budget exhausted')
    root=project_root();work=root/'.runtime/codex-json'/str(uuid.uuid4());(work/'input').mkdir(parents=True)
    (work/'owner').write_text('One explicit Codex JSON/vision request; saved login; no media generation\n')
    (work/'output').mkdir();payload=copy.deepcopy(request);images=[]
    for message in payload.get('messages',[]):
        content=message.get('content',[])
        if not isinstance(content,list):continue
        for part in content:
            if part.get('type')!='image_url':continue
            url=part.get('image_url',{}).get('url','')
            if not url.startswith('data:image/')or';base64,'not in url:
                raise ValueError('Codex vision requires the preserved inline image bytes')
            relative=f'input/image-{len(images)+1:03d}.png';path=work/relative
            path.write_bytes(base64.b64decode(url.split(';base64,',1)[1]));images.append(relative)
            part.clear();part.update(type='text',text=f'Actual attached image {len(images)}: {relative}')
    atomic_write_json(work/'input/manifest.json',{'phase':'model_json'})
    atomic_write_json(work/'input/request.json',payload)
    config={**load_config(root/'configs/agent_sandbox.json'),'backend':'codex','model':request['model'],
            'effort':'low','codex_attached_images':images}
    schema=(request.get('response_format',{}).get('json_schema')or{}).get('schema')
    if schema:
        atomic_write_json(work/'input/schema.json',schema);config['codex_output_schema']='input/schema.json'
    prompt='Return the final JSON response for this structured request. Preserve all role/source constraints and use the actual attached images in their listed order.\n'+json.dumps(payload,ensure_ascii=False)
    attempt=run_session(work,prompt,timeout=max(1,int(timeout)),config=config,expected_output='response.json')
    if not attempt.ok or'response.json'not in attempt.produced:
        raise RuntimeError('Codex request did not complete; preserved evidence: '+str(attempt.directory))
    answer=read_json(work/'output/response.json');assert isinstance(answer,dict)
    return {'choices':[{'message':{'content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],
            'usage':attempt.record.get('usage',{}),
            'codex_execution':{'seconds':attempt.record.get('seconds',0),'attempt':str(getattr(attempt,'directory',work))}}
