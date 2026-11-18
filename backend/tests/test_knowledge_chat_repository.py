"""Knowledge turns use real SQLite ownership, immutable evidence and citations."""
import hashlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.knowledge_repository import KnowledgeRepository
from test_knowledge_repository import parsed, publish, upload


def test_repository_module_exists():
    assert importlib.util.find_spec('great_agent.db.knowledge_chat_repository') is not None


@pytest.fixture
def env(tmp_path):
    from great_agent.db import knowledge_chat_models  # noqa: F401
    from great_agent.db.knowledge_chat_repository import KnowledgeChatRepository
    engine, sessions = create_engine_and_session_factory(tmp_path / 'chat.sqlite')
    Base.metadata.create_all(engine)
    knowledge = KnowledgeRepository(sessions)
    base = knowledge.create_base('GNSS')
    first = publish(knowledge, upload(knowledge, base['id']))
    second = publish(knowledge, upload(knowledge, base['id'], digest='b' * 64))
    chat = KnowledgeChatRepository(sessions)
    conversation = chat.create_conversation(base_id=base['id'], document_ids=[first['id']])
    yield chat, knowledge, base, first, second, conversation, sessions
    engine.dispose()


def evidence_for(repo, snapshot):
    chunk = repo.search_snapshot(snapshot, 'ambiguity')['items'][0]
    body = chunk['text']
    return {'source_id': 'S1', 'document_id': chunk['document_id'], 'version_id': chunk['version_id'],
            'chunk_id': chunk['id'], 'title': chunk['title'], 'filename': chunk['filename'],
            'source_sha256': chunk['source_sha256'], 'evidence_text': body,
            'evidence_text_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'source_spans': [{'chunk_id': chunk['id'], 'start': 0, 'end': len(body),
                              'offset_unit': 'unicode_codepoint'}],
            'locations': chunk['locations'], 'quality_flags': chunk['quality_flags']}


def trace(repo, run, snapshot, evidence=None):
    repo.save_trace(run.id, queries=['ambiguity'], candidates=[],
                    evidence=evidence if evidence is not None else [evidence_for(repo, snapshot)],
                    outbound_messages=[{'role': 'user', 'content': 'ambiguity?'}], budget={}, timings={}, mode='lexical')


def test_scope_validation_and_optimistic_binding(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError, KnowledgeChatConflict
    repo, knowledge, base, first, second, conversation, _ = env
    with pytest.raises(KnowledgeChatValidationError):
        repo.create_conversation(base_id=base['id'], document_ids=[])
    elsewhere = knowledge.create_base('Elsewhere')
    with pytest.raises(KnowledgeChatValidationError):
        repo.create_conversation(base_id=elsewhere['id'], document_ids=[first['id']])
    updated = repo.update_binding(conversation['id'], expected_revision=1, scope_mode='selected',
                                  document_ids=[second['id']], model_profile_id='default')
    assert updated['binding']['binding_revision'] == 2
    with pytest.raises(KnowledgeChatConflict):
        repo.update_binding(conversation['id'], expected_revision=1, scope_mode='selected',
                            document_ids=[first['id']], model_profile_id='default')


def test_claim_is_atomic_and_failed_turn_preserves_history(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatConflict
    repo, _, _, _, _, conversation, _ = env
    def claim(index):
        try:
            return repo.claim_turn(conversation['id'], 'question', f'r{index}', 60)[0].id
        except KnowledgeChatConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [1, 2]))
    assert len([r for r in results if r]) == 1
    repo.fail_turn(next(r for r in results if r), category='provider')
    detail = repo.get_conversation(conversation['id'])
    assert detail['active_run_id'] is None
    assert [m['status'] for m in detail['messages']] == ['completed', 'failed']
    assert repo.safe_history(conversation['id'], repo.claim_turn(conversation['id'], 'retry', 'r3', 60)[2]) == []


