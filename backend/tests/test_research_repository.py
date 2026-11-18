"""Real temporary SQLite tests for research durability and terminal races."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.research.errors import ResearchError
from great_agent.research.repository import ResearchRepository


def snapshot(label='old'):
    return {'id': label, 'manifest_sha256': label, 'selection': [], 'materials': [
        {'id': label, 'kind': 'code', 'title': label, 'locator': {'path': 'a.py'}, 'content': label}
    ], 'limitations': []}


@pytest.fixture
def repo(tmp_path):
    engine, sessions = create_engine_and_session_factory(tmp_path / 'research.db')
    Base.metadata.create_all(engine)
    yield ResearchRepository(sessions)
    engine.dispose()


def queued(repo, session=None, request_id='request', message='Why?', **kwargs):
    session = session or repo.create_session(goal='Explain convergence', snapshot=snapshot())
    return repo.create_run(session['id'], request_id=request_id, expected_version=session['version'],
                           message=message, **kwargs)


def claim(repo):
    run = repo.claim_next('worker')
    assert run is not None
    return run, (run['id'], run['owner_id'], run['fencing_token'])


def test_sessions_restore_revisions_and_do_not_expose_captured_code(repo):
    session = repo.create_session(title='TF study', goal='Why?', snapshot=snapshot())
    assert session['settings'] == {'model_profile_id': 'default', 'mode': 'deep'}
    assert session['materials'] == []
    assert 'content' not in str(session['material_summary'])
    restored = ResearchRepository(repo.sessions).get_session(session['id'])
    assert restored == session
    updated = repo.update_materials(session['id'], expected_version=session['version'],
                                    materials=[{'kind': 'code', 'source_id': 'project', 'paths': ['a.py']}],
                                    snapshot=snapshot('new'))
    assert updated['material_revision'] == 2
    with pytest.raises(ResearchError) as error:
        repo.update_settings(session['id'], expected_version=session['version'], model_profile_id='default', mode='quick')
    assert error.value.code == 'version_conflict'


def test_idempotency_returns_same_run_after_version_changes_and_rejects_changed_payload(repo):
    session = repo.create_session(snapshot=snapshot())
    run = queued(repo, session)
    replay = queued(repo, session)
    assert replay['id'] == run['id']
    with pytest.raises(ResearchError) as error:
        queued(repo, session, message='Different')
    assert error.value.code == 'request_conflict'
    with pytest.raises(ResearchError) as error:
        queued(repo, repo.get_session(session['id']), request_id='second')
    assert error.value.code == 'active_run'
    assert len(repo.get_session(session['id'])['messages']) == 1


def test_request_lookup_preserves_original_server_budget_after_settings_changed(repo):
    session = repo.create_session()
    budget = {'model_fingerprint': 'accepted-profile', 'max_requests': 4}
    run = queued(repo, session, budget=budget)
    current = repo.get_session(session['id'])
    repo.update_settings(session['id'], expected_version=current['version'], model_profile_id='new', mode='quick')
    original = repo.find_request(session['id'], 'request')
    assert original['run']['id'] == run['id']
    assert original['budget'] == budget
    assert original['request']['expected_version'] == session['version']
    assert repo.find_request(session['id'], 'never-accepted') is None


def test_settings_and_material_update_during_run_preserves_queued_snapshot(repo):
    session = repo.create_session(snapshot=snapshot())
    run = queued(repo, session)
    current = repo.get_session(session['id'])
    current = repo.update_settings(session['id'], expected_version=current['version'], model_profile_id='other', mode='quick')
    repo.update_materials(session['id'], expected_version=current['version'], materials=[], snapshot=snapshot('new'))
    assert repo.get_run_snapshot(run['id'])['materials'][0]['content'] == 'old'
    running, args = claim(repo)
    assert running['model_profile_id'] == 'default' and running['mode'] == 'deep'
    repo.finish(*args, result={'answer': 'Done', 'answer_blocks': []})
    restored = repo.get_session(session['id'])
    assert restored['settings']['model_profile_id'] == 'other'
    assert restored['material_revision'] == 2 and restored['state_revision'] == 1


def test_global_queue_concurrency_and_heartbeat_ownership(repo):
    queued(repo)
    queued(repo, request_id='other')
    run, args = claim(repo)
    assert repo.claim_next('second') is None
    assert repo.heartbeat(*args)
    assert not repo.heartbeat(run['id'], 'other', args[2])
    repo.fail(*args, error_code='test_failure', message='Failed')
    assert repo.claim_next('second') is not None


def test_cancellation_fences_late_completion_and_preserves_audit_only(repo):
    run = queued(repo)
    _, args = claim(repo)
    repo.add_evidence(*args, evidence=[{'id': 'code-proof', 'alias': 'C1', 'kind': 'code', 'title': 'code',
        'text': 'x = 1', 'sha256': 'abc', 'locator': {'path': 'a.py'}, 'quality_flags': [], 'available': True}])
    cancelled = repo.cancel(run['id'])
    assert cancelled['status'] == 'cancelled'
    with pytest.raises(ResearchError) as error:
        repo.finish(*args, result={'answer': 'late'}, items=[{'kind': 'hypothesis', 'text': 'late'}])
    assert error.value.code == 'ownership_lost'
    session = repo.get_session(run['session_id'])
    assert session['state_revision'] == 0 and session['items'] == []
    assert session['active_run_id'] is None
    assert repo.get_evidence(run['id'], 'C1')['text'] == 'x = 1'


def test_completed_run_wins_later_cancel_and_state_commits_exactly_once(repo):
    run = queued(repo)
    _, args = claim(repo)
    result = {'answer': 'Observed', 'answer_blocks': [{'id': 'b1', 'type': 'observation', 'text': 'Observed'}]}
    items = [{'id': 'h1', 'kind': 'hypothesis', 'text': 'Potential reset', 'status': 'proposed', 'support': [], 'opposition': []}]
    repo.finish(*args, result=result, items=items)
    repo.finish(*args, result=result, items=items)
    assert repo.cancel(run['id'])['status'] == 'completed'
    session = repo.get_session(run['session_id'])
    assert session['state_revision'] == 1
    assert session['items'][0]['origin_run_id'] == run['id']
    assert len(session['messages']) == 2


def test_evidence_alias_is_immutable_and_response_checkpoint_survives_restore(repo):
    run = queued(repo)
    _, args = claim(repo)
    evidence = {'id': 'proof', 'alias': 'E1', 'kind': 'experiment', 'title': 'metric', 'text': '1 m',
                'locator': {'attempt_id': 'attempt'}, 'sha256': 'hash', 'quality_flags': [], 'available': True}
    repo.add_evidence(*args, evidence=[evidence])
    call = repo.start_model_call(*args, request={'messages': []}, evidence_ids=['proof'], model_fingerprint='profile-hash')
    repo.record_model_response(*args, call_id=call['id'], response={'answer': '1'}, usage={'input_tokens': 4})
    repo.save_checkpoint(*args, key='validation', payload={'result': {'answer': '1'}})
    restored = ResearchRepository(repo.sessions)
    assert restored.get_evidence_by_id('proof')['locator']['attempt_id'] == 'attempt'
    assert restored.list_model_calls(run['id'])[0]['response'] == {'answer': '1'}
    assert restored.get_checkpoint(run['id'], 'validation')['payload']['result']['answer'] == '1'
    with pytest.raises(ResearchError):
        repo.add_evidence(*args, evidence=[{**evidence, 'text': 'changed'}])


def test_expired_uncertain_call_interrupts_instead_of_repeating_billable_request(repo):
    run = queued(repo)
    _, args = claim(repo)
    repo.start_model_call(*args, request={'messages': []})
    assert repo.recover_expired(now=datetime.now(timezone.utc) + timedelta(minutes=10)) == 1
    assert repo.get_run(run['id'])['status'] == 'interrupted'
    assert repo.list_model_calls(run['id'])[0]['status'] == 'uncertain'
    assert repo.claim_next('replacement') is None
    assert repo.get_session(run['session_id'])['state_revision'] == 0


def test_expired_worker_with_saved_response_can_resume_and_old_owner_is_fenced(repo):
    run = queued(repo)
    _, old_args = claim(repo)
    call = repo.start_model_call(*old_args, request={'messages': []})
    repo.record_model_response(*old_args, call_id=call['id'], response={'answer': 'saved'})
    repo.recover_expired(now=datetime.now(timezone.utc) + timedelta(minutes=10))
    resumed = repo.claim_next('replacement')
    assert resumed['id'] == run['id']
    assert resumed['fencing_token'] > old_args[2]
    with pytest.raises(ResearchError):
        repo.finish(*old_args, result={'answer': 'stale'})
    assert repo.list_model_calls(run['id'])[0]['response']['answer'] == 'saved'


def test_retry_uses_original_snapshot_and_refuses_stale_state(repo):
    run = queued(repo)
    _, args = claim(repo)
    repo.fail(*args, error_code='network', message='Interrupted')
    current = repo.get_session(run['session_id'])
    repo.update_materials(run['session_id'], expected_version=current['version'], materials=[], snapshot=snapshot('new'))
    retry = repo.retry_run(run['id'], request_id='retry-one')
    assert retry['retry_of'] == run['id']
    assert repo.get_run_snapshot(retry['id'])['id'] == 'old'
    assert repo.retry_run(run['id'], request_id='retry-one')['id'] == retry['id']
    _, retry_args = claim(repo)
    repo.finish(*retry_args, result={'answer': 'done'})
    with pytest.raises(ResearchError) as error:
        repo.retry_run(run['id'], request_id='retry-two')
    assert error.value.code == 'stale_retry'


def test_followup_target_must_belong_to_session_and_exist(repo):
    first = queued(repo)
    _, args = claim(repo)
    repo.finish(*args, result={'answer': 'a', 'answer_blocks': [{'id': 'block', 'type': 'answer', 'text': 'a'}]})
    other = repo.create_session()
    with pytest.raises(ResearchError) as error:
        queued(repo, other, followup_target={'run_id': first['id'], 'block_id': 'block'})
    assert error.value.code == 'invalid_followup'
    current = repo.get_session(first['session_id'])
    followup = queued(repo, current, request_id='followup', followup_target={'run_id': first['id'], 'block_id': 'block'})
    assert followup['followup_target']['block_id'] == 'block'
    assert repo.get_run_context(followup['id'])['followup']['messages'][0]['blocks'][0]['text'] == 'a'


def test_reports_freeze_message_and_evidence_content_and_events_resume(repo):
    run = queued(repo)
    _, args = claim(repo)
    repo.finish(*args, result={'answer': 'a', 'answer_blocks': []})
    report = repo.create_report(run['session_id'], title='Saved', content='a', run_ids=[run['id']])
    current = repo.get_session(run['session_id'])
    repo.update_materials(run['session_id'], expected_version=current['version'], materials=[], snapshot=snapshot('new'))
    assert repo.list_reports(run['session_id'])[0] == report
    assert report['version'] == 1
    assert repo.create_report(run['session_id'], title='Second', content='a')['version'] == 2
    assert report['snapshot']['runs'][0]['result']['answer'] == 'a'
    events = repo.list_events(run['session_id'])
    assert len(events) >= 4
    assert repo.list_events(run['session_id'], after=events[-2]['id']) == events[-1:]


def test_two_workers_racing_claim_cannot_exceed_global_concurrency(repo):
    queued(repo)
    queued(repo, request_id='second')
    barrier = Barrier(2)
    def attempt(owner):
        barrier.wait(timeout=5)
        return ResearchRepository(repo.sessions).claim_next(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ('one', 'two')))
    assert len([result for result in results if result is not None]) == 1


def test_changed_budget_conflicts_even_when_question_and_request_id_match(repo):
    session = repo.create_session()
    queued(repo, session, budget={'model_fingerprint': 'original', 'model_requests': 4})
    with pytest.raises(ResearchError) as error:
        queued(repo, session, budget={'model_fingerprint': 'other', 'model_requests': 4})
    assert error.value.code == 'request_conflict'


def test_expired_owner_cannot_finish_before_recovery_runs(repo):
    from great_agent.research.models import ResearchRunModel
    run = queued(repo)
    _, args = claim(repo)
    with repo.sessions.begin() as database:
        database.get(ResearchRunModel, run['id']).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ResearchError) as error:
        repo.finish(*args, result={'answer': 'late'})
    assert error.value.code == 'ownership_lost'
    assert repo.get_session(run['session_id'])['state_revision'] == 0


def test_new_state_head_keeps_old_result_historical_and_commit_replay_is_safe(repo):
    from great_agent.research.models import ResearchSessionModel, ResearchStateRevisionModel
    run = queued(repo)
    _, args = claim(repo)
    # Simulate an explicit future research-state correction while this run is in flight.
    with repo.sessions.begin() as database:
        database.get(ResearchSessionModel, run['session_id']).state_revision = 1
        database.add(ResearchStateRevisionModel(session_id=run['session_id'], revision=1, run_id=None,
            items_json='[{"id":"correction","kind":"constraint","text":"Keep this correction"}]'))
    repo.finish(*args, result={'answer': 'Historical'}, items=[{'id': 'stale', 'kind': 'hypothesis', 'text': 'Stale'}])
    assert repo.get_run(run['id'])['result']['state_conflict'] is True
    assert repo.get_session(run['session_id'])['items'][0]['id'] == 'correction'
    repo.finish(*args, result={'answer': 'Historical'}, items=[{'id': 'stale', 'kind': 'hypothesis', 'text': 'Stale'}])
    assert len(repo.get_session(run['session_id'])['messages']) == 2
