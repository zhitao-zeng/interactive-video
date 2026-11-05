"""Resize only an in-memory model-request image; original files/history are untouched."""
import base64
import io
import json
import sys
from PIL import Image, ImageOps

block=json.load(sys.stdin)
value=block.get('image_url',{}).get('url') if block['type']=='image_url' else block.get('data','')
if block['type']=='image_url' and not value.startswith('data:'):
    json.dump(block,sys.stdout)
else:
    raw=base64.b64decode(value.split(',',1)[-1])
    with Image.open(io.BytesIO(raw))as source:
        image=ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1280,1280))
        buf=io.BytesIO();image.save(buf,'JPEG',quality=85)
    encoded=base64.b64encode(buf.getvalue()).decode()
    if block['type']=='image_url':block={**block,'image_url':{**block['image_url'],'url':'data:image/jpeg;base64,'+encoded}}
    else:block={**block,'data':encoded,'mimeType':'image/jpeg'}
    json.dump(block,sys.stdout)
