"""Evidence callbacks preserve complete scientific text and share one budget."""
import copy
import json

import pytest

from test_knowledge_chat_repository import env  # noqa: F401
from test_knowledge_chat_service import service
from test_knowledge_repository import parsed, publish


def republish(environment, document, body, *, chunks=None):
    knowledge = environment[1]
    job = knowledge.queue_reparse(document['id'], 'baseline', 'evidence-' + document['id'])
    payload = parsed(body)
    if chunks:
        payload['chunks'] = [{**payload['chunks'][0], 'ordinal': index, 'text': value}
                             for index, value in enumerate(chunks)]
    return publish(knowledge, {'document': document, 'job': job}, payload=payload)


class CallbackEngine:
    def __init__(self, callback):
        self.callback = callback

    async def reply(self, question, history, search_knowledge, read_evidence, on_request):
        result = await self.callback(search_knowledge, read_evidence)
        return result or {'text': 'Evidence insufficient.', 'source_ids': [], 'answerable': False,
                          'metrics': {}, 'outbound_messages': []}


@pytest.mark.asyncio
async def test_parent_fitting_registry_budget_is_not_cut_at_1400(env):
    body = 'ambiguity ' + 'x' * 1389 + '6.01 mm.'
    republish(env, env[3], body)
    seen = {}
    async def inspect(search, read):
        seen.update(await search('ambiguity'))
    await service(env, CallbackEngine(inspect)).send_message(env[5]['id'], 'question', 'whole-parent')
    assert seen['items'][0]['evidence_text'] == body
    assert 'locations' not in seen['items'][0]
    assert seen['items'][0]['pages'] == [{'page_index': 0, 'page_label': 'i'}]


@pytest.mark.asyncio
async def test_repeated_searches_only_acknowledge_already_sent_sources(env):
    body = 'ambiguity ' + 'Measurements use a complete definition. ' * 45
    for document in (env[3], env[4]):
        republish(env, document, body)
    env[0].update_binding(env[5]['id'], expected_revision=1, scope_mode='selected',
        document_ids=[env[3]['id'], env[4]['id']], model_profile_id='default')
    replies = []
    async def inspect(search, read):
        replies.append(await search('ambiguity'))
        replies.append(await search('ambiguity'))
        replies.append(await search('ambiguity'))
        replies.append(await read(['S1', 'S2']))
    await service(env, CallbackEngine(inspect)).send_message(env[5]['id'], 'question', 'dedup-search')
    assert len(replies[0]['items']) == 2
    assert replies[1]['items'] == replies[2]['items'] == []
    assert replies[1]['existing_source_ids'] == ['S1', 'S2']
    assert sum(len(json.dumps(reply, ensure_ascii=False, separators=(',', ':'))) for reply in replies) <= 12000
    assert all(reply['budget_remaining']['tool_characters'] >= 0 for reply in replies)


@pytest.mark.asyncio
async def test_unsafe_long_numeric_line_is_omitted_with_clear_warning(env):
    body = 'ambiguity ' + 'x' * 1389 + '6.01 mm' + 'x' * 5000
    republish(env, env[3], body)
    seen = {}
    async def inspect(search, read):
        seen.update(await search('ambiguity'))
    await service(env, CallbackEngine(inspect)).send_message(env[5]['id'], 'question', 'no-partial-number')
    assert seen['items'] == []
    assert seen['warning']


