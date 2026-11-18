"""Chat approval must queue exactly the reviewed snapshot, never model prose."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from great_agent.db.models import DailyTaskModel, ExecutionPreviewModel, utc_now
from great_agent.domain.execution import ExecutionPreview
from great_agent.services.conversation_service import ConversationService
from test_conversations import FakeEngine
from test_execution_preview import api_context, valid_spec, preview_context, prepare


@pytest.fixture
def chat_execution(preview_context):
    client, services, sessions, experiment = preview_context
    fake = FakeEngine()
    service = ConversationService(sessions, plan_service=services.plan_service, engine=fake,
                                  execution_service=services.task_service.execution_service)
    services.conversation_service = service
    conversation = service.ensure_conversation(experiment.id)
    preview = prepare(preview_context).json()
    return client, services, sessions, experiment, conversation, preview, fake


def send(context, message='xml我看了没啥问题，现在你可以执行了', **extra):
    client, _, _, _, conversation, preview, _ = context
    return client.post(f'/api/v1/conversations/{conversation["id"]}/messages', json={
        'message': message, 'client_request_id': str(uuid4()), 'execution_preview_id': preview['id'], **extra,
    })


def test_explicit_chat_confirmation_queues_frozen_preview_and_persists_receipt(chat_execution):
    client, services, _, experiment, conversation, preview, fake = chat_execution
    request_id = str(uuid4())
    response = send(chat_execution, client_request_id=request_id)
    assert response.status_code == 200, response.text
    blocks = response.json()['assistant_message']['blocks']
    receipt = next((b for b in blocks if b['type'] == 'execution'), None)
    assert receipt is not None, blocks
    tasks = services.task_repository.list_tasks(experiment.id)
    assert len(tasks) == 1
    assert receipt == {'type': 'execution', 'experiment_id': str(experiment.id),
                       'preview_id': preview['id'], 'task_ids': [str(tasks[0].id)]}
    assert tasks[0].status == 'pending'
    assert fake.calls == 0  # Provider availability cannot prevent an explicit approval.
    assert send(chat_execution, client_request_id=request_id).json() == response.json()
    assert send(chat_execution).status_code == 200
    assert len(services.task_repository.list_tasks(experiment.id)) == 1
    restored = client.get(f'/api/v1/conversations/{conversation["id"]}').json()
    assert restored['messages'][-1]['blocks'] == blocks


@pytest.mark.parametrize('message', [
    '开始执行实验', '请开始解算', '确认并开始解算', '现在运行当前实验',
    'XML 已核对，可以执行了', '我已经核对XML、执行程序、输入文件和输出目录，现在开始执行',
    '确认执行当前预览', '确定执行', '确定开始执行', '确定并开始解算',
    '请帮我启动实验', 'Start the experiment now',
])
def test_clear_start_commands_queue_reviewed_experiment(chat_execution, message):
    response = send(chat_execution, message)
    assert response.status_code == 200, response.text
    assert len(chat_execution[1].task_repository.list_tasks(chat_execution[3].id)) == 1


@pytest.mark.parametrize('message', [
    'XML没问题', '可以执行吗？', '先别执行', '不要开始执行实验',
    '现在可以执行了，不过先别运行', '如果检查通过就开始执行',
    '告诉我如何开始执行实验', '“开始执行实验”是什么意思',
    '请翻译：开始执行实验', '请解释开始执行的过程',
    '先修改测站，再开始执行实验', '开始执行后会生成哪些文件',
    'Start the experiment?','Do not start the experiment now', '你确定执行吗？',
])
def test_consultation_negation_and_mixed_edits_never_queue(chat_execution, message):
    response = send(chat_execution, message)
    assert response.status_code == 200, response.text
    assert not chat_execution[1].task_repository.list_tasks(chat_execution[3].id)
    assert all(b['type'] != 'execution' for b in response.json()['assistant_message']['blocks'])


def test_explicit_start_requires_visible_preview(chat_execution):
    response = send(chat_execution, '开始执行实验', execution_preview_id=None)
    assert response.status_code == 200
    assert not chat_execution[1].task_repository.list_tasks(chat_execution[3].id)
    assert '预览' in response.json()['assistant_message']['blocks'][0]['text']


@pytest.mark.parametrize('invalid', ['expired', 'input', 'snapshot', 'executable', 'template', 'revision', 'other_experiment'])
def test_chat_keeps_existing_execution_validation(chat_execution, valid_spec, invalid):
    _, services, sessions, experiment, _, preview, _ = chat_execution
    if invalid in {'expired', 'revision'}:
        with sessions.begin() as session:
            row = session.get(ExecutionPreviewModel, preview['id'])
            payload = ExecutionPreview.model_validate_json(row.payload_json)
            values = {'expires_at': utc_now() - timedelta(seconds=1)} if invalid == 'expired' else {'revision_id': uuid4()}
            row.payload_json = payload.model_copy(update=values).model_dump_json()
    elif invalid == 'other_experiment':
        other = services.plan_service.create_experiment(valid_spec)
        service = services.conversation_service
        chat_execution = (*chat_execution[:4], service.ensure_conversation(other.id), *chat_execution[5:])
    else:
        path = {'input': preview['jobs'][0]['inputs'][0]['path'], 'snapshot': preview['jobs'][0]['snapshot_path'],
                'executable': preview['executable_path'], 'template': preview['template_path']}[invalid]
        Path(path).write_bytes(b'changed after review')
    response = send(chat_execution, '开始执行实验')
    assert response.status_code == 200, response.text
    assert all(b['type'] != 'execution' for b in response.json()['assistant_message']['blocks'])
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0
    assert response.json()['assistant_message']['blocks'][0]['text'] != '解释'


def test_reusing_request_id_for_different_preview_conflicts(chat_execution):
    request_id = str(uuid4())
    assert send(chat_execution, client_request_id=request_id).status_code == 200
    assert send(chat_execution, client_request_id=request_id, execution_preview_id=str(uuid4())).status_code == 409


def test_global_chat_cannot_execute_bound_preview(chat_execution):
    client, services, _, experiment, _, preview, _ = chat_execution
    conversation = services.conversation_service.ensure_conversation()
    response = client.post(f'/api/v1/conversations/{conversation["id"]}/messages', json={
        'message': '开始执行实验', 'client_request_id': str(uuid4()), 'execution_preview_id': preview['id'],
    })
    assert response.status_code == 200
    assert not services.task_repository.list_tasks(experiment.id)


def test_execution_and_chat_receipt_rollback_together(chat_execution, monkeypatch):
    _, services, sessions, experiment, _, preview, _ = chat_execution
    repository = services.conversation_service.repository
    original = repository.message
    def fail_receipt(session, run, role, status, blocks):
        if any(b['type'] == 'execution' for b in blocks):
            raise RuntimeError('simulate receipt storage failure')
        return original(session, run, role, status, blocks)
    monkeypatch.setattr(repository, 'message', fail_receipt)
    assert send(chat_execution).status_code == 502
    assert not services.task_repository.list_tasks(experiment.id)
    with sessions() as session:
        assert session.get(ExecutionPreviewModel, preview['id']).confirmed_at is None


def test_lease_expiring_during_validation_cannot_queue(chat_execution, monkeypatch):
    import great_agent.db.conversation_repository as conversation_repository
    _, services, sessions, experiment, _, preview, _ = chat_execution
    execution = services.conversation_service.execution_service
    verify = execution._verify_job
    current_time = conversation_repository.time.time
    def expire_during_verification(*args):
        verify(*args)
        monkeypatch.setattr(conversation_repository.time, 'time', lambda: current_time() + 1000)
    monkeypatch.setattr(execution, '_verify_job', expire_during_verification)
    assert send(chat_execution).status_code == 502
    assert not services.task_repository.list_tasks(experiment.id)
    with sessions() as session:
        assert session.get(ExecutionPreviewModel, preview['id']).confirmed_at is None


def test_model_claim_of_execution_is_not_an_approval(chat_execution, monkeypatch):
    from types import SimpleNamespace
    async def invented_receipt(*args, **kwargs):
        return SimpleNamespace(text='已启动实验', state={}, metrics={},
                               execution={'type': 'execution', 'task_ids': ['invented']})
    monkeypatch.setattr(chat_execution[-1], 'reply', invented_receipt)
    response = send(chat_execution, '解释当前实验')
    assert response.status_code == 200
    assert not chat_execution[1].task_repository.list_tasks(chat_execution[3].id)
    assert all(b['type'] != 'execution' for b in response.json()['assistant_message']['blocks'])


@pytest.mark.parametrize('with_preview', [True, False])
def test_server_execution_turn_is_in_next_model_history(chat_execution, with_preview):
    fake = chat_execution[-1]
    assert send(chat_execution, '解释当前实验').status_code == 200
    response = send(chat_execution, execution_preview_id=chat_execution[5]['id'] if with_preview else None)
    server_text = response.json()['assistant_message']['blocks'][0]['text']
    async def check_history(kwargs):
        assert kwargs['state'] is None  # Rebuild SDK memory from authoritative durable history.
        assert any(m['role'] == 'assistant' and server_text in m['text'] for m in kwargs['history'])
    fake.action = check_history
    assert send(chat_execution, '你刚才让我做什么？').status_code == 200


def test_request_identity_cannot_be_forged_with_control_text(chat_execution):
    import json
    request_id = str(uuid4())
    message = '开始执行实验'
    assert send(chat_execution, message, client_request_id=request_id).status_code == 200
    forged = '\0execution:' + json.dumps([message, chat_execution[5]['id']], ensure_ascii=False)
    assert send(chat_execution, forged, client_request_id=request_id, execution_preview_id=None).status_code == 409