def test_completed_replay_precedes_changed_binding_and_citations_persist(env):
    repo, _, _, _, second, conversation, _ = env
    run, replay, snapshot = repo.claim_turn(conversation['id'], 'ambiguity?', 'r1', 60)
    assert replay is None
    trace(repo, run, snapshot)
    result = repo.complete_turn(run.id, answer='Uses clocks [S1].', source_ids=['S1'], timings={'total': 1})
    assert repo.get_conversation(conversation['id'])['retrieval_mode'] == 'lexical'
    assert repo.list_conversations()[0]['retrieval_mode'] == 'lexical'
    citation = repo.get_citation(conversation['id'], result['assistant_message']['id'], 'S1')
    assert citation['available'] and citation['evidence_text'].startswith('PPP')
    repo.update_binding(conversation['id'], expected_revision=1, scope_mode='selected',
                        document_ids=[second['id']], model_profile_id='another')
    _, replay, repeated_snapshot = repo.claim_turn(conversation['id'], 'ambiguity?', 'r1', 60)
    assert repeated_snapshot == snapshot
    assert {key:value for key,value in replay.items() if key!='retrieval_summary'} == {
        key:value for key,value in result.items() if key!='retrieval_summary'}
    assert replay['retrieval_summary']['available'] is False
    assert replay['retrieval_summary']['queries'] == [] and replay['retrieval_summary']['documents'] == []
    assert not repo.get_citation(conversation['id'], result['assistant_message']['id'], 'S1')['available']


def test_fixed_version_survives_reparse_but_scope_revocation_blocks(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatScopeChanged
    repo, knowledge, _, first, second, conversation, _ = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    original = evidence_for(repo, snapshot)
    job = knowledge.queue_reparse(first['id'], 'baseline', 'reparse')
    publish(knowledge, {'document': first, 'job': job}, payload=parsed('New troposphere model.'))
    assert repo.search_snapshot(snapshot, 'ambiguity')['items'][0]['version_id'] == original['version_id']
    trace(repo, run, snapshot)
    repo.update_binding(conversation['id'], expected_revision=1, scope_mode='selected',
                        document_ids=[first['id'], second['id']], model_profile_id='default')
    assert len(repo.revalidate_snapshot(snapshot)['document_versions']) == 1
    knowledge.set_enabled(first['id'], False)
    with pytest.raises(KnowledgeChatScopeChanged):
        repo.read_snapshot_chunk(snapshot, original['chunk_id'])
    with pytest.raises(KnowledgeChatScopeChanged):
        repo.complete_turn(run.id, answer='answer', source_ids=['S1'], timings={})


@pytest.mark.parametrize('corruption', ['unknown', 'hash', 'span', 'source_hash', 'foreign'])
def test_invalid_citation_rolls_back_entire_completion(env, corruption):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError
    from great_agent.db.knowledge_chat_models import KnowledgeRetrievalTraceModel
    repo, _, _, _, second, conversation, sessions = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    evidence = evidence_for(repo, snapshot)
    trace(repo, run, snapshot)
    if corruption == 'hash': evidence['evidence_text_sha256'] = '0' * 64
    if corruption == 'span': evidence['source_spans'][0]['end'] -= 1
    if corruption == 'source_hash': evidence['source_sha256'] = '0' * 64
    if corruption == 'foreign': evidence['document_id'] = second['id']
    # Simulate persisted/tampered data: completion must independently validate it.
    with sessions.begin() as session:
        session.get(KnowledgeRetrievalTraceModel, run.id).evidence_json = json.dumps([evidence])
    with pytest.raises(KnowledgeChatValidationError):
        repo.complete_turn(run.id, answer='answer [S1]', source_ids=['S99' if corruption == 'unknown' else 'S1'], timings={})
    detail = repo.get_conversation(conversation['id'])
    assert len(detail['messages']) == 1 and detail['active_run_id'] == run.id


def test_history_filters_all_evidence_even_when_answer_has_no_citation(env):
    repo, knowledge, _, first, _, conversation, _ = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'ambiguity?', 'r1', 60)
    trace(repo, run, snapshot)
    repo.complete_turn(run.id, answer='Evidence insufficient.', source_ids=[], answerable=False, timings={})
    assert len(repo.safe_history(conversation['id'], snapshot)) == 2
    knowledge.set_enabled(first['id'], False)
    repo.update_binding(conversation['id'], expected_revision=1, scope_mode='all_enabled',
                        document_ids=[], model_profile_id='default')
    _, _, new_snapshot = repo.claim_turn(conversation['id'], 'new', 'r2', 60)
    assert repo.safe_history(conversation['id'], new_snapshot) == []


