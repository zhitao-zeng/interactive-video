"""Accepted provider failures retain their task identity even without an MP4."""
import json
from types import SimpleNamespace
import httpx
import pytest
from novel_manga.providers.phanrouter_video import generate_video


def test_failed_provider_task_is_archived_before_its_active_receipt_is_released(tmp_path):
    def handle(request):
        if request.method=='POST':return httpx.Response(200,json={'task_id':'failed-video-1'})
        return httpx.Response(200,json={'status':'failed','error':{'code':'OutputVideoSensitiveContentDetected'}})
    settings=SimpleNamespace(video_model='sd2.0-fast',phanrouter_base_url='https://provider.invalid',request_timeout=10,poll_timeout=10)
    payload={'model':'sd2.0-fast','content':[{'type':'text','text':'An ordinary dinner conversation.'}],
             'generate_audio':True,'output_format':'mp4'}
    with httpx.Client(transport=httpx.MockTransport(handle))as client:
        with pytest.raises(RuntimeError,match='video generation failed'):
            generate_video(settings,client,{},payload,tmp_path/'clip.mp4')
    archived=json.loads((tmp_path/'failed-provider-tasks/failed-video-1.json').read_text())
    assert archived['task_id']=='failed-video-1'and archived['model']=='sd2.0-fast'
    assert archived['error']['code']=='OutputVideoSensitiveContentDetected'
    assert not(tmp_path/'clip.mp4.task.json').exists()
