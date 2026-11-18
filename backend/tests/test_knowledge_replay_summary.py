"""Frozen answers replay with a freshly authorized retrieval summary."""
from uuid import uuid4

from test_knowledge_chat_repository import env  # noqa: F401
from test_knowledge_chat_service import service


async def test_completed_replay_reprojects_summary_after_source_revocation(env):
    repo, knowledge, _, document, _, conversation, _ = env
    chat = service(env)
    request_id = str(uuid4())
    answer = await chat.send_message(conversation['id'], 'ambiguity?', request_id)
    assert answer['retrieval_summary']['queries'] == ['ambiguity']
    assert answer['retrieval_summary']['documents']
    assert await chat.send_message(conversation['id'], 'ambiguity?', request_id) == answer

    knowledge.set_enabled(document['id'], False)
    replay = await chat.send_message(conversation['id'], 'ambiguity?', request_id)
    summary = replay['retrieval_summary']
    assert summary['available'] is False
    assert summary['queries'] == [] and summary['documents'] == []
    assert summary['hit_count'] == 0 and summary['evidence_count'] == 0
    assert replay['assistant_message'] == answer['assistant_message']
    assert replay['user_message'] == answer['user_message']
    assert chat.engine.calls == 1
    await chat.close()
