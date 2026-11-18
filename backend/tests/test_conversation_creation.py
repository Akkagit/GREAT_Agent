import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from great_agent.adapters.model_provider import ModelProposalError
from great_agent.db.models import DailyTaskModel, ExperimentModel
from great_agent.domain.conversation import TurnResponse
from test_api import valid_spec, api_context
from test_conversations import context, FakeEngine


@pytest.mark.asyncio
async def test_global_creation_is_validated_durable_and_replayed_once(context, valid_spec):
    service, plans, fake, sessions = context
    conversation = service.ensure_conversation()

    async def create(kwargs):
        staged = await kwargs['create'](valid_spec.model_dump(mode='json'))
        assert staged['pending'] is True
        assert plans.list_experiments() == []  # Creation waits for turn completion.
    fake.action = create
    request = uuid4()
    response = await service.send_message(conversation['id'], '创建这个实验', request)
    receipt = response['assistant_message']['blocks'][-1]
    assert receipt['type'] == 'experiment_created'
    experiment_id = UUID(receipt['experiment_id'])
    assert plans.get_experiment(experiment_id).status == 'draft'
    assert plans.get_current_revision(experiment_id).spec == valid_spec
    assert receipt['spec'] == valid_spec.model_dump(mode='json')
    TurnResponse.model_validate(response)
    assert service.get_conversation(conversation['id'])['messages'][-1]['blocks'][-1] == receipt
    assert await service.send_message(conversation['id'], '创建这个实验', request) == response
    assert len(plans.list_experiments()) == 1
    assert fake.calls == 1
    assert service.get_conversation(conversation['id'])['experiment_id'] is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0


@pytest.mark.asyncio
async def test_creation_callback_is_unavailable_in_bound_conversation(context, valid_spec):
    service, plans, fake, _ = context
    experiment = plans.create_experiment(valid_spec)
    conversation = service.ensure_conversation(experiment.id)
    async def inspect(kwargs):
        assert kwargs.get('create') is None
    fake.action = inspect
    await service.send_message(conversation['id'], '解释计划', uuid4())
    assert len(plans.list_experiments()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_field,bad_value', [('stations', []), ('solver_profile_id', 'unknown'), ('concurrency', 99)])
async def test_invalid_creation_has_no_side_effects(context, valid_spec, bad_field, bad_value):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    async def create(kwargs):
        candidate = valid_spec.model_dump(mode='json')
        candidate[bad_field] = bad_value
        await kwargs['create'](candidate)
    fake.action = create
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'], '创建实验', uuid4())
    assert plans.list_experiments() == []


@pytest.mark.asyncio
async def test_creation_is_not_committed_when_model_fails_after_staging(context, valid_spec):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    async def create(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
        raise RuntimeError('provider failed after tool result')
    fake.action = create
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'], '创建实验', uuid4())
    assert plans.list_experiments() == []


@pytest.mark.asyncio
async def test_timeout_cannot_commit_staged_or_late_creation(context, valid_spec):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    service.timeout_seconds = .01
    attempted = asyncio.Event()
    async def late(kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        try:
            await kwargs['create'](valid_spec.model_dump(mode='json'))
        finally:
            attempted.set()
    fake.action = late
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'], '创建实验', uuid4())
    assert attempted.is_set()
    assert plans.list_experiments() == []


@pytest.mark.asyncio
async def test_creation_rolls_back_when_receipt_persistence_fails(context, valid_spec, monkeypatch):
    service, plans, fake, sessions = context
    conversation = service.ensure_conversation()
    async def create(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
    fake.action = create
    def fail(*args, **kwargs):
        raise RuntimeError('receipt write failed')
    monkeypatch.setattr(service, '_complete', fail)
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'], '创建实验', uuid4())
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentModel)) == 0


@pytest.mark.asyncio
async def test_cancel_after_staging_does_not_create_experiment(context, valid_spec):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    staged = asyncio.Event()
    async def create_then_wait(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
        staged.set()
        await asyncio.Event().wait()
    fake.action = create_then_wait
    task = asyncio.create_task(service.send_message(conversation['id'], '创建实验', uuid4()))
    await staged.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plans.list_experiments() == []
    assert service.get_conversation(conversation['id'])['messages'][-1]['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_server_rejects_multiple_creation_attempts(context, valid_spec):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    async def duplicate(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
        await kwargs['create'](valid_spec.model_dump(mode='json'))
    fake.action = duplicate
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'], '创建实验', uuid4())
    assert plans.list_experiments() == []


def test_creation_receipt_survives_api_serialization(api_context, valid_spec):
    from great_agent.services.conversation_service import ConversationService
    client, services, sessions = api_context
    fake = FakeEngine()
    services.conversation_service = ConversationService(sessions, plan_service=services.plan_service, engine=fake)
    async def create(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
    fake.action = create
    with client:
        cid = client.post('/api/v1/conversations', json={}).json()['id']
        response = client.post(f'/api/v1/conversations/{cid}/messages', json={
            'message': '创建实验', 'client_request_id': str(uuid4())})
        assert response.status_code == 200
        receipt = response.json()['assistant_message']['blocks'][-1]
        assert receipt['type'] == 'experiment_created'
        assert client.get(f'/api/v1/conversations/{cid}').json()['messages'][-1]['blocks'][-1] == receipt


@pytest.mark.asyncio
async def test_followup_receives_authoritative_creation_receipt(context, valid_spec):
    service, plans, fake, _ = context
    conversation = service.ensure_conversation()
    async def create(kwargs):
        await kwargs['create'](valid_spec.model_dump(mode='json'))
    fake.action = create
    response = await service.send_message(conversation['id'], '创建实验', uuid4())
    experiment_id = response['assistant_message']['blocks'][-1]['experiment_id']
    async def inspect(kwargs):
        assert kwargs['state'] is None
        assert experiment_id in str(kwargs['history'])
        assert 'experiment_created' in str(kwargs['history'])
    fake.action = inspect
    await service.send_message(conversation['id'], '刚创建的实验编号是什么？', uuid4())
    assert len(plans.list_experiments()) == 1