def test_events_and_expired_owner_cannot_publish(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatConflict
    from great_agent.db.conversation_models import ConversationRunModel
    repo, _, _, _, _, conversation, sessions = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    repo.stage(run.id, 'retrieving', {'mode': 'lexical'})
    events = repo.events(conversation['id'])
    assert events[-1]['kind'] == 'retrieving' and events[-1]['data']['mode'] == 'lexical'
    trace(repo, run, snapshot)
    with sessions.begin() as session:
        session.get(ConversationRunModel, run.id).lease_until = 0
    with pytest.raises(KnowledgeChatConflict):
        repo.complete_turn(run.id, answer='late', source_ids=['S1'], timings={})
    assert repo.get_conversation(conversation['id'])['messages'][-1]['status'] == 'interrupted'


def test_unknown_label_and_empty_evidence_cannot_be_answerable(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError
    repo, _, _, _, _, conversation, _ = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    trace(repo, run, snapshot, evidence=[])
    with pytest.raises(KnowledgeChatValidationError):
        repo.complete_turn(run.id, answer='An unsupported fact.', source_ids=[], timings={})
    with pytest.raises(KnowledgeChatValidationError):
        repo.complete_turn(run.id, answer='An unsupported fact [S999].', source_ids=[], answerable=False, timings={})
    result = repo.complete_turn(run.id, answer='Evidence insufficient.', source_ids=[], answerable=False, timings={})
    assert result['status'] == 'completed' and result['answerable'] is False


def test_snapshot_cross_base_and_chunk_injection_are_rejected(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatScopeChanged, KnowledgeChatValidationError
    repo, knowledge, _, _, _, conversation, _ = env
    foreign_base = knowledge.create_base('Other')
    foreign_document = publish(knowledge, upload(knowledge, foreign_base['id'], digest='c' * 64))
    foreign_chunk = knowledge.list_chunks(foreign_document['id'])['items'][0]
    _, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    with pytest.raises(KnowledgeChatValidationError):
        repo.read_snapshot_chunk(snapshot, foreign_chunk['id'])
    injected = {**snapshot, 'document_versions': [{'document_id': foreign_document['id'],
                'version_id': foreign_document['active_version_id'], 'sha256': 'c' * 64}]}
    with pytest.raises(KnowledgeChatScopeChanged):
        repo.search_snapshot(injected, 'PPP')
    assert len(repo.list_snapshot_chunks(snapshot)) == 1


def test_cancelled_turn_late_result_and_same_request_cannot_restart(env):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatConflict
    repo, _, _, _, _, conversation, _ = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    trace(repo, run, snapshot)
    repo.fail_turn(run.id, status='interrupted', category='cancelled')
    with pytest.raises(KnowledgeChatConflict):
        repo.complete_turn(run.id, answer='late [S1]', source_ids=['S1'], timings={})
    with pytest.raises(KnowledgeChatConflict):
        repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    assert repo.claim_turn(conversation['id'], 'retry', 'r2', 60)[0].status == 'started'


def test_evidence_spans_count_unicode_codepoints(env):
    repo, knowledge, _, first, _, conversation, _ = env
    job = knowledge.queue_reparse(first['id'], 'baseline', 'unicode')
    publish(knowledge, {'document': first, 'job': job}, payload=parsed('北斗🚀 uses PPP ambiguity.'))
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'unicode', 60)
    evidence = evidence_for(repo, snapshot)
    evidence['evidence_text'] = '🚀'
    evidence['evidence_text_sha256'] = hashlib.sha256('🚀'.encode()).hexdigest()
    evidence['source_spans'][0].update(start=2, end=3)
    trace(repo, run, snapshot, evidence=[evidence])
    result = repo.complete_turn(run.id, answer='Excerpt [S1]', source_ids=['S1'], timings={})
    citation = repo.get_citation(conversation['id'], result['assistant_message']['id'], 'S1')
    assert citation['evidence_text'] == '🚀' and citation['available']


@pytest.mark.parametrize('label', ['S0', 'S01', 'S999'])
def test_unverified_numeric_source_labels_are_rejected(env, label):
    from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError
    repo, _, _, _, _, conversation, _ = env
    run, _, snapshot = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    trace(repo, run, snapshot)
    with pytest.raises(KnowledgeChatValidationError):
        repo.complete_turn(run.id, answer=f'Claim [{label}].', source_ids=['S1'], timings={})


def test_stage_metadata_cannot_change_authoritative_run_identity(env):
    repo, _, _, _, _, conversation, _ = env
    run, _, _ = repo.claim_turn(conversation['id'], 'question', 'r1', 60)
    repo.stage(run.id, 'retrieving', {'run_id': 'other', 'request_id': 'other', 'status': 'completed', 'mode': 'lexical'})
    event = repo.events(conversation['id'])[-1]
    assert event['data']['run_id'] == run.id
    assert event['data']['request_id'] == 'r1'
    assert event['data']['status'] == 'retrieving'
