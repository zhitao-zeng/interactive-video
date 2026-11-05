from types import SimpleNamespace
from novel_manga.application.agents.episode import progress


def state(job,targets,validated):
    return {'job':{'id':job,'action':'first_frames','status':'completed','result':{
        'ok':False,'argument_errors':{cid:['invalid request'] for cid in targets if cid not in validated},
        'validation_targets':targets,'validated_clips':validated}}}


def test_host_confirmed_more_valid_rows_allows_another_correction_turn(tmp_path):
    ctx=SimpleNamespace(work_dir=tmp_path/'work',run_dir=tmp_path/'run')
    before=state('one',['a','b','c'],[]);after=state('two',['a','b','c'],['a','b'])
    assert progress.made_progress(ctx,progress.progress(ctx,before),before,after)


def test_rewording_same_error_or_dropping_targets_is_not_progress(tmp_path):
    ctx=SimpleNamespace(work_dir=tmp_path/'work',run_dir=tmp_path/'run')
    before=state('one',['a','b','c'],['a'])
    for after in [state('two',['a','b','c'],['a']),state('two',['a','b'],['a','b'])]:
        assert not progress.made_progress(ctx,progress.progress(ctx,before),before,after)
