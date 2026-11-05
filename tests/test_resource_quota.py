import pytest
from novel_manga.llm import quota


def test_confirmed_image_group_stops_official_and_h3cloud_but_not_sd25(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    assert quota.remember('router-image',403,{'error':{'code':'quota_not_enough'}})
    with pytest.raises(RuntimeError,match='router-image'):quota.check('router-image')
    quota.check('router-video')


def test_auth_and_generic_forbidden_do_not_exhaust_quota(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    for status,error in [(403,{'error':{'message':'forbidden'}}),(401,{'error':{'code':'invalid_api_key'}}),(500,{'error':{'message':'quota_not_enough'}})]:
        assert not quota.remember('router-image',status,error)
    assert not list(tmp_path.iterdir())


def test_group_snapshot_retains_history_when_refreshed(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    quota.remember('router-image',403,{'error':{'code':'quota_not_enough'}})
    quota.remember('router-image',403,{'error':{'code':'quota_not_enough'}})
    import json
    data=json.loads((tmp_path/'router-image.json').read_text())
    assert len(data['history'])==1


def test_daily_gateway_reset_is_not_replaced_by_permanent_stop(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    assert not quota.remember('phanthy-account',402,{'error':{'code':'insufficient_credits','message':'Both daily quota pools are exhausted.'}})
    quota.check('phanthy-account')


def test_unknown_explicit_key_does_not_share_registered_token_stop(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY','registered-key')
    group=quota.for_model('deepseek-v4.1-flash-1017','https://cloud.phanthy.com/phanrouter/v1','registered-key')
    assert group=='router-image'
    quota.remember(group,403,{'error':{'code':'quota_not_enough'}})
    unknown=quota.for_model('deepseek-v4.1-flash-1017','https://cloud.phanthy.com/phanrouter/v1','other-key')
    assert unknown == 'router-image'
    quota.check(unknown,key='other-key')


def test_concurrent_provider_failures_keep_all_history(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    from concurrent.futures import ThreadPoolExecutor
    import json,threading
    start=threading.Barrier(8)
    def fail(_):
        start.wait()
        return quota.remember('router-image',403,{'error':{'code':'quota_not_enough'}})
    with ThreadPoolExecutor(max_workers=8) as pool:assert all(pool.map(fail,range(8)))
    data=json.loads((tmp_path/'router-image.json').read_text())
    assert len(data['history'])==7


def test_image_quota_failure_during_sd25_does_not_write_sd_stop(tmp_path,monkeypatch):
    import httpx
    from novel_manga.providers.phanrouter_tasks import submit_once,check_generation_quota
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path/'groups'))
    marker=tmp_path/'sd25.stop'
    monkeypatch.setenv('NOVEL_QUOTA_STOP_FILE',str(marker))
    monkeypatch.setenv('NOVEL_RESOURCE_QUOTA_GROUP','router-video')
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY','image-key')
    monkeypatch.setenv('PHANROUTER_SD25_KEY','video-key')
    def reject():
        request=httpx.Request('POST','https://offline.invalid/image')
        response=httpx.Response(403,request=request,json={'error':{'code':'quota_not_enough'}})
        response.raise_for_status()
    with pytest.raises(RuntimeError,match='403'):
        submit_once(reject,resource_id='image',resource_key='image-key')
    assert not marker.exists()
    check_generation_quota('sd25','video-key')
    with pytest.raises(RuntimeError,match='router-image'):
        check_generation_quota('image','image-key')


def test_dedicated_h3_key_divergence_is_not_stopped_by_image_key(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY','image-key')
    monkeypatch.setenv('PHANROUTER_H3CLOUD_API_KEY','separate-h3-key')
    image=quota.for_resource('image','image-key')
    h3=quota.for_resource('h3cloud','separate-h3-key')
    assert image != h3
    quota.remember(image,403,{'error':{'code':'quota_not_enough'}})
    quota.check(h3)


def test_two_project_credentials_and_key_rotation_do_not_share_stops(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path))
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY','project-A')
    a=quota.for_resource('image','project-A')
    quota.remember(a,403,{'error':{'code':'quota_not_enough'}},key='project-A')
    monkeypatch.setenv('PHANROUTER_IMAGE_API_KEY','project-B')
    b=quota.for_resource('image','project-B')
    quota.check(b,key='project-B')
    with pytest.raises(RuntimeError):quota.check(a,key='project-A')
