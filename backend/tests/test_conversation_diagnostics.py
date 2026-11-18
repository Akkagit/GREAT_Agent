import json
import logging
from uuid import uuid4

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProposalError, ModelProviderError
from test_conversations import context, valid_spec


def stages(caplog):
    return [json.loads(record.getMessage().split('chat_conversation_stage ', 1)[1])
            for record in caplog.records
            if record.getMessage().startswith('chat_conversation_stage ')]


@pytest.mark.asyncio
async def test_text_turn_logs_correlated_stages_without_message_contents(context, caplog):
    service, _, _, _ = context
    conversation = service.ensure_conversation()
    request_id = uuid4()
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        response = await service.send_message(conversation['id'], 'private-user-text', request_id)

    events = stages(caplog)
    assert {(event['stage'], event['event']) for event in events} == {
        ('plan_load', 'started'), ('plan_load', 'completed'),
        ('model_request', 'started'), ('model_request', 'completed'),
        ('persistence', 'started'), ('persistence', 'completed'),
    }
    assert all(event['request_id'] == str(request_id) and event['conversation_id'] == conversation['id']
               and event['run_id'] == response['run_id'] and event['elapsed_ms'] >= 0 for event in events)
    assert 'private-user-text' not in caplog.text
    assert response['assistant_message']['blocks'] == [{'type': 'text', 'text': '解释'}]


@pytest.mark.asyncio
async def test_model_failure_logs_safe_cause_type_and_http_status(context, caplog):
    service, _, fake, _ = context
    conversation = service.ensure_conversation()
    request_id = uuid4()

    async def fail(_):
        request = httpx.Request('POST', 'https://private-provider-url.invalid/private-key')
        response = httpx.Response(401, request=request, text='private-provider-body')
        try:
            raise httpx.HTTPStatusError('private-upstream-exception', request=request, response=response)
        except httpx.HTTPStatusError as error:
            raise ModelProviderError('private-wrapped-exception') from error

    fake.action = fail
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        with pytest.raises(ModelProposalError) as error:
            await service.send_message(conversation['id'], 'private-user-text', request_id)

    failed = next(event for event in stages(caplog) if event['event'] == 'failed')
    assert failed['stage'] == 'model_request'
    assert failed['error_category'] == 'model'
    assert failed['exception_type'] == 'ModelProviderError'
    assert failed['cause_types'] == ['HTTPStatusError']
    assert failed['upstream_status'] == 401
    assert failed['request_id'] == str(request_id)
    assert failed['conversation_id'] == conversation['id']
    assert failed['run_id']
    assert error.value.category == 'provider'
    assert error.value.request_id == str(request_id)
    assert 'private-' not in caplog.text + str(error.value)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize('code, expected', [('missing_api_key', 'missing_api_key'), (['private-code'], None)])
async def test_model_diagnostic_codes_are_allowlisted(context, caplog, code, expected):
    service, _, fake, _ = context
    conversation = service.ensure_conversation()

    async def fail(_):
        error = ModelProviderError('private-model-error')
        error.diagnostic_code = code
        error.upstream_status = 'private-status'
        # The runtime deliberately suppresses exception text when wrapping.
        try:
            raise error
        except ModelProviderError:
            raise ModelProviderError('private-wrapper') from None

    fake.action = fail
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        with pytest.raises(ModelProposalError):
            await service.send_message(conversation['id'], '问题', uuid4())

    failed = next(event for event in stages(caplog) if event['event'] == 'failed')
    assert failed.get('diagnostic_code') == expected
    assert 'upstream_status' not in failed
    assert 'private-' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('broken_stage', ['plan_load', 'persistence'])
async def test_application_failure_keeps_its_stage_and_internal_classification(context, caplog, monkeypatch, broken_stage):
    service, _, fake, _ = context
    conversation = service.ensure_conversation()

    def fail(*args, **kwargs):
        raise RuntimeError('private-application-error')

    monkeypatch.setattr(service, '_context' if broken_stage == 'plan_load' else '_complete', fail)
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        with pytest.raises(ModelProposalError):
            await service.send_message(conversation['id'], '问题', uuid4())

    failed = next(event for event in stages(caplog) if event['event'] == 'failed')
    assert (failed['stage'], failed['error_category'], failed['exception_type']) == (
        broken_stage, 'internal', 'RuntimeError')
    assert fake.calls == (0 if broken_stage == 'plan_load' else 1)
    assert 'private-application-error' not in caplog.text


@pytest.mark.asyncio
async def test_missing_execution_preview_logs_server_path_without_model(context, valid_spec, caplog):
    service, plans, fake, _ = context
    experiment = plans.create_experiment(valid_spec)
    conversation = service.ensure_conversation(experiment.id)
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        response = await service.send_message(conversation['id'], '确认并开始解算', uuid4())

    events = stages(caplog)
    confirmation = next(event for event in events
                        if event['stage'] == 'execution_confirmation' and event['event'] == 'completed')
    assert confirmation['preview_attached'] is False
    assert not any(event['stage'] == 'model_request' for event in events)
    assert fake.calls == 0
    assert '预览' in response['assistant_message']['blocks'][0]['text']
    assert any(event['stage'] == 'persistence' and event['event'] == 'completed' for event in events)


@pytest.mark.asyncio
async def test_proposal_validation_failure_is_visible_before_model_wrapping(context, valid_spec, caplog):
    service, plans, fake, _ = context
    experiment = plans.create_experiment(valid_spec)
    conversation = service.ensure_conversation(experiment.id)

    async def fail(kwargs):
        await kwargs['propose']({'stations': ['private-invalid-candidate']}, 'private-explanation')

    fake.action = fail
    with caplog.at_level(logging.INFO, logger='uvicorn.error.great_agent.chat'):
        with pytest.raises(ModelProposalError):
            await service.send_message(conversation['id'], '修改', uuid4())

    failed = [event for event in stages(caplog) if event['event'] == 'failed']
    assert any(event['stage'] == 'proposal_validation' and event['error_category'] == 'schema'
               and event['exception_type'] == 'ValidationError' for event in failed)
    assert plans.list_proposals(experiment.id) == []
    assert 'private-' not in caplog.text
