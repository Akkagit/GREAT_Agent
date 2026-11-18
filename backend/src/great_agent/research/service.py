"""Research orchestration; HTTP acceptance and worker execution have separate lives."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import re
from uuid import uuid4

from sqlalchemy.exc import DBAPIError

from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout
from great_agent.research.engine import MODES, ResearchEngine, model_fingerprint
from great_agent.research.errors import ResearchError
from great_agent.research.tools import ResearchToolset

logger = logging.getLogger(__name__)


class ResearchService:
    def __init__(self, repository, materials, settings, *, engine=None):
        self.repository, self.materials, self.settings = repository, materials, settings
        override = settings.research.model
        self.model_settings = override or settings.model.model_copy(update={
            'max_tokens': max(settings.model.max_tokens, 8192),
            'thinking': 'enabled' if settings.model.name.lower().startswith('glm') else settings.model.thinking})
        self.engine = engine or ResearchEngine(self.model_settings)

    def model_profiles(self):
        return [{'id': 'default', 'name': self.model_settings.name,
                 'configured': bool(self.model_settings.api_key.get_secret_value().strip()),
                 'capabilities': ['tool_calling', 'citations', 'multi_turn_research', 'read_only'],
                 'modes': list(MODES)}]

    def _profile(self, profile):
        if profile != 'default':
            raise ResearchError('invalid_model_profile', '请选择已配置的科研模型。', 422)

    def catalog(self):
        result = self.materials.catalog()
        result['profiles'] = self.model_profiles()
        return result

    def create_session(self, *, title='', goal='', materials=None, model_profile_id='default', mode='deep'):
        self._profile(model_profile_id)
        snapshot = self.materials.capture(materials or [])
        return self.repository.create_session(title=title.strip() or goal.strip()[:40] or '新研究课题',
            goal=goal, materials=snapshot.get('selection', materials or []), snapshot=snapshot,
            model_profile_id=model_profile_id, mode=mode)

    def get_session(self, session_id):
        session = self.repository.get_session(session_id)
        session['reports'] = [self._readable_report(report) for report in session.get('reports', [])]
        run_ids = dict.fromkeys(message['run_id'] for message in session.get('messages', []))
        session['runs'] = [self.repository.get_run(run_id) for run_id in run_ids]
        unavailable = self._unavailable_runs(run_ids)
        for message in session.get('messages', []):
            if message['run_id'] in unavailable:
                message.update(text='该轮材料当前不可访问，历史内容暂不展示。', blocks=[])
        for run in session['runs']:
            if run['id'] in unavailable:
                run.update(result=None, unavailable_reason='材料当前不可访问')
        session['items'] = [item for item in session.get('items', [])
                            if not self._item_runs(item).intersection(unavailable)]
        return session

    def get_run(self, run_id):
        run = self.repository.get_run(run_id)
        if self._unavailable_runs([run_id]):
            run.update(result=None, unavailable_reason='材料当前不可访问')
        return run

    @staticmethod
    def _item_runs(item):
        dependencies = {ref['run_id'] for ref in item.get('support_refs', []) + item.get('opposition_refs', [])}
        if item.get('origin_run_id'):
            dependencies.add(item['origin_run_id'])
        return dependencies

    def _unavailable_runs(self, run_ids):
        unavailable = set()
        for run_id in run_ids:
            try:
                self.materials.validate(self.repository.get_run_snapshot(run_id))
            except (ValueError, ResearchError):
                unavailable.add(run_id)
        return unavailable

    def update_materials(self, session_id, *, expected_version, materials):
        current = self.get_session(session_id)
        if current['version'] != expected_version:
            raise ResearchError('version_conflict', '课题材料已变化，请刷新后再提交。')
        snapshot = self.materials.capture(materials)
        self.repository.update_materials(session_id, expected_version=expected_version,
            materials=snapshot.get('selection', materials), snapshot=snapshot)
        return self.get_session(session_id)

    def update_settings(self, session_id, *, expected_version, model_profile_id, mode):
        self._profile(model_profile_id)
        self.repository.update_settings(session_id, expected_version=expected_version,
            model_profile_id=model_profile_id, mode=mode)
        return self.get_session(session_id)

    def submit(self, session_id, *, request_id, expected_version, message, followup_target=None):
        if not message.strip() or len(message) > 8000:
            raise ResearchError('invalid_request', '请输入 1–8000 字的研究问题。', 422)
        # Server-generated budgets must not change the identity of an already
        # accepted browser request when next-turn settings have since changed.
        existing = self.repository.find_request(session_id, request_id)
        if existing:
            budget = existing['budget']
        else:
            current = self.get_session(session_id)
            self._profile(current['settings']['model_profile_id'])
            if not self.model_profiles()[0]['configured']:
                raise ResearchError('model_not_configured', '服务端尚未配置科研分析模型密钥。', 503)
            budget = {**MODES[current['settings']['mode']], 'model_fingerprint': model_fingerprint(self.model_settings),
                      'model_config': self.model_settings.model_dump(mode='json', exclude={'api_key'})}
        return self.repository.create_run(session_id, request_id=request_id, expected_version=expected_version,
            message=message, followup_target=followup_target, budget=budget)

    def cancel(self, run_id):
        return self.repository.cancel(run_id)

    def retry(self, run_id, *, request_id):
        return self.repository.retry_run(run_id, request_id=request_id)

    def evidence(self, run_id, alias):
        evidence = self.repository.get_evidence(run_id, alias)
        try:
            self.materials.validate(self.repository.get_run_snapshot(run_id))
        except (ValueError, ResearchError):
            return {key: evidence[key] for key in ('id', 'alias', 'kind', 'title') if key in evidence} | {
                'available': False, 'text': '', 'unavailable_reason': '材料已不可访问，不能显示缓存内容。'}
        return evidence

    def create_report(self, session_id, *, title='', content='', run_ids=None):
        # Report contents are completed response/evidence snapshots, never a new
        # paid summarization request or an experiment modification.
        if not content.strip():
            session = self.repository.get_session(session_id)
            selected = run_ids if run_ids is not None else list(dict.fromkeys(
                message['run_id'] for message in session['messages'] if message['role'] == 'assistant'))
            sections = [f'# {title or session["title"]}', session['goal']]
            for index, run_id in enumerate(selected, 1):
                run = self.repository.get_run(run_id)
                if run['session_id'] != session_id or run['status'] != 'completed':
                    raise ResearchError('invalid_report', '报告只能包含本课题已完成的分析轮次。', 422)
                self.materials.validate(self.repository.get_run_snapshot(run_id))
                answer = run.get('result') or {}
                sections.extend([f'## 第 {index} 轮 · {run_id}', f'问题：{run["message"]}',
                    f'回答范围：{answer.get("answer_status", "partial")}', answer.get('answer', '')])
                for limitation in answer.get('limitations', []):
                    sections.append(f'- 限制：{limitation}')
                for suggestion in answer.get('suggestions', []):
                    sections.append(f'建议：{suggestion["text"]}')
                    for field, label in (('target', '位置'), ('rationale', '依据'), ('change', '改法'),
                                         ('impact', '影响'), ('validation', '人工验证')):
                        if suggestion.get(field):
                            sections.append(f'{label}：{suggestion[field]}')
                aliases = answer.get('source_ids', [])
                if aliases:
                    sections.append('本轮证据：' + '、'.join(f'[{alias}]' for alias in aliases))
            if not selected:
                raise ResearchError('invalid_report', '请先完成至少一轮分析，再保存报告。', 422)
            content = '\n\n'.join(section for section in sections if section)
        report = self.repository.create_report(session_id, title=title, content=content, run_ids=run_ids)
        return self._readable_report(report)

    def reports(self, session_id):
        return [self._readable_report(report) for report in self.repository.list_reports(session_id)]

    def _readable_report(self, report):
        result = deepcopy(report)
        dependencies = {run['id'] for run in result.get('snapshot', {}).get('runs', [])}
        for item in result.get('snapshot', {}).get('items', []):
            dependencies.update(self._item_runs(item))
        unavailable = self._unavailable_runs(dependencies)
        for run in result.get('snapshot', {}).get('runs', []):
            if run['id'] in unavailable:
                run['result'] = None
                run['unavailable_reason'] = '材料已不可访问'
                result['content'] = '部分材料当前不可访问；报告正文与不可用证据已隐藏。'
                for evidence in result.get('snapshot', {}).get('evidence', []):
                    if evidence.get('run_id') == run['id']:
                        evidence.update(text='', available=False)
        if unavailable:
            result['content'] = '部分材料当前不可访问；报告正文与不可用证据已隐藏。'
            result.get('snapshot', {})['items'] = []
        return result

    def _authorized_context(self, context):
        """Historical records stay immutable; revoked source text is not resent."""
        context = deepcopy(context)
        run_ids = {message['run_id'] for message in context.get('messages', []) if message.get('run_id')}
        followup = context.get('followup') or {}
        if followup.get('run_id'):
            run_ids.add(followup['run_id'])
        for item in context.get('items', []):
            if item.get('origin_run_id'):
                run_ids.add(item['origin_run_id'])
            for reference in item.get('support_refs', []) + item.get('opposition_refs', []):
                run_ids.add(reference['run_id'])
        unavailable = set()
        for run_id in run_ids:
            try:
                self.materials.validate(self.repository.get_run_snapshot(run_id))
            except (ValueError, ResearchError):
                unavailable.add(run_id)
        context['messages'] = [message for message in context.get('messages', []) if message.get('run_id') not in unavailable]
        safe_items = []
        for item in context.get('items', []):
            dependencies = {ref['run_id'] for ref in item.get('support_refs', []) + item.get('opposition_refs', [])}
            if item.get('kind') not in ('user_statement', 'constraint'):
                dependencies.add(item.get('origin_run_id'))
            if not dependencies.intersection(unavailable):
                safe_items.append(item)
        context['items'] = safe_items
        if followup.get('run_id') in unavailable:
            context['followup'] = {'run_id': followup['run_id'], 'unavailable_reason': '原材料已不可访问，本轮不发送其历史内容。'}
        if unavailable:
            context.setdefault('limitations', []).append('部分历史材料已不可访问，相关消息与研究事项未发送给模型。')
        return context

    def _prior_evidence(self, context):
        run_ids = dict.fromkeys(message['run_id'] for message in context.get('messages', []) if message.get('run_id'))
        target = context.get('followup_target') or {}
        if target.get('run_id'):
            run_ids[target['run_id']] = None
        evidence = {}
        for run_id in run_ids:
            for item in self.repository.list_evidence(run_id):
                evidence[item['id']] = item
        for item in context.get('items', []):
            for evidence_id in item.get('support', []) + item.get('opposition', []):
                if evidence_id not in evidence:
                    try:
                        evidence[evidence_id] = self.repository.get_evidence_by_id(evidence_id)
                    except ResearchError as error:
                        if error.code != 'not_found':
                            raise
        return list(evidence.values())

    @staticmethod
    def _context_for_question(context, question, summary, prior):
        context = deepcopy(context)
        context['current_question'] = question
        context['material_summary'] = summary
        context['prior_evidence'] = [{k: item[k] for k in ('id', 'kind', 'title', 'locator') if k in item}
                                     for item in prior][-80:]
        items = context.get('items', [])
        target = context.get('followup_target') or {}
        selected = target.get('item_id')
        terms = set(re.findall(r'[A-Za-z0-9_]+|[\u4e00-\u9fff]{2}', question.casefold()))

        def rank(item):
            text = str(item.get('text', '')).casefold()
            return (item.get('id') == selected, item.get('kind') in ('constraint', 'user_statement'),
                    item.get('status') == 'challenged', sum(term in text for term in terms))

        context['items'] = sorted(items, key=rank, reverse=True)[:24]
        context.setdefault('limitations', [])
        if len(items) > len(context['items']):
            context['limitations'].append(f'本轮选入 {len(context["items"])} 个相关研究事项，其他 {len(items)-len(context["items"])} 项仍保存在课题历史。')
        return context

    @staticmethod
    def _state_changes(result, context, evidence, run_id):
        previous = {item['id']: item for item in context.get('items', [])}
        by_text = {(item.get('kind'), item.get('text')): item for item in previous.values()}
        by_alias = {item['alias']: item for item in evidence}
        changes = []
        for field, kind in (('observations', 'observation'), ('hypotheses', 'hypothesis'),
                            ('suggestions', 'suggestion'), ('proposed_state_changes', None)):
            for value in result.get(field, []):
                value = dict(value)
                old = previous.get(value.get('id')) or by_text.get((kind, value['text']))
                item_kind = kind or (old or {}).get('kind', 'hypothesis')
                item = {**(old or {}), **value, 'kind': item_kind,
                        'id': old['id'] if old else str(uuid4()), 'origin_run_id': (old or {}).get('origin_run_id', run_id)}
                for side in ('support', 'opposition'):
                    new_refs = [{'run_id': run_id, 'alias': alias, 'evidence_id': by_alias[alias]['id']}
                                for alias in value.get(side, [])]
                    # Preserve explicit counterevidence/history even when a later
                    # model omits it from its summary of the updated hypothesis.
                    old_ids = list((old or {}).get(side, []))
                    item[side] = list(dict.fromkeys(old_ids + [ref['evidence_id'] for ref in new_refs]))
                    refs = (old or {}).get(side + '_refs', []) + new_refs
                    item[side + '_refs'] = list({(ref['run_id'], ref['alias']): ref for ref in refs}.values())
                item['unknowns'] = list(dict.fromkeys((old or {}).get('unknowns', []) + value.get('unknowns', [])))
                changes.append(item)
        for text in result.get('open_questions', []):
            old = by_text.get(('question', text))
            changes.append(old or {'id': str(uuid4()), 'kind': 'question', 'text': text, 'status': 'proposed',
                                  'support': [], 'opposition': [], 'origin_run_id': run_id})
        for value in result.get('user_statements', []):
            old = next((item for item in previous.values() if item.get('kind') == 'user_statement'
                        and item.get('quote') == value['quote']), None)
            if old:
                continue
            changes.append({'id': str(uuid4()), 'kind': 'user_statement', 'text': value['text'],
                'quote': value['quote'], 'source_message_id': value['source_message_id'],
                'source_run_id': run_id, 'status': 'user_provided', 'support': [], 'opposition': [],
                'origin_run_id': run_id})
        return changes

    async def _persist(self, function, *args, **kwargs):
        for attempt in range(4):
            try:
                return await asyncio.to_thread(function, *args, **kwargs)
            except DBAPIError:
                if attempt == 3:
                    raise
                await asyncio.sleep(.05 * (attempt + 1))

    async def execute(self, run):
        """Execute a claimed run. The caller maintains its lease and cancellation."""
        args = (run['id'], run['owner_id'], run['fencing_token'])
        toolset = None
        try:
            snapshot = run['snapshot']
            await asyncio.to_thread(self.materials.validate, snapshot)
            budget = run.get('budget') or {}
            if budget.get('model_fingerprint') and budget['model_fingerprint'] != model_fingerprint(self.model_settings):
                raise ResearchError('model_profile_changed', '模型配置已变化；请基于当前配置发起新一轮。')
            context = await asyncio.to_thread(self._authorized_context, run['context'])
            authorized_base = deepcopy(context)
            prior = await asyncio.to_thread(self._prior_evidence, context)
            toolset = ResearchToolset(self.materials, snapshot, prior_evidence=prior)
            context = self._context_for_question(context, run['message'], self.materials.summary(snapshot), prior)
            previous_calls = await asyncio.to_thread(self.repository.list_model_calls, run['id'])
            if any(call['status'] != 'completed' for call in previous_calls):
                raise ResearchError('uncertain_model_call', '前次模型请求结果未知，请明确重试。')

            async def store_evidence():
                if toolset.evidence:
                    await self._persist(self.repository.add_evidence, *args, toolset.evidence)

            async def progress(stage):
                await store_evidence()
                await self._persist(self.repository.set_stage, *args, stage)

            async def before(messages, metrics):
                await asyncio.to_thread(self.materials.validate, snapshot)
                if await asyncio.to_thread(self._authorized_context, run['context']) != authorized_base:
                    raise ResearchError('history_access_changed', '历史材料权限已变化；已停止后续外发，请重新确认研究范围。', 403)
                await store_evidence()
                call = await self._persist(self.repository.start_model_call, *args,
                    request={'messages': messages, 'metrics': metrics}, evidence_ids=[e['id'] for e in toolset.evidence],
                    model_fingerprint=model_fingerprint(self.model_settings), call_id=str(uuid4()))
                return call['id']

            async def after(call_id, response, usage):
                await self._persist(self.repository.record_model_response, *args,
                    call_id=call_id, response=response, usage=usage)

            async def tool_result(name, arguments, result, index):
                key = f'tool:{index}'
                saved = await asyncio.to_thread(self.repository.get_checkpoint, run['id'], key)
                if saved:
                    payload = saved['payload']
                    if payload['name'] != name or payload['arguments'] != arguments:
                        raise ResearchError('checkpoint_conflict', '恢复的工具步骤与原轮次不一致。')
                    return payload['result']
                await self._persist(self.repository.save_checkpoint, *args, key=key,
                                    payload={'name': name, 'arguments': arguments, 'result': result})
                return result

            checkpoint = await asyncio.to_thread(self.repository.get_checkpoint, run['id'], 'validated-result')
            if checkpoint:
                saved = checkpoint['payload']
                result, changes = saved['result'], saved['items']
            else:
                result = await self.engine.reply(run['message'], context=context, toolset=toolset,
                    mode=run['mode'], budget=budget, on_request=before, on_response=after, on_progress=progress,
                    replay_calls=previous_calls, on_tool_result=tool_result)
                await store_evidence()
                # The model engine already validates; injected engines must honor
                # exactly the same final-result validation boundary.
                checked = ResearchEngine.validate({key: value for key, value in result.items() if key != 'metrics'}, toolset.evidence, context)
                result = {**checked, 'metrics': result.get('metrics', {})}
                changes = self._state_changes(result, context, toolset.evidence, run['id'])
                await self._persist(self.repository.save_checkpoint, *args, key='validated-result',
                                    payload={'result': result, 'items': changes})
            await self._persist(self.repository.finish, *args, result=result, items=changes, usage=result.get('metrics', {}))
        except asyncio.CancelledError:
            try:
                await self._persist(self.repository.fail, *args, error_code='worker_interrupted',
                                    message='研究执行器已停止，可明确重试。', status='interrupted')
            except ResearchError:
                pass
            raise
        except Exception as error:
            if isinstance(error, ModelTimeout):
                code, message, status = 'model_timeout', '科研分析达到时限，可缩小问题或明确重试。', 'timed_out'
            elif isinstance(error, ResearchError) or hasattr(error, 'code'):
                code, message, status = error.code, str(error), 'failed'
            elif isinstance(error, ModelProviderError):
                code, message, status = 'research_model_error', '模型请求、预算或回答校验未通过；已保留问题和取证记录。', 'failed'
            else:
                code, message, status = 'research_failed', '科研分析未完成；已保留问题，可根据请求编号查看诊断。', 'failed'
            logger.warning('research_run_failed run_id=%s code=%s error_type=%s', run['id'], code, type(error).__name__)
            try:
                if toolset is not None and toolset.evidence:
                    await self._persist(self.repository.add_evidence, *args, toolset.evidence)
                await self._persist(self.repository.fail, *args, error_code=code, message=message, status=status)
            except ResearchError:
                # Explicit cancellation or a newer fenced owner already won.
                pass