@pytest.mark.asyncio
async def test_read_adds_new_sid_without_mutating_initial_excerpt(env):
    from great_agent.db.knowledge_chat_models import KnowledgeRetrievalTraceModel
    body = 'ambiguity phase residual = 6.01 mm.\n' * 250
    republish(env, env[3], body)
    seen = {}
    async def inspect(search, read):
        first = await search('ambiguity')
        seen['first'] = copy.deepcopy(first['items'][0])
        seen['read'] = await read(['S1'])
        return {'text': 'Evidence [S1] and [S2].', 'source_ids': ['S1', 'S2'], 'answerable': True,
                'metrics': {}, 'outbound_messages': []}
    result = await service(env, CallbackEngine(inspect)).send_message(env[5]['id'], 'question', 'read-expansion')
    assert seen['read']['items'][0]['source_id'] == 'S2'
    with env[-1]() as session:
        evidence = json.loads(session.get(KnowledgeRetrievalTraceModel, result['run_id']).evidence_json)
    assert evidence[0]['evidence_text'] == seen['first']['evidence_text']
    assert evidence[1]['source_spans'][0]['start'] >= evidence[0]['source_spans'][0]['end']
    assert all(item['evidence_text'].endswith('\n') or item['evidence_text'].endswith('.') for item in evidence)


@pytest.mark.asyncio
async def test_read_can_add_adjacent_definition_from_same_fixed_version(env):
    first = 'PPP ambiguity uses the definition in the following paragraph.'
    second = 'The receiver term is the clock offset measured in nanoseconds.'
    republish(env, env[3], first + '\n\n' + second, chunks=[first, second])
    seen = {}
    async def inspect(search, read):
        await search('ambiguity')
        seen.update(await read(['S1']))
    await service(env, CallbackEngine(inspect)).send_message(env[5]['id'], 'question', 'adjacent-definition')
    assert seen['items'][0]['source_id'] == 'S2'
    assert seen['items'][0]['evidence_text'] == second


@pytest.mark.asyncio
@pytest.mark.parametrize('caption,flags,kind,query,prioritize', [
    ('Table 24\nLabel Unit\n', [], 'text', 'Table24 statistics', True),
    ('表24\nLabel Unit\n', [], 'text', '表格统计', True),
    ('Table 24\nLabel Unit\n', ['table_structure_unverified'], 'table', 'comparison', True),
    ('Introduction references Table 24\n', [], 'text', 'main methods and contributions', False),
    ('Structured results\n', ['table_structure_unverified'], 'text', 'table statistics', False),
])
async def test_top_table_context_reserves_same_page_next_chunk_before_lower_ranks(env, caption, flags, kind, query, prioritize):
    from great_agent.db.knowledge_chat_models import KnowledgeRetrievalTraceModel
    header = caption + 'h' * (2767 - len(caption))
    left = 'Repeated discussion ' + 'x' * 1680
    numeric = 'Residual RMS 7.25 mm; ' + 'v' * 900
    knowledge = env[1]
    job = knowledge.queue_reparse(env[3]['id'], 'baseline', 'table-context')
    payload = parsed('\n\n'.join([left, header, numeric]))
    payload['chunks'] = [{**payload['chunks'][0], 'ordinal': index, 'text': text,
                          'kind': kind if index == 1 else 'text',
                          'quality_flags': flags if index == 1 else []}
                         for index, text in enumerate([left, header, numeric])]
    publish(knowledge, {'document': env[3], 'job': job}, payload=payload)
    seen = {}
    async def inspect(search, read):
        seen.update(await search(query))
    chat = service(env, CallbackEngine(inspect))
    async def ranked(snapshot, query, limit=8):
        chunks = env[0].list_snapshot_chunks(snapshot)
        return {'mode': 'lexical', 'items': [chunks[1], chunks[0], chunks[2]],
                'queries': [query], 'candidates': [], 'timings': {}}
    chat.retriever.search = ranked
    result = await chat.send_message(env[5]['id'], 'question', 'table-context')
    texts = [item['evidence_text'] for item in seen['items']]
    expected_second = numeric if prioritize else left
    assert texts[:2] == [header, expected_second]
    assert [item['source_id'] for item in seen['items'][:2]] == ['S1', 'S2']
    with env[-1]() as session:
        stored = json.loads(session.get(KnowledgeRetrievalTraceModel, result['run_id']).evidence_json)
    assert stored[1]['source_spans'][0]['start'] == 0
    assert stored[1]['source_spans'][0]['end'] == len(expected_second)
    assert len(json.dumps(seen, ensure_ascii=False, separators=(',', ':'))) <= 12000
