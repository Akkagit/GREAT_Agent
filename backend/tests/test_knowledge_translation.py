"""Derived excerpt translations use SQLite and a single mocked physical HTTP request."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, func

from great_agent.config import ModelSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.knowledge_repository import KnowledgeRepository
from great_agent.db.knowledge_chat_repository import KnowledgeChatRepository, KnowledgeChatValidationError
from test_knowledge_repository import upload, publish, parsed
from test_knowledge_chat_repository import trace


def test_translation_module_exists():
    import importlib.util
    assert importlib.util.find_spec('great_agent.services.knowledge_translation_service') is not None


@pytest.fixture
def env(tmp_path):
    from great_agent.db.knowledge_translation_repository import KnowledgeTranslationRepository
    from great_agent.services.knowledge_source_validation import KnowledgeSourceValidator
    engine, sessions = create_engine_and_session_factory(tmp_path / 'translation.sqlite')
    Base.metadata.create_all(engine)
    original = tmp_path / 'original.pdf'
    original.write_bytes(b'%PDF-local-immutable-original')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    knowledge = KnowledgeRepository(sessions)
    base = knowledge.create_base('papers')
    body = r'PPP ambiguity decreases by 12.5% when $x_i = \\alpha + 2$ and N = 3.'
    document = publish(knowledge, upload(knowledge, base['id'], digest=digest), payload=parsed(body))
    chat = KnowledgeChatRepository(sessions)
    conversation = chat.create_conversation(base_id=base['id'], document_ids=[document['id']])
    cid = conversation['id']
    run, _, snapshot = chat.claim_turn(cid, 'ambiguity?', str(uuid4()), 60)
    trace(chat, run, snapshot)
    result = chat.complete_turn(run.id, answer='Uses ambiguity [S1].', source_ids=['S1'], timings={})
    mid = result['assistant_message']['id']
    ingestion = SimpleNamespace(original=lambda did: (original, knowledge.get_document(did)))
    cache = KnowledgeTranslationRepository(sessions)
    yield SimpleNamespace(chat=chat, knowledge=knowledge, document=document, original=original,
                          cid=cid, mid=mid, body=body, sessions=sessions, cache=cache,
                          validator=KnowledgeSourceValidator(chat, ingestion))
    engine.dispose()


def provider_response(request, mutate=None):
    payload = json.loads(request.content)
    protected = json.loads(payload['messages'][1]['content'])['evidence_text']
    translated = protected.replace('ambiguity decreases by', '模糊度下降').replace('when', '当').replace('and', '且')
    if mutate:
        translated = mutate(translated)
    return httpx.Response(200, json={'choices':[{'finish_reason':'stop', 'message':{
        'role':'assistant', 'content':json.dumps({'translated_text':translated}, ensure_ascii=False)}}]})


def service(env, handler=provider_response, **kwargs):
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationService
    return KnowledgeTranslationService(env.cache, ModelSettings(name='test-model', base_url='https://example.invalid/v1',
        api_key='server-secret'), source_validator=env.validator, transport=httpx.MockTransport(handler), **kwargs)


def cache_count(env):
    from great_agent.db.knowledge_translation_models import KnowledgeCitationTranslationModel
    with env.sessions() as session:
        return session.scalar(select(func.count()).select_from(KnowledgeCitationTranslationModel))


@pytest.mark.asyncio
async def test_translation_preserves_evidence_numbers_math_and_persistent_cache(env):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return provider_response(request)
    s = service(env, handler)
    result = await s.translate(env.cid, env.mid, 'S1', 'zh-CN')
    assert result['original_text'] == env.body
    assert '模糊度下降' in result['translated_text']
    assert '12.5' in result['translated_text'] and r'$x_i = \\alpha + 2$' in result['translated_text']
    assert 'N = 3' in result['translated_text']
    assert result['cached'] is False and result['target_language'] == 'zh-CN'
    assert result['evidence_text_sha256'] == hashlib.sha256(env.body.encode()).hexdigest()
    assert result['model_profile'] == 'default' and result['translation_version']
    assert len(calls) == 1 and len(calls[0]['messages']) == 2
    assert not any(k in calls[0] for k in ('tools', 'tool_choice'))
    assert calls[0]['max_tokens'] <= 4096 and not calls[0]['stream']
    assert env.body not in json.dumps(calls)  # only the protected excerpt, never history/full PDF
    assert 'ambiguity?' not in json.dumps(calls)
    again = await service(env, lambda _: pytest.fail('persistent cache missed')).translate(env.cid, env.mid, 'S1', 'zh-CN')
    assert again == {**result, 'cached':True}
    assert env.chat.get_citation(env.cid, env.mid, 'S1')['evidence_text'] == env.body


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disabled', 'revoked', 'corrupt'])
async def test_cache_hit_revalidates_scope_and_physical_original(env, change):
    s = service(env)
    await s.translate(env.cid, env.mid, 'S1', 'zh-CN')
    if change == 'disabled':
        env.knowledge.set_enabled(env.document['id'], False)
    elif change == 'revoked':
        second = publish(env.knowledge, upload(env.knowledge, env.document['base_id'], digest='b'*64))
        env.chat.update_binding(env.cid, expected_revision=1, scope_mode='selected', document_ids=[second['id']], model_profile_id='default')
    else:
        env.original.write_bytes(b'corrupt')
    with pytest.raises(KnowledgeChatValidationError):
        await s.translate(env.cid, env.mid, 'S1', 'zh-CN')


@pytest.mark.asyncio
async def test_singleflight_serves_concurrent_waiters_with_one_request(env):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return provider_response(request)
    s = service(env, handler)
    first = asyncio.create_task(s.translate(env.cid, env.mid, 'S1', 'zh-CN'))
    await entered.wait()
    second = asyncio.create_task(s.translate(env.cid, env.mid, 'S1', 'zh-CN'))
    await asyncio.sleep(.03)
    first.cancel()
    with pytest.raises(asyncio.CancelledError): await first
    release.set()
    assert (await second)['translated_text']
    assert calls == 1


@pytest.mark.asyncio
async def test_revoked_during_request_never_caches(env):
    async def handler(request):
        env.knowledge.set_enabled(env.document['id'], False)
        return provider_response(request)
    with pytest.raises(KnowledgeChatValidationError):
        await service(env, handler).translate(env.cid, env.mid, 'S1', 'zh-CN')
    assert cache_count(env) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['placeholder_missing','duplicate_placeholder','extra_number','duplicate_json','tools','refusal','truncated','malformed','oversized','secret'])
async def test_bad_provider_output_is_rejected_without_cache_or_secrets(env, kind):
    def handler(request):
        response = provider_response(request)
        data = response.json()
        message = data['choices'][0]['message']
        if kind == 'placeholder_missing':
            return provider_response(request, lambda text: text.replace('KT_', 'broken_', 1))
        if kind == 'duplicate_placeholder':
            return provider_response(request, lambda text: text + ' ' + next(word for word in text.split() if 'KT_' in word))
        if kind == 'extra_number': return provider_response(request, lambda text: text + ' 999')
        if kind == 'duplicate_json': message['content'] = '{"translated_text":"first","translated_text":"second"}'
        if kind == 'tools': message['tool_calls'] = [{'id':'secret-tool', 'function':{'name':'shell','arguments':'server-secret'}}]
        if kind == 'refusal': message['refusal'] = 'server-secret'
        if kind == 'truncated': data['choices'][0]['finish_reason'] = 'length'
        if kind == 'malformed': message['content'] = 'server-secret invalid JSON'
        if kind == 'oversized': message['content'] = json.dumps({'translated_text':'大'*25000})
        if kind == 'secret': return httpx.Response(500, text='server-secret')
        return httpx.Response(200, json=data)
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationError
    with pytest.raises(KnowledgeTranslationError) as caught:
        await service(env, handler).translate(env.cid, env.mid, 'S1', 'zh-CN')
    assert 'server-secret' not in str(caught.value)
    assert cache_count(env) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cancel','timeout'])
async def test_late_provider_ignoring_cancellation_cannot_cache_or_return(env, mode):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finished.set()
        return provider_response(request)
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationError
    s = service(env, handler, timeout_seconds=.05 if mode == 'timeout' else 45)
    task = asyncio.create_task(s.translate(env.cid, env.mid, 'S1', 'zh-CN'))
    await entered.wait()
    if mode == 'cancel': task.cancel()
    with pytest.raises(asyncio.CancelledError if mode == 'cancel' else KnowledgeTranslationError):
        await asyncio.wait_for(task, 1)
    release.set()
    await asyncio.wait_for(finished.wait(), 1)
    await asyncio.sleep(.05)
    assert cache_count(env) == 0
    await s.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['secret_success','over_token_limit'])
async def test_translation_rejects_provider_secret_echo_and_token_overflow(env, kind):
    def handler(request):
        data = provider_response(request).json()
        content = json.loads(data['choices'][0]['message']['content'])
        if kind == 'secret_success':
            content['translated_text'] += ' server-secret'
        else:
            data['usage'] = {'completion_tokens':4097}
        data['choices'][0]['message']['content'] = json.dumps(content)
        return httpx.Response(200, json=data)
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationError
    with pytest.raises(KnowledgeTranslationError):
        await service(env, handler).translate(env.cid, env.mid, 'S1', 'en')
    assert cache_count(env) == 0


@pytest.mark.asyncio
async def test_each_source_gate_is_checked_before_cache_write_and_return(env):
    from great_agent.db.knowledge_translation_models import KnowledgeCitationTranslationModel
    calls = 0
    def validator(*args):
        nonlocal calls
        calls += 1
        if calls == 4:  # post-model check passed, next check must catch revocation
            env.knowledge.set_enabled(env.document['id'], False)
        return env.validator(*args)
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationService
    s = KnowledgeTranslationService(env.cache, ModelSettings(name='test',base_url='https://example.invalid',api_key='secret'),
        source_validator=validator, transport=httpx.MockTransport(provider_response))
    with pytest.raises(KnowledgeChatValidationError):
        await s.translate(env.cid, env.mid, 'S1', 'en')
    assert calls == 4 and cache_count(env) == 0

@pytest.mark.asyncio
async def test_cancellation_during_final_authorization_never_commits_cache(env):
    import threading
    entered, release = threading.Event(), threading.Event()
    checks = 0
    def validator(*args):
        nonlocal checks
        checks += 1
        citation = env.validator(*args)
        if checks == 4:
            entered.set()
            assert release.wait(3)
        return citation
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationService
    s = KnowledgeTranslationService(env.cache, ModelSettings(name='test',base_url='https://example.invalid',api_key='test-secret'),
        source_validator=validator, transport=httpx.MockTransport(provider_response))
    task = asyncio.create_task(s.translate(env.cid, env.mid, 'S1', 'en'))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    finally:
        release.set()
    await asyncio.sleep(.05)
    assert cache_count(env) == 0

@pytest.mark.parametrize('body', [
    r'The L1 and L2 phases use x2 + y2 with sin(α) at 1.2e-3 m/s (12).',
    '矩阵为 A−B，误差为 σ²≤0.03，采用 H2O 和 N₁（式 4）。',
    r'The value uses \\(x_i^2 + y_j\\), \\[A=\\frac{b}{2}\\], and `v=3.0`.',
])
def test_protection_rejects_changed_bare_mathematical_content(body):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    masked, replacements = _protect(body)
    assert _restore(masked, replacements) == body
    # A provider must never be able to change an operator or digit while
    # faithfully echoing the placeholders it received.
    assert not any(character.isdigit() for character in __import__('re').sub(r'KT_(?:[a-f0-9]{4}_)?[0-9]+','',masked))
    assert '+' not in masked and '−' not in masked and '≤' not in masked

@pytest.mark.asyncio
async def test_unexpected_tool_role_is_not_accepted_as_translation(env):
    def handler(request):
        data = provider_response(request).json()
        data['choices'][0]['message']['role'] = 'tool'
        return httpx.Response(200, json=data)
    from great_agent.services.knowledge_translation_service import KnowledgeTranslationError
    with pytest.raises(KnowledgeTranslationError):
        await service(env, handler).translate(env.cid, env.mid, 'S1', 'en')
    assert cache_count(env) == 0


def test_many_scientific_placeholders_have_small_wire_expansion():
    from great_agent.services.knowledge_translation_service import _protect, _restore
    body = ' '.join(f'The residual for x{i} is stable.' for i in range(72))
    protected, replacements = _protect(body)
    assert len(replacements) == 72
    assert max(map(len, replacements)) <= 6
    assert len(protected) <= len(body) + 300
    assert _restore(protected, replacements) == body


def test_source_literal_markers_are_not_mistaken_for_generated_placeholders(monkeypatch):
    from great_agent.services import knowledge_translation_service as module
    nonces = iter(['abcd' + '0'*28, 'beef' + '0'*28])
    monkeypatch.setattr(module, 'uuid4', lambda: SimpleNamespace(hex=next(nonces)))
    body = 'Literal KT_0 and KT_abcd_0 must survive; the value x2 is 3.5.'
    protected, replacements = module._protect(body)
    assert all(key.startswith('KT_beef_') for key in replacements)
    assert module._restore(protected, replacements) == body
    # Unknown and repeated tokens remain invalid with compact IDs.
    first, second, *_ = replacements
    for changed in (protected + ' KT_beef_999', protected + ' ' + first):
        with pytest.raises(ValueError): module._restore(changed, replacements)

@pytest.mark.parametrize('fallback', [False, True])
@pytest.mark.parametrize('attachment', ['suffix_letters','prefix_letters','suffix_underscore','prefix_underscore'])
def test_compact_marker_boundaries_reject_ascii_attachment(fallback, attachment):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    body = 'Value x2 is stable.' + (' Literal KT_9.' if fallback else '')
    protected, replacements = _protect(body)
    marker = next(iter(replacements))
    additions = {'suffix_letters': marker + 'abc', 'prefix_letters':'abc' + marker,
                 'suffix_underscore':marker + '_abc', 'prefix_underscore':'abc_' + marker}
    with pytest.raises(ValueError):
        _restore(protected.replace(marker, additions[attachment], 1), replacements)


@pytest.mark.parametrize('fallback', [False, True])
def test_compact_markers_allow_chinese_and_other_markers_adjacent(fallback):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    body = '数值N₁与x2稳定。' + ('标记KT_9。' if fallback else '')
    protected, replacements = _protect(body)
    assert _restore(protected, replacements) == body
    first = next(iter(replacements))
    assert _restore('中文' + first + '中文' + ''.join(list(replacements)[1:]), replacements).startswith('中文' + replacements[first] + '中文')

@pytest.mark.parametrize('body', ['The inter-satellite ambiguity-fixed solution is stable.',
                                 'The user-side ambiguity is round-\nestimated.',
                                 'The variables x-y are preserved.'])
def test_marker_boundaries_preserve_original_hyphenated_terms_and_math(body):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    protected, replacements = _protect(body)
    assert _restore(protected, replacements) == body

@pytest.mark.parametrize('body', ['The 2nd residual is stable.', 'The delay is 3.5ns.',
                                 'Use $x$th order.', 'The surface is m².', 'Use δrb and rbδ.'])
def test_marker_boundaries_preserve_original_ascii_math_adjacency(body):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    protected, replacements = _protect(body)
    assert _restore(protected, replacements) == body


def test_translation_allows_grammatical_date_clause_reordering_with_values_intact():
    from great_agent.services.knowledge_translation_service import _protect, _restore
    protected, replacements = _protect('Corrections for BDS3 and GPS from DOY 092 are used.')
    by_value = {value:key for key,value in replacements.items()}
    translated = f"使用{by_value['DOY']} {by_value['092']}的{by_value['BDS3']}和{by_value['GPS']}改正。"
    assert _restore(translated, replacements) == '使用DOY 092的BDS3和GPS改正。'


def test_reordered_math_and_code_blocks_keep_internal_bytes_exact():
    from great_agent.services.knowledge_translation_service import _protect, _restore
    original = r'Math $x+y=3$ and code `value = x + 2` remain intact.'
    _, replacements = _protect(original)
    math_marker, code_marker = replacements
    assert _restore(code_marker + '；' + math_marker, replacements) == r'`value = x + 2`；$x+y=3$'
    with pytest.raises(ValueError):
        _restore(code_marker + '；$y+x=3$', replacements)

@pytest.mark.parametrize('expression', ['10 / 2', 'x + y', 'x_i + y_j', 'α − β'])
def test_bare_arithmetic_remains_one_protected_block_when_prose_reorders(expression):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    _, replacements = _protect('Use ' + expression + ' for correction.')
    assert list(replacements.values()) == [expression]
    marker = next(iter(replacements))
    assert _restore('改正采用' + marker + '。', replacements) == '改正采用' + expression + '。'

@pytest.mark.parametrize('expression', ['092 to 098', '3 to5', '092 to098', '3.5–7.2', '093—157', '093-157'])
def test_numeric_ranges_remain_atomic_when_date_clauses_move(expression):
    from great_agent.services.knowledge_translation_service import _protect, _restore
    _, replacements = _protect('Measurements from ' + expression + ' are used.')
    assert list(replacements.values()) == [expression]
    marker = next(iter(replacements))
    assert _restore('采用' + marker + '期间的测量值。', replacements) == '采用' + expression + '期间的测量值。'
