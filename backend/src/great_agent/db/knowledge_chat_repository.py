"""Transactional knowledge turns. Evidence is immutable data, never SDK state."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from uuid import uuid4

from sqlalchemy import select, text

from great_agent.db.conversation_models import (
    ConversationModel as Conversation, ConversationRunModel as Run,
    ConversationMessageModel as Message, ConversationEventModel as Event,
)
from great_agent.db.conversation_repository import ConversationRepository, ConversationConflict
from great_agent.db.knowledge_chat_models import (
    KnowledgeConversationBindingModel as Binding, KnowledgeRetrievalTraceModel as Trace,
    KnowledgeMessageCitationModel as Citation,
)
from great_agent.db.knowledge_models import (
    KnowledgeBaseModel as Library, KnowledgeBlobModel as Blob,
    KnowledgeDocumentModel as Document, KnowledgeVersionModel as Version,
    KnowledgeChunkModel as Chunk,
)
from great_agent.db.knowledge_repository import _lexical_terms, _serialize


class KnowledgeChatNotFound(Exception):
    pass


class KnowledgeChatConflict(ConversationConflict):
    pass


class KnowledgeChatValidationError(ValueError):
    pass


class KnowledgeChatScopeChanged(KnowledgeChatValidationError):
    pass


_PUBLISHED = ('ready', 'completed_with_warnings')


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise KnowledgeChatValidationError('invalid JSON data') from error


def _string(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or '\0' in value or len(value) > maximum:
        raise KnowledgeChatValidationError(f'invalid {label}')
    return value


class KnowledgeChatRepository:
    def __init__(self, sessions):
        self.sessions = sessions
        self.conversations = ConversationRepository(sessions)

    @staticmethod
    def _begin(session):
        session.execute(text('BEGIN IMMEDIATE'))

    @staticmethod
    def _require(session, model, identifier):
        row = session.get(model, str(identifier))
        if row is None:
            raise KnowledgeChatNotFound('knowledge source or conversation not found')
        return row

    def _conversation(self, session, conversation_id):
        conversation = self._require(session, Conversation, conversation_id)
        binding = session.get(Binding, conversation.id)
        if binding is None or conversation.scope != f'knowledge:{conversation.id}':
            raise KnowledgeChatNotFound('unknown knowledge conversation')
        return conversation, binding

    @staticmethod
    def _binding(binding):
        return {'base_id': binding.base_id, 'scope_mode': binding.scope_mode,
                'document_ids': json.loads(binding.document_ids_json),
                'model_profile_id': binding.model_profile_id, 'binding_revision': binding.binding_revision}

    def _validate_binding(self, session, base_id, scope_mode, document_ids, model_profile_id):
        self._require(session, Library, base_id)
        _string(model_profile_id, 'model profile', 100)
        if scope_mode not in ('selected', 'all_enabled'):
            raise KnowledgeChatValidationError('invalid scope mode')
        if (not isinstance(document_ids, list) or len(document_ids) > 200
                or any(not isinstance(identifier, str) or not identifier for identifier in document_ids)):
            raise KnowledgeChatValidationError('invalid document IDs')
        document_ids = list(dict.fromkeys(document_ids))
        if scope_mode == 'selected' and not document_ids:
            raise KnowledgeChatValidationError('select at least one document')
        for identifier in document_ids:
            document = session.get(Document, identifier)
            if document is None or document.base_id != str(base_id) or not document.enabled:
                raise KnowledgeChatValidationError('document outside available library scope')
        return document_ids if scope_mode == 'selected' else []

    def create_conversation(self, *, base_id, document_ids, scope_mode='selected', model_profile_id='default', title=None):
        if title is not None:
            _string(title, 'title', 200)
        with self.sessions.begin() as session:
            self._begin(session)
            documents = self._validate_binding(session, base_id, scope_mode, document_ids, model_profile_id)
            identifier = str(uuid4())
            conversation = Conversation(id=identifier, scope=f'knowledge:{identifier}',
                                        title=title or '文献对话', state_json='{}')
            session.add(conversation)
            session.flush()
            binding = Binding(conversation_id=identifier, base_id=str(base_id), scope_mode=scope_mode,
                              document_ids_json=_json(documents), model_profile_id=model_profile_id, binding_revision=1)
            session.add(binding)
            session.flush()
            return self._detail(session, conversation, binding)

    def list_conversations(self, base_id=None):
        with self.sessions() as session:
            query = select(Conversation, Binding).join(Binding, Binding.conversation_id == Conversation.id)
            if base_id is not None:
                query = query.where(Binding.base_id == str(base_id))
            return [self._detail(session, conversation, binding) for conversation, binding in
                    session.execute(query.order_by(Conversation.id))]

    def _detail(self, session, conversation, binding):
        rows = session.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.sequence))
        messages = []
        latest_completed_run_id = None
        formula_sources=set()
        for row in rows:
            if row.role == 'assistant' and row.status == 'completed':
                latest_completed_run_id = row.run_id
            message = self.conversations.serialize(row)
            for block in message['blocks']:
                if block.get('type') == 'citation':
                    citation = session.scalar(select(Citation).where(Citation.message_id == row.id,
                                                                    Citation.source_id == block.get('source_id')))
                    if citation is not None:
                        block.pop('formula_transcriptions',None)
                        block.update(self._citation(session, conversation, citation,formula_sources=formula_sources))
            messages.append(message)
        trace = session.get(Trace, conversation.active_run_id) if conversation.active_run_id else None
        mode_trace = trace or (session.get(Trace, latest_completed_run_id) if latest_completed_run_id else None)
        return {'id': conversation.id, 'title': conversation.title, 'binding': self._binding(binding),
                'messages': messages, 'active_run_id': conversation.active_run_id,
                'stage': trace.stage if trace else None,
                'retrieval_mode': mode_trace.mode if mode_trace else None,
                'retrieval_summary': self._trace_summary(session, mode_trace) if mode_trace else None}

    def _trace_summary(self, session, trace):
        snapshot=json.loads(trace.snapshot_json)
        library=session.get(Library,snapshot.get('base_id'))
        summary={'base_id':snapshot.get('base_id'),
            'base_name':snapshot.get('base_name') or (library.name if library else '文献库'),
            'scope_mode':snapshot.get('scope_mode'),'document_count':len(snapshot.get('document_versions',[])),
            'mode':trace.mode,'available':True}
        try:
            self._revalidate(session,snapshot)
        except (KnowledgeChatValidationError,KnowledgeChatNotFound):
            return {**summary,'available':False,'queries':[],'documents':[],'hit_count':0,'evidence_count':0}
        evidence=json.loads(trace.evidence_json)
        candidates=json.loads(trace.candidates_json)
        source_ids={item.get('document_id') for item in evidence+candidates}
        hits={item.get('chunk_id') or item.get('parent_chunk_id') for item in candidates+evidence}
        return {**summary,'queries':json.loads(trace.queries_json),
            'hit_count':len(hits-{None}),'evidence_count':len(evidence),
            'documents':[{'document_id':source['document_id'],'title':source['title'],'filename':source['filename']}
                         for source in snapshot['document_versions'] if source['document_id'] in source_ids]}

    def get_retrieval_summary(self, run_id):
        with self.sessions() as session:
            trace=self._require(session,Trace,run_id)
            return self._trace_summary(session,trace)

    def get_conversation(self, conversation_id):
        with self.sessions.begin() as session:
            self._begin(session)
            conversation, binding = self._conversation(session, conversation_id)
            self.conversations.recover(session, conversation)
            session.flush()
            return self._detail(session, conversation, binding)

    def update_binding(self, conversation_id, *, expected_revision, scope_mode, document_ids, model_profile_id):
        with self.sessions.begin() as session:
            self._begin(session)
            conversation, binding = self._conversation(session, conversation_id)
            if isinstance(expected_revision, bool) or binding.binding_revision != expected_revision:
                raise KnowledgeChatConflict('knowledge binding revision changed')
            documents = self._validate_binding(session, binding.base_id, scope_mode, document_ids, model_profile_id)
            binding.scope_mode = scope_mode
            binding.document_ids_json = _json(documents)
            binding.model_profile_id = model_profile_id
            binding.binding_revision += 1
            session.flush()
            return self._detail(session, conversation, binding)

    def _snapshot(self, session, conversation, binding):
        query = select(Document).where(Document.base_id == binding.base_id, Document.enabled.is_(True))
        if binding.scope_mode == 'selected':
            ids = json.loads(binding.document_ids_json)
            if not ids:
                raise KnowledgeChatValidationError('select at least one document')
            query = query.where(Document.id.in_(ids))
        versions = []
        for document in session.scalars(query.order_by(Document.id)):
            version = session.get(Version, document.active_version_id) if document.active_version_id else None
            if version is None or version.status not in _PUBLISHED or version.document_id != document.id:
                continue
            blob = self._require(session, Blob, document.blob_id)
            versions.append({'document_id': document.id, 'version_id': version.id, 'filename': document.filename,
                             'title': json.loads(version.metadata_json).get('title') or document.filename,
                             'sha256': blob.sha256})
        if binding.scope_mode == 'selected' and len(versions) != len(json.loads(binding.document_ids_json)):
            raise KnowledgeChatScopeChanged('selected source is unavailable or not parsed')
        return {'conversation_id': conversation.id, 'base_id': binding.base_id,
                'base_name':self._require(session,Library,binding.base_id).name,
                'binding_revision': binding.binding_revision, 'scope_mode': binding.scope_mode,
                'model_profile_id': binding.model_profile_id, 'document_versions': versions}

    def claim_turn(self, conversation_id, message, request_id, lease_seconds):
        _string(message, 'message', 2000)
        _string(request_id, 'request ID', 128)
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise KnowledgeChatValidationError('invalid lease')
        with self.sessions.begin() as session:
            self._begin(session)
            conversation, binding = self._conversation(session, conversation_id)
            self.conversations.recover(session, conversation)
            run = session.scalar(select(Run).where(Run.conversation_id == conversation.id, Run.request_id == request_id))
            if run is not None:
                if run.input_text != message:
                    raise KnowledgeChatConflict('request ID already used with different content')
                trace = session.get(Trace, run.id)
                snapshot = json.loads(trace.snapshot_json) if trace else {}
                if run.status == 'completed':
                    response=json.loads(run.response_json)
                    response['retrieval_summary']=self._trace_summary(session,trace) if trace else {
                        'available':False,'queries':[],'documents':[],'hit_count':0,'evidence_count':0}
                    formula_sources=set()
                    for block in response.get('assistant_message',{}).get('blocks',[]):
                        if block.get('type') != 'citation' or 'formula_transcriptions' not in block:
                            continue
                        block.pop('formula_transcriptions',None)
                        citation=session.scalar(select(Citation).where(Citation.run_id==run.id,
                            Citation.source_id==block.get('source_id')))
                        if citation is not None:
                            refreshed=self._citation(session,conversation,citation,formula_sources=formula_sources)
                            if 'formula_transcriptions' in refreshed:
                                block['formula_transcriptions']=refreshed['formula_transcriptions']
                            if not refreshed.get('available'):
                                block.update(available=False,unavailable_reason=refreshed['unavailable_reason'])
                    return run, response, snapshot
                if run.status == 'failed':
                    return run, None, snapshot
                raise KnowledgeChatConflict('request active or interrupted; use a new request ID')
            if conversation.active_run_id:
                raise KnowledgeChatConflict('conversation already has an active turn')
            snapshot = self._snapshot(session, conversation, binding)
            run = Run(id=str(uuid4()), conversation_id=conversation.id, request_id=request_id,
                      input_text=message, status='started', lease_until=time.time() + lease_seconds, owner_pid=os.getpid())
            session.add(run)
            session.flush()
            conversation.active_run_id = run.id
            self.conversations.message(session, run, 'user', 'completed', [{'type': 'text', 'text': message}])
            session.add(Trace(run_id=run.id, snapshot_json=_json(snapshot)))
            self._event(session, run, 'started')
            return run, None, snapshot

    def _owned(self, session, run_id):
        run = self._require(session, Run, run_id)
        conversation, _ = self._conversation(session, run.conversation_id)
        if (run.status != 'started' or conversation.active_run_id != run.id
                or run.lease_until <= time.time() or run.owner_pid != os.getpid()):
            raise KnowledgeChatConflict('knowledge turn no longer active')
        trace = self._require(session, Trace, run.id)
        return run, conversation, trace

    @staticmethod
    def _event(session, run, kind, payload=None):
        if payload is not None and not isinstance(payload, dict):
            raise KnowledgeChatValidationError('invalid event metadata')
        data = dict(payload or {})
        data.update(run_id=run.id, request_id=run.request_id, status=kind)
        session.add(Event(conversation_id=run.conversation_id, run_id=run.id, kind=kind, payload_json=_json(data)))

    def stage(self, run_id, stage, payload=None):
        if stage not in ('retrieving', 'generating', 'started'):
            raise KnowledgeChatValidationError('invalid stage')
        with self.sessions.begin() as session:
            self._begin(session)
            run, _, trace = self._owned(session, run_id)
            self._revalidate(session, json.loads(trace.snapshot_json))
            trace.stage = stage
            self._event(session, run, stage, payload)

    def _revalidate(self, session, snapshot):
        if not isinstance(snapshot, dict):
            raise KnowledgeChatScopeChanged('invalid knowledge snapshot')
        try:
            _, binding = self._conversation(session, snapshot['conversation_id'])
            if binding.base_id != snapshot['base_id']:
                raise KnowledgeChatScopeChanged('knowledge library changed')
            current_ids = set(json.loads(binding.document_ids_json)) if binding.scope_mode == 'selected' else None
            seen = set()
            for source in snapshot['document_versions']:
                identifier = source['document_id']
                if identifier in seen:
                    raise KnowledgeChatScopeChanged('duplicate snapshot source')
                seen.add(identifier)
                document = session.get(Document, identifier)
                version = session.get(Version, source['version_id'])
                if (document is None or not document.enabled or document.base_id != binding.base_id
                        or (current_ids is not None and identifier not in current_ids)
                        or version is None or version.document_id != identifier or version.status not in _PUBLISHED):
                    raise KnowledgeChatScopeChanged('knowledge source was disabled or scope was revoked')
                blob = session.get(Blob, document.blob_id)
                if blob is None or blob.sha256 != source['sha256']:
                    raise KnowledgeChatScopeChanged('knowledge source identity changed')
        except (KeyError, TypeError, KnowledgeChatNotFound) as error:
            raise KnowledgeChatScopeChanged('invalid or unavailable knowledge snapshot') from error
        return snapshot

    def revalidate_snapshot(self, snapshot):
        with self.sessions() as session:
            return self._revalidate(session, snapshot)

    def _chunk(self, session, snapshot, chunk_id):
        chunk = session.get(Chunk, str(chunk_id))
        sources = {(source['document_id'], source['version_id']): source for source in snapshot['document_versions']}
        source = sources.get((chunk.document_id, chunk.version_id)) if chunk else None
        if chunk is None or source is None:
            raise KnowledgeChatValidationError('chunk outside fixed knowledge scope')
        result = _serialize(chunk)
        result.update(chunk_id=chunk.id, base_id=snapshot['base_id'], title=source['title'],
                      filename=source['filename'], source_sha256=source['sha256'])
        return result

    def read_snapshot_chunk(self, snapshot, chunk_id):
        with self.sessions() as session:
            self._revalidate(session, snapshot)
            return self._chunk(session, snapshot, chunk_id)

    def list_snapshot_chunks(self, snapshot):
        with self.sessions() as session:
            self._revalidate(session, snapshot)
            versions = [source['version_id'] for source in snapshot['document_versions']]
            chunks = session.scalars(select(Chunk).where(Chunk.version_id.in_(versions))
                                      .order_by(Chunk.document_id, Chunk.ordinal, Chunk.id))
            return [self._chunk(session, snapshot, chunk.id) for chunk in chunks]

    def search_snapshot(self, snapshot, query, limit=20):
        if not isinstance(query, str) or '\0' in query or len(query) > 4000:
            raise KnowledgeChatValidationError('invalid query')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise KnowledgeChatValidationError('invalid result limit')
        with self.sessions() as session:
            self._revalidate(session, snapshot)
            terms = _lexical_terms(query)
            if not terms or not snapshot['document_versions']:
                return {'mode': 'lexical', 'items': []}
            parameters = {'match': ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms),
                          'limit': limit, 'base': snapshot['base_id']}
            clauses = []
            for index, source in enumerate(snapshot['document_versions']):
                parameters[f'd{index}'] = source['document_id']
                parameters[f'v{index}'] = source['version_id']
                clauses.append(f'(c.document_id=:d{index} AND c.version_id=:v{index})')
            matches = session.execute(text('''SELECT c.id, bm25(knowledge_chunks_fts) AS score
                FROM knowledge_chunks_fts JOIN knowledge_chunks c ON c.rowid=knowledge_chunks_fts.rowid
                JOIN knowledge_documents d ON d.id=c.document_id
                JOIN knowledge_versions v ON v.id=c.version_id
                WHERE knowledge_chunks_fts MATCH :match AND d.base_id=:base AND d.enabled=1
                AND v.document_id=d.id AND v.status IN ('ready','completed_with_warnings') AND ('''
                + ' OR '.join(clauses) + ') ORDER BY score, c.ordinal, c.id LIMIT :limit'), parameters)
            items = []
            for identifier, score in matches:
                item = self._chunk(session, snapshot, identifier)
                item['score'] = float(score)
                items.append(item)
            return {'mode': 'lexical', 'items': items}

    def _validate_evidence(self, session, snapshot, evidence, *, formula_sources=None):
        if not isinstance(evidence, dict):
            raise KnowledgeChatValidationError('invalid evidence')
        try:
            if re.fullmatch(r'S[1-9][0-9]*', evidence['source_id']) is None:
                raise KnowledgeChatValidationError('invalid evidence source ID')
            chunk = self._chunk(session, snapshot, evidence['chunk_id'])
            if any(evidence[key] != chunk[key] for key in ('document_id', 'version_id', 'source_sha256')):
                raise KnowledgeChatValidationError('evidence source identity mismatch')
            body = evidence['evidence_text']
            if not isinstance(body, str) or not body or hashlib.sha256(body.encode('utf-8')).hexdigest() != evidence['evidence_text_sha256']:
                raise KnowledgeChatValidationError('evidence text hash mismatch')
            spans = evidence['source_spans']
            if not isinstance(spans, list) or not spans:
                raise KnowledgeChatValidationError('evidence requires source spans')
            excerpts, locations = [], []
            for span in spans:
                parent = self._chunk(session, snapshot, span['chunk_id'])
                if parent['document_id'] != chunk['document_id'] or parent['version_id'] != chunk['version_id']:
                    raise KnowledgeChatValidationError('evidence span source mismatch')
                start, end = span['start'], span['end']
                if (span['offset_unit'] != 'unicode_codepoint' or isinstance(start, bool) or isinstance(end, bool)
                        or not isinstance(start, int) or not isinstance(end, int)
                        or not 0 <= start < end <= len(parent['text'])):
                    raise KnowledgeChatValidationError('invalid evidence source span')
                excerpts.append(parent['text'][start:end])
                locations.extend(parent['locations'])
            if body not in (''.join(excerpts), '\n'.join(excerpts)):
                raise KnowledgeChatValidationError('evidence text does not match original spans')
            if not isinstance(evidence['locations'], list) or any(location not in locations for location in evidence['locations']):
                raise KnowledgeChatValidationError('evidence location outside original source')
            result = dict(evidence)
            result.update(title=chunk['title'], filename=chunk['filename'])
            if 'formula_transcriptions' in result:
                validator=getattr(self,'formula_validator',None)
                if validator is None:
                    raise KnowledgeChatValidationError('formula validation unavailable')
                result['formula_transcriptions']=validator(snapshot,result,result['formula_transcriptions'],
                    session=session,source_checks=formula_sources)
            return result
        except (KeyError, TypeError, UnicodeError) as error:
            raise KnowledgeChatValidationError('invalid evidence structure') from error

    def save_trace(self, run_id, *, queries, candidates, evidence, outbound_messages, budget, timings, mode):
        if not isinstance(evidence, list) or not isinstance(outbound_messages, list):
            raise KnowledgeChatValidationError('invalid retrieval trace')
        _string(mode, 'retrieval mode', 32)
        with self.sessions.begin() as session:
            self._begin(session)
            _, _, trace = self._owned(session, run_id)
            snapshot = self._revalidate(session, json.loads(trace.snapshot_json))
            formula_sources=set()
            validated = [self._validate_evidence(session, snapshot, item,formula_sources=formula_sources) for item in evidence]
            if len({item['source_id'] for item in validated}) != len(validated):
                raise KnowledgeChatValidationError('duplicate evidence source ID')
            trace.queries_json, trace.candidates_json = _json(queries), _json(candidates)
            trace.evidence_json, trace.outbound_messages_json = _json(validated), _json(outbound_messages)
            trace.budget_json, trace.timings_json, trace.mode = _json(budget), _json(timings), mode

    def complete_turn(self, run_id, *, answer, source_ids, timings, answerable=True):
        _string(answer, 'answer', 100000)
        if not isinstance(source_ids, list) or any(not isinstance(source, str) for source in source_ids):
            raise KnowledgeChatValidationError('invalid cited source IDs')
        with self.sessions.begin() as session:
            self._begin(session)
            run, conversation, trace = self._owned(session, run_id)
            snapshot = self._revalidate(session, json.loads(trace.snapshot_json))
            formula_sources=set()
            evidence = [self._validate_evidence(session, snapshot, item,formula_sources=formula_sources)
                        for item in json.loads(trace.evidence_json)]
            by_source = {item['source_id']: item for item in evidence}
            if len(by_source) != len(evidence) or any(source not in by_source for source in source_ids):
                raise KnowledgeChatValidationError('unknown or duplicate evidence source ID')
            if answerable and not evidence:
                raise KnowledgeChatValidationError('an answer requires evidence or an explicit insufficient-evidence result')
            labels = re.findall(r'\[(S[0-9]+)\]', answer)
            if any(source not in source_ids for source in labels):
                raise KnowledgeChatValidationError('answer contains unverified citation label')
            message_id = str(uuid4())
            blocks = [{'type': 'text', 'text': answer}]
            citations = []
            for source_id in dict.fromkeys(source_ids):
                item = by_source[source_id]
                citation = Citation(id=str(uuid4()), message_id=message_id, run_id=run.id, source_id=source_id,
                    document_id=item['document_id'], version_id=item['version_id'], chunk_id=item['chunk_id'],
                    source_sha256=item['source_sha256'], evidence_text=item['evidence_text'],
                    evidence_text_sha256=item['evidence_text_sha256'], source_spans_json=_json(item['source_spans']),
                    locations_json=_json(item['locations']), quality_flags_json=_json(item.get('quality_flags', [])),
                    title=item['title'], filename=item['filename'])
                # Citation rows must follow their message FK, flushed below.
                blocks.append({'type': 'citation', **item, 'conversation_id': conversation.id,
                               'run_id': run.id, 'message_id': message_id, 'available': True})
                citations.append(citation)
            assistant = Message(id=message_id, conversation_id=conversation.id, run_id=run.id, role='assistant',
                                status='completed', blocks_json=_json(blocks), request_id=run.request_id)
            session.add(assistant)
            session.flush()
            session.add_all(citations)
            user = session.scalar(select(Message).where(Message.run_id == run.id, Message.role == 'user'))
            response = {'conversation_id': conversation.id, 'run_id': run.id, 'request_id': run.request_id,
                        'status': 'completed', 'user_message': self.conversations.serialize(user),
                        'assistant_message': self.conversations.serialize(assistant), 'timings': timings,
                        'answerable': bool(answerable),'retrieval_summary':self._trace_summary(session,trace)}
            run.status, run.response_json = 'completed', _json(response)
            conversation.active_run_id = None
            trace.stage, trace.timings_json = 'completed', _json(timings)
            self._event(session, run, 'completed', {'mode': trace.mode,'retrieval':self._trace_summary(session,trace)})
            return response

    def fail_turn(self, run_id, status='failed', category='provider'):
        if status not in ('failed', 'interrupted'):
            raise KnowledgeChatValidationError('invalid failure status')
        _string(category, 'error category', 24)
        with self.sessions.begin() as session:
            self._begin(session)
            run = self._require(session, Run, run_id)
            conversation, _ = self._conversation(session, run.conversation_id)
            if run.status != 'started' or conversation.active_run_id != run.id:
                return
            run.status, run.error_category = status, category
            conversation.active_run_id = None
            trace = session.get(Trace, run.id)
            if trace:
                trace.stage = status
            self.conversations.message(session, run, 'assistant', status,
                [{'type': 'text', 'text': '文献对话已中断，可重新发送。' if status == 'interrupted' else '文献对话未完成，请重新发送。'}])
            self._event(session, run, status, {'category': category})

    def _citation(self, session, conversation, citation, *, formula_sources=None):
        result = _serialize(citation)
        result.update(type='citation', conversation_id=conversation.id, available=True)
        trace = session.get(Trace, citation.run_id)
        try:
            if trace is None:
                raise KnowledgeChatScopeChanged('source trace unavailable')
            snapshot = json.loads(trace.snapshot_json)
            snapshot['document_versions'] = [source for source in snapshot['document_versions']
                                              if source['document_id'] == citation.document_id]
            self._revalidate(session, snapshot)
            stored=next((item for item in json.loads(trace.evidence_json)
                if item.get('source_id')==citation.source_id and item.get('chunk_id')==citation.chunk_id),None)
            if stored is not None and 'formula_transcriptions' in stored:
                result['formula_transcriptions']=stored['formula_transcriptions']
            self._validate_evidence(session, snapshot, result,formula_sources=formula_sources)
        except (KnowledgeChatValidationError, KnowledgeChatNotFound):
            result.pop('formula_transcriptions',None)
            result.update(available=False, unavailable_reason='source_unavailable_or_scope_revoked')
        return result

    def get_citation(self, conversation_id, message_id, source_id):
        with self.sessions() as session:
            conversation, _ = self._conversation(session, conversation_id)
            message = session.scalar(select(Message).where(Message.id == str(message_id), Message.conversation_id == conversation.id))
            if message is None:
                raise KnowledgeChatNotFound('unknown citation message')
            citation = session.scalar(select(Citation).where(Citation.message_id == message.id, Citation.source_id == str(source_id)))
            if citation is None:
                raise KnowledgeChatNotFound('unknown citation')
            return self._citation(session, conversation, citation)

    def safe_history(self, conversation_id, snapshot, limit=40):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise KnowledgeChatValidationError('invalid history limit')
        with self.sessions() as session:
            conversation, _ = self._conversation(session, conversation_id)
            if snapshot.get('conversation_id') != conversation.id:
                raise KnowledgeChatValidationError('history conversation mismatch')
            self._revalidate(session, snapshot)
            current_ids = {source['document_id'] for source in snapshot['document_versions']}
            runs = session.scalars(select(Run).where(Run.conversation_id == conversation.id, Run.status == 'completed'))
            allowed = set()
            formula_sources=set()
            for run in runs:
                trace = session.get(Trace, run.id)
                if trace is None:
                    continue
                evidence = json.loads(trace.evidence_json)
                if any(item.get('document_id') not in current_ids for item in evidence):
                    continue
                previous = json.loads(trace.snapshot_json)
                used_ids = {item.get('document_id') for item in evidence}
                previous['document_versions'] = [source for source in previous['document_versions'] if source['document_id'] in used_ids]
                try:
                    self._revalidate(session, previous)
                    for item in evidence:
                        self._validate_evidence(session, previous, item,formula_sources=formula_sources)
                except (KnowledgeChatValidationError, KnowledgeChatNotFound):
                    continue
                allowed.add(run.id)
            rows = session.scalars(select(Message).where(Message.conversation_id == conversation.id,
                Message.run_id.in_(allowed), Message.status == 'completed').order_by(Message.sequence))
            history = [{'role': row.role, 'text': '\n'.join(block['text'] for block in json.loads(row.blocks_json)
                       if block.get('type') == 'text')} for row in rows if row.role in ('user', 'assistant')]
            return history[-limit:]

    def events(self, conversation_id, after=0):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise KnowledgeChatValidationError('invalid event cursor')
        with self.sessions.begin() as session:
            self._begin(session)
            conversation, _ = self._conversation(session, conversation_id)
            self.conversations.recover(session, conversation)
            session.flush()
            events = session.scalars(select(Event).where(Event.conversation_id == conversation.id, Event.id > after).order_by(Event.id))
            result=[]
            public_fields={'base_id','base_name','scope_mode','document_count','mode','available',
                           'queries','documents','hit_count','evidence_count','action'}
            for event in events:
                data=json.loads(event.payload_json)
                if isinstance(data.get('retrieval'),dict):
                    trace=session.get(Trace,event.run_id)
                    current=self._trace_summary(session,trace) if trace else {
                        'available':False,'queries':[],'documents':[],'hit_count':0,'evidence_count':0}
                    historical=data['retrieval'] if current.get('available') else current
                    data['retrieval']={key:value for key,value in historical.items() if key in public_fields}
                result.append({'id':event.id,'kind':event.kind,'data':data})
            return result
