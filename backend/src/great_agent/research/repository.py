"""Short, serialized transactions for durable research and fenced worker commits.

Material capture and provider calls deliberately happen outside this repository.
Only completed, validated runs may advance the canonical research state.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import func, select, text

from great_agent.db.models import utc_now
from great_agent.research.errors import ResearchError
from great_agent.research.models import (
    ResearchCheckpointModel as Checkpoint,
    ResearchEventModel as Event,
    ResearchEvidenceModel as Evidence,
    ResearchMaterialRevisionModel as MaterialRevision,
    ResearchMessageModel as Message,
    ResearchModelCallModel as ModelCall,
    ResearchReportModel as Report,
    ResearchRunEvidenceModel as RunEvidence,
    ResearchRunModel as Run,
    ResearchSessionModel as ResearchSession,
    ResearchSettingsRevisionModel as SettingsRevision,
    ResearchStateRevisionModel as StateRevision,
)

TERMINAL = frozenset({'completed', 'failed', 'timed_out', 'cancelled', 'interrupted'})
RETRYABLE = TERMINAL - {'completed'}
STAGES = frozenset({'preparing', 'gathering', 'analyzing', 'validating'})


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _load(value, default=None):
    return json.loads(value) if value is not None else default


def _hash(value):
    return hashlib.sha256(_dump(value).encode('utf-8')).hexdigest()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _summary(snapshot):
    return [{key: material[key] for key in ('id', 'kind', 'title', 'locator', 'sha256', 'quality_flags', 'provenance')
             if key in material} for material in snapshot.get('materials', [])]


class ResearchRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    @contextmanager
    def _write(self):
        with self.sessions.begin() as session:
            # SQLite serializes only the brief metadata transition, never network I/O.
            session.execute(text('BEGIN IMMEDIATE'))
            yield session

    @staticmethod
    def _required(session, model, identity, kind):
        row = session.get(model, identity)
        if row is None:
            raise ResearchError('not_found', f'Research {kind} was not found.', 404)
        return row

    @staticmethod
    def _version(row, expected_version):
        if row.version != expected_version:
            raise ResearchError('version_conflict', 'The research session changed. Reload it before submitting.')

    @staticmethod
    def _settings(model_profile_id, mode):
        if mode not in ('quick', 'deep') or not isinstance(model_profile_id, str) or not model_profile_id.strip():
            raise ResearchError('invalid_settings', 'Select a valid research mode and model profile.', 422)
        return {'model_profile_id': model_profile_id, 'mode': mode}

    @staticmethod
    def _event(session, session_id, event_type, payload=None, run_id=None):
        row = Event(session_id=session_id, run_id=run_id, type=event_type, payload_json=_dump(payload or {}))
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _message(row):
        return {'id': row.id, 'run_id': row.run_id, 'role': row.role, 'status': row.status,
                'text': row.text, 'blocks': _load(row.blocks_json, []), 'sequence': row.sequence}

    @staticmethod
    def _run(row):
        settings = _load(row.settings_json)
        return {'id': row.id, 'session_id': row.session_id, 'request_id': row.request_id,
                'status': row.status, 'stage': row.stage, 'message': row.message,
                'snapshot_summary': _summary(_load(row.snapshot_json)),
                'model_profile_id': settings['model_profile_id'], 'mode': settings['mode'],
                'followup_target': _load(row.followup_json), 'material_revision': row.material_revision,
                'settings_revision': row.settings_revision, 'base_state_revision': row.base_state_revision,
                'result': _load(row.result_json), 'usage': _load(row.usage_json),
                'error_code': row.error_code, 'error_message': row.error_message,
                'retry_of': row.retry_of, 'created_at': _utc(row.created_at).isoformat(),
                'updated_at': _utc(row.updated_at).isoformat()}

    def _session(self, session, row, *, detail=True):
        material = session.get(MaterialRevision, (row.id, row.material_revision))
        settings = session.get(SettingsRevision, (row.id, row.settings_revision))
        state = session.get(StateRevision, (row.id, row.state_revision))
        value = {'id': row.id, 'title': row.title, 'goal': row.goal, 'version': row.version,
                 'material_revision': row.material_revision, 'settings_revision': row.settings_revision,
                 'state_revision': row.state_revision, 'settings': _load(settings.settings_json),
                 'materials': _load(material.selection_json), 'material_summary': _summary(_load(material.snapshot_json)),
                 'active_run_id': row.active_run_id, 'created_at': _utc(row.created_at).isoformat(),
                 'updated_at': _utc(row.updated_at).isoformat()}
        if row.active_run_id:
            value['stage'] = self._required(session, Run, row.active_run_id, 'run').stage
        if detail:
            value['messages'] = [self._message(message) for message in session.scalars(
                select(Message).where(Message.session_id == row.id).order_by(Message.sequence))]
            value['items'] = _load(state.items_json, []) if state else []
            value['reports'] = [_load(report.payload_json) for report in session.scalars(
                select(Report).where(Report.session_id == row.id).order_by(Report.created_at, Report.id))]
        return value

    def create_session(self, *, title='', goal='', materials=None, snapshot=None,
                       model_profile_id='default', mode='deep'):
        settings = self._settings(model_profile_id, mode)
        selection = materials or []
        snapshot = snapshot or {'id': str(uuid4()), 'selection': selection, 'materials': [], 'limitations': []}
        with self._write() as session:
            row = ResearchSession(id=str(uuid4()), title=title or '新研究课题', goal=goal or '',
                                  version=1, material_revision=1, settings_revision=1, state_revision=0,
                                  active_run_id=None, next_sequence=1)
            session.add(row)
            session.flush()
            session.add_all([
                MaterialRevision(session_id=row.id, revision=1, selection_json=_dump(selection), snapshot_json=_dump(snapshot)),
                SettingsRevision(session_id=row.id, revision=1, settings_json=_dump(settings)),
                StateRevision(session_id=row.id, revision=0, run_id=None, items_json='[]'),
            ])
            self._event(session, row.id, 'session_created', {'version': 1})
            return self._session(session, row)

    def list_sessions(self):
        with self.sessions() as session:
            return [self._session(session, row, detail=False) for row in session.scalars(
                select(ResearchSession).order_by(ResearchSession.updated_at.desc(), ResearchSession.id))]

    def get_session(self, session_id):
        with self.sessions() as session:
            return self._session(session, self._required(session, ResearchSession, str(session_id), 'session'))

    def update_materials(self, session_id, *, expected_version, materials, snapshot):
        with self._write() as session:
            row = self._required(session, ResearchSession, str(session_id), 'session')
            self._version(row, expected_version)
            row.material_revision += 1
            row.version += 1
            row.updated_at = utc_now()
            session.add(MaterialRevision(session_id=row.id, revision=row.material_revision,
                                         selection_json=_dump(materials), snapshot_json=_dump(snapshot)))
            self._event(session, row.id, 'materials_changed', {'version': row.version, 'material_revision': row.material_revision})
            return self._session(session, row)

    def update_settings(self, session_id, *, expected_version, model_profile_id, mode):
        settings = self._settings(model_profile_id, mode)
        with self._write() as session:
            row = self._required(session, ResearchSession, str(session_id), 'session')
            self._version(row, expected_version)
            row.settings_revision += 1
            row.version += 1
            row.updated_at = utc_now()
            session.add(SettingsRevision(session_id=row.id, revision=row.settings_revision, settings_json=_dump(settings)))
            self._event(session, row.id, 'settings_changed', {'version': row.version, 'settings_revision': row.settings_revision})
            return self._session(session, row)

    @staticmethod
    def _existing_request(session, session_id, request_id, request):
        existing = session.scalar(select(Run).where(Run.session_id == session_id, Run.request_id == request_id))
        if existing is not None and existing.request_json != _dump(request):
            raise ResearchError('request_conflict', 'This request ID was already used for different research input.')
        return existing

    def _followup(self, session, session_id, target):
        if target is None:
            return None
        if not isinstance(target, dict) or not target.get('run_id'):
            raise ResearchError('invalid_followup', 'Select an existing answer or research item.', 422)
        parent = session.get(Run, str(target['run_id']))
        def invalid():
            raise ResearchError('invalid_followup', 'The follow-up target does not belong to this research session.', 422)
        if parent is None or parent.session_id != session_id or parent.status != 'completed':
            invalid()
        messages = list(session.scalars(select(Message).where(Message.run_id == parent.id)))
        if target.get('message_id'):
            messages = [message for message in messages if message.id == target['message_id']]
            if not messages:
                invalid()
        if target.get('block_id') and not any(block.get('id') == target['block_id']
                for message in messages for block in _load(message.blocks_json, [])):
            invalid()
        if target.get('item_id'):
            states = session.scalars(select(StateRevision).where(StateRevision.session_id == session_id,
                                                                 StateRevision.run_id == parent.id))
            if not any(item.get('id') == target['item_id'] for state in states for item in _load(state.items_json, [])):
                invalid()
        return target

    def _followup_context(self, session, target):
        if target is None:
            return None
        run = self._required(session, Run, str(target['run_id']), 'run')
        messages = [self._message(message) for message in session.scalars(select(Message)
                    .where(Message.run_id == run.id).order_by(Message.sequence))]
        if target.get('message_id'):
            messages = [message for message in messages if message['id'] == target['message_id']]
        if target.get('block_id'):
            messages = [{**message, 'blocks': [block for block in message['blocks'] if block.get('id') == target['block_id']]}
                        for message in messages if any(block.get('id') == target['block_id'] for block in message['blocks'])]
        items = []
        if target.get('item_id'):
            for state in session.scalars(select(StateRevision).where(StateRevision.run_id == run.id)):
                items.extend(item for item in _load(state.items_json, []) if item.get('id') == target['item_id'])
        return {'run_id': run.id, 'question': run.message, 'messages': messages, 'items': items}

    def _new_run(self, session, row, *, request_id, request, message, followup_target,
                 material_revision, settings_revision, snapshot_json, settings_json, budget_json,
                 base_state_revision=None, context_json=None, retry_of=None):
        if row.active_run_id:
            raise ResearchError('active_run', 'This research session already has an active run.')
        base = row.state_revision if base_state_revision is None else base_state_revision
        if context_json is None:
            state = session.get(StateRevision, (row.id, base))
            recent = list(session.scalars(select(Message).join(Run, Run.id == Message.run_id)
                          .where(Message.session_id == row.id, Run.status == 'completed')
                          .order_by(Message.sequence.desc()).limit(40)))
            context_json = _dump({'goal': row.goal, 'title': row.title, 'state_revision': base,
                                  'items': _load(state.items_json, []) if state else [],
                                  'messages': [self._message(message) for message in reversed(recent)],
                                  'followup_target': followup_target,
                                  'followup': self._followup_context(session, followup_target)})
        run = Run(id=str(uuid4()), session_id=row.id, request_id=request_id,
                  request_sha256=_hash({'request': request, 'material_revision': material_revision,
                                        'settings_revision': settings_revision, 'budget': _load(budget_json)}),
                  request_json=_dump(request), status='queued', stage='queued', message=message,
                  followup_json=_dump(followup_target), material_revision=material_revision,
                  settings_revision=settings_revision, base_state_revision=base, snapshot_json=snapshot_json,
                  context_json=context_json, settings_json=settings_json, budget_json=budget_json,
                  owner_id=None, fencing_token=0, cancel_requested=0, retry_of=retry_of)
        session.add(run)
        session.flush()
        session.add(Message(id=str(uuid4()), session_id=row.id, run_id=run.id, role='user',
                            status='completed', text=message, blocks_json='[]', sequence=row.next_sequence))
        row.next_sequence += 1
        row.active_run_id = run.id
        row.version += 1
        row.updated_at = utc_now()
        self._event(session, row.id, 'queued', {'run_id': run.id, 'version': row.version}, run.id)
        return self._run(run)

    def create_run(self, session_id, *, request_id, expected_version, message, followup_target=None, budget=None):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ResearchError('invalid_request', 'A request ID of at most 128 characters is required.', 422)
        if not isinstance(message, str) or not message.strip():
            raise ResearchError('invalid_request', 'Enter a research question.', 422)
        request = {'request_id': request_id, 'expected_version': expected_version, 'message': message,
                   'followup_target': followup_target, 'budget': budget or {}}
        with self._write() as session:
            row = self._required(session, ResearchSession, str(session_id), 'session')
            existing = self._existing_request(session, row.id, request_id, request)
            if existing is not None:
                return self._run(existing)
            self._version(row, expected_version)
            followup_target = self._followup(session, row.id, followup_target)
            material = session.get(MaterialRevision, (row.id, row.material_revision))
            settings = session.get(SettingsRevision, (row.id, row.settings_revision))
            return self._new_run(session, row, request_id=request_id, request=request, message=message,
                                 followup_target=followup_target, material_revision=row.material_revision,
                                 settings_revision=row.settings_revision, snapshot_json=material.snapshot_json,
                                 settings_json=settings.settings_json, budget_json=_dump(budget or {}))

    def get_run(self, run_id):
        with self.sessions() as session:
            return self._run(self._required(session, Run, str(run_id), 'run'))

    def find_request(self, session_id, request_id):
        """Recover server-generated accepted configuration before checking a replay."""
        with self.sessions() as session:
            self._required(session, ResearchSession, str(session_id), 'session')
            row = session.scalar(select(Run).where(Run.session_id == str(session_id), Run.request_id == request_id))
            return {'run': self._run(row), 'budget': _load(row.budget_json),
                    'request': _load(row.request_json)} if row is not None else None

    def get_run_snapshot(self, run_id):
        with self.sessions() as session:
            return _load(self._required(session, Run, str(run_id), 'run').snapshot_json)

    def get_run_context(self, run_id):
        with self.sessions() as session:
            return _load(self._required(session, Run, str(run_id), 'run').context_json)

    @staticmethod
    def _owned(row, owner_id, fencing_token):
        if (row.status != 'running' or row.owner_id != str(owner_id) or row.fencing_token != fencing_token
                or row.cancel_requested or row.lease_expires_at is None or _utc(row.lease_expires_at) <= utc_now()):
            raise ResearchError('ownership_lost', 'This worker no longer owns the research run.')

    def claim_next(self, owner_id, *, lease_seconds=60, max_concurrency=1):
        if lease_seconds <= 0 or max_concurrency < 1:
            raise ValueError('positive research lease and concurrency are required')
        with self._write() as session:
            active = session.scalar(select(func.count()).select_from(Run).where(Run.status == 'running'))
            if active >= max_concurrency:
                return None
            row = session.scalar(select(Run).where(Run.status == 'queued').order_by(Run.created_at, Run.id).limit(1))
            if row is None:
                return None
            row.status, row.stage, row.owner_id = 'running', 'preparing', str(owner_id)
            row.fencing_token += 1
            row.heartbeat_at = row.updated_at = utc_now()
            row.lease_expires_at = row.heartbeat_at + timedelta(seconds=lease_seconds)
            self._event(session, row.session_id, 'preparing', {'run_id': row.id}, row.id)
            return {**self._run(row), 'owner_id': row.owner_id, 'fencing_token': row.fencing_token,
                    'lease_expires_at': _utc(row.lease_expires_at).isoformat(),
                    'snapshot': _load(row.snapshot_json), 'context': _load(row.context_json),
                    'budget': _load(row.budget_json)}

    def heartbeat(self, run_id, owner_id, fencing_token, *, lease_seconds=60):
        if lease_seconds <= 0:
            raise ValueError('positive research lease is required')
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            try:
                self._owned(row, owner_id, fencing_token)
            except ResearchError:
                return False
            row.heartbeat_at = utc_now()
            row.lease_expires_at = row.heartbeat_at + timedelta(seconds=lease_seconds)
            return True

    def set_stage(self, run_id, owner_id, fencing_token, stage):
        if stage not in STAGES:
            raise ValueError('unknown research stage')
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            self._owned(row, owner_id, fencing_token)
            row.stage, row.updated_at = stage, utc_now()
            self._event(session, row.session_id, stage, {'run_id': row.id}, row.id)
            return self._run(row)

    @staticmethod
    def _evidence(session, link):
        value = _load(session.get(Evidence, link.evidence_id).payload_json)
        return {**value, 'alias': link.alias}

    def add_evidence(self, run_id, owner_id, fencing_token, evidence):
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            self._owned(row, owner_id, fencing_token)
            values = []
            for value in evidence:
                alias = value.get('alias', '')
                if re.fullmatch(r'[ECL][1-9][0-9]*', alias) is None or not value.get('id'):
                    raise ResearchError('invalid_evidence', 'Evidence needs a stable ID and a valid run alias.', 422)
                payload = {key: item for key, item in value.items() if key != 'alias'}
                stored = session.get(Evidence, str(value['id']))
                if stored is not None and stored.payload_sha256 != _hash(payload):
                    raise ResearchError('evidence_conflict', 'An immutable evidence ID cannot be changed.')
                link = session.get(RunEvidence, (row.id, alias))
                if link is not None and link.evidence_id != str(value['id']):
                    raise ResearchError('evidence_conflict', 'An evidence alias cannot be reassigned within a run.')
                other_alias = session.scalar(select(RunEvidence).where(
                    RunEvidence.run_id == row.id, RunEvidence.evidence_id == str(value['id'])))
                if other_alias is not None and other_alias.alias != alias:
                    raise ResearchError('evidence_conflict', 'An evidence object already has an alias in this run.')
                if stored is None:
                    session.add(Evidence(id=str(value['id']), payload_json=_dump(payload), payload_sha256=_hash(payload)))
                    session.flush()
                if link is None:
                    session.add(RunEvidence(run_id=row.id, alias=alias, evidence_id=str(value['id'])))
                    session.flush()
                values.append(value)
            if values:
                self._event(session, row.session_id, 'evidence_registered', {'aliases': [value['alias'] for value in values]}, row.id)
            return values

    def list_evidence(self, run_id):
        with self.sessions() as session:
            self._required(session, Run, str(run_id), 'run')
            return [self._evidence(session, link) for link in session.scalars(
                select(RunEvidence).where(RunEvidence.run_id == str(run_id)).order_by(RunEvidence.alias))]

    def get_evidence(self, run_id, alias):
        with self.sessions() as session:
            self._required(session, Run, str(run_id), 'run')
            link = self._required(session, RunEvidence, (str(run_id), alias), 'evidence')
            return self._evidence(session, link)

    def get_evidence_by_id(self, evidence_id):
        with self.sessions() as session:
            return _load(self._required(session, Evidence, str(evidence_id), 'evidence').payload_json)

    @staticmethod
    def _checkpoint(row):
        return {'run_id': row.run_id, 'key': row.key, 'payload': _load(row.payload_json),
                'created_at': _utc(row.created_at).isoformat()}

    def save_checkpoint(self, run_id, owner_id, fencing_token, *, key, payload):
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            self._owned(row, owner_id, fencing_token)
            checkpoint = session.get(Checkpoint, (row.id, key))
            if checkpoint is not None:
                if checkpoint.payload_json != _dump(payload):
                    raise ResearchError('checkpoint_conflict', 'A completed research checkpoint cannot be overwritten.')
            else:
                checkpoint = Checkpoint(run_id=row.id, key=key, payload_json=_dump(payload))
                session.add(checkpoint)
                session.flush()
            return self._checkpoint(checkpoint)

    def get_checkpoint(self, run_id, key):
        with self.sessions() as session:
            self._required(session, Run, str(run_id), 'run')
            row = session.get(Checkpoint, (str(run_id), key))
            return self._checkpoint(row) if row else None

    def list_checkpoints(self, run_id):
        with self.sessions() as session:
            self._required(session, Run, str(run_id), 'run')
            return [self._checkpoint(row) for row in session.scalars(select(Checkpoint)
                    .where(Checkpoint.run_id == str(run_id)).order_by(Checkpoint.created_at, Checkpoint.key))]

    @staticmethod
    def _call(row):
        return {'id': row.id, 'run_id': row.run_id, 'status': row.status, 'fencing_token': row.fencing_token,
                'request': _load(row.request_json), 'evidence_ids': _load(row.evidence_ids_json),
                'model_fingerprint': row.model_fingerprint, 'response': _load(row.response_json),
                'usage': _load(row.usage_json), 'created_at': _utc(row.created_at).isoformat(),
                'updated_at': _utc(row.updated_at).isoformat()}

    def start_model_call(self, run_id, owner_id, fencing_token, *, request, evidence_ids=None,
                         model_fingerprint='', call_id=None):
        evidence_ids = evidence_ids or []
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            self._owned(row, owner_id, fencing_token)
            known = set(session.scalars(select(RunEvidence.evidence_id).where(RunEvidence.run_id == row.id)))
            if set(evidence_ids) - known:
                raise ResearchError('invalid_evidence', 'Model input contains evidence not registered for this run.', 422)
            existing = session.get(ModelCall, str(call_id)) if call_id is not None else None
            if existing is not None:
                if (existing.run_id != row.id or existing.request_json != _dump(request)
                        or existing.evidence_ids_json != _dump(evidence_ids) or existing.model_fingerprint != model_fingerprint):
                    raise ResearchError('model_call_conflict', 'A model request ID cannot be changed.')
                return self._call(existing)
            call = ModelCall(id=str(call_id or uuid4()), run_id=row.id, fencing_token=fencing_token,
                             status='pending', request_json=_dump(request), evidence_ids_json=_dump(evidence_ids),
                             model_fingerprint=model_fingerprint)
            session.add(call)
            session.flush()
            self._event(session, row.session_id, 'model_request_recorded', {'call_id': call.id}, row.id)
            return self._call(call)

    def record_model_response(self, run_id, owner_id, fencing_token, *, call_id, response, usage=None):
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            self._owned(row, owner_id, fencing_token)
            call = self._required(session, ModelCall, str(call_id), 'model call')
            if call.run_id != row.id:
                raise ResearchError('model_call_conflict', 'The model call belongs to another run.')
            if call.response_json is not None:
                if call.response_json != _dump(response) or call.usage_json != _dump(usage):
                    raise ResearchError('model_call_conflict', 'A saved model response cannot be changed.')
                return self._call(call)
            if call.status not in ('pending', 'sent'):
                raise ResearchError('model_call_conflict', 'This model call cannot accept a response.')
            call.status, call.response_json, call.usage_json = 'completed', _dump(response), _dump(usage)
            call.updated_at = utc_now()
            self._event(session, row.session_id, 'model_response_saved', {'call_id': call.id}, row.id)
            return self._call(call)

    def list_model_calls(self, run_id):
        with self.sessions() as session:
            self._required(session, Run, str(run_id), 'run')
            return [self._call(row) for row in session.scalars(select(ModelCall)
                    .where(ModelCall.run_id == str(run_id)).order_by(ModelCall.created_at, ModelCall.id))]

    def _terminal(self, session, row, status, *, error_code=None, message=None, usage=None):
        row.status = row.stage = status
        row.error_code, row.error_message = error_code, message
        row.usage_json = _dump(usage) if usage is not None else row.usage_json
        row.updated_at = utc_now()
        row.lease_expires_at = None
        parent = self._required(session, ResearchSession, row.session_id, 'session')
        if parent.active_run_id == row.id:
            parent.active_run_id = None
        parent.version += 1
        parent.updated_at = utc_now()
        self._event(session, row.session_id, status, {'run_id': row.id, 'error_code': error_code,
                                                     'version': parent.version}, row.id)
        return parent

    def finish(self, run_id, owner_id, fencing_token, *, result, items=None, usage=None):
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            # Retrying only the local commit after a lost acknowledgement is safe.
            saved = _load(row.result_json, {})
            replay_result = {**result, 'state_conflict': True} if saved.get('state_conflict') else result
            if (row.status == 'completed' and row.owner_id == str(owner_id) and row.fencing_token == fencing_token
                    and row.result_json == _dump(replay_result)):
                return self._run(row)
            self._owned(row, owner_id, fencing_token)
            parent = self._required(session, ResearchSession, row.session_id, 'session')
            final_result = dict(result)
            if parent.state_revision == row.base_state_revision:
                previous = session.get(StateRevision, (parent.id, parent.state_revision))
                old_items = _load(previous.items_json, []) if previous else []
                merged = {item['id']: item for item in old_items}
                for item in items or []:
                    item = dict(item)
                    item.setdefault('id', str(uuid4()))
                    old = merged.get(item['id'])
                    item.setdefault('origin_run_id', old.get('origin_run_id') if old else row.id)
                    item['revision'] = old.get('revision', 1) + 1 if old else 1
                    item.setdefault('support', [])
                    item.setdefault('opposition', [])
                    item.setdefault('unknowns', [])
                    merged[item['id']] = item
                parent.state_revision += 1
                session.add(StateRevision(session_id=parent.id, revision=parent.state_revision,
                                           run_id=row.id, items_json=_dump(list(merged.values()))))
            else:
                final_result['state_conflict'] = True
            row.result_json = _dump(final_result)
            session.add(Message(id=str(uuid4()), session_id=parent.id, run_id=row.id,
                                role='assistant', status='completed', text=str(final_result.get('answer', '')),
                                blocks_json=_dump(final_result.get('answer_blocks', [])), sequence=parent.next_sequence))
            parent.next_sequence += 1
            self._terminal(session, row, 'completed', usage=usage)
            return self._run(row)

    def fail(self, run_id, owner_id, fencing_token, *, error_code, message, status='failed', usage=None):
        if status not in RETRYABLE - {'cancelled'}:
            raise ValueError('invalid failed research status')
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            if (row.status == status and row.owner_id == str(owner_id) and row.fencing_token == fencing_token
                    and row.error_code == error_code):
                return self._run(row)
            self._owned(row, owner_id, fencing_token)
            self._terminal(session, row, status, error_code=error_code, message=message, usage=usage)
            return self._run(row)

    def cancel(self, run_id):
        with self._write() as session:
            row = self._required(session, Run, str(run_id), 'run')
            if row.status in TERMINAL:
                return self._run(row)
            row.cancel_requested = 1
            row.fencing_token += 1
            self._event(session, row.session_id, 'cancel_requested', {'run_id': row.id}, row.id)
            self._terminal(session, row, 'cancelled')
            return self._run(row)

    def recover_expired(self, *, now=None):
        now = _utc(now or utc_now())
        with self._write() as session:
            rows = list(session.scalars(select(Run).where(Run.status == 'running', Run.lease_expires_at <= now)))
            for row in rows:
                uncertain = list(session.scalars(select(ModelCall).where(ModelCall.run_id == row.id,
                                             ModelCall.status.in_(('pending', 'sent')))))
                row.fencing_token += 1
                if uncertain:
                    for call in uncertain:
                        call.status, call.updated_at = 'uncertain', utc_now()
                    self._terminal(session, row, 'interrupted', error_code='uncertain_model_call',
                                   message='A previous model request may have completed. Retry explicitly to continue.')
                else:
                    row.status, row.stage, row.owner_id, row.lease_expires_at = 'queued', 'queued', None, None
                    row.updated_at = utc_now()
                    self._event(session, row.session_id, 'requeued', {'run_id': row.id, 'reason': 'lease_expired'}, row.id)
            return len(rows)

    def retry_run(self, run_id, *, request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ResearchError('invalid_request', 'A request ID of at most 128 characters is required.', 422)
        request = {'retry_of': str(run_id), 'request_id': request_id}
        with self._write() as session:
            original = self._required(session, Run, str(run_id), 'run')
            parent = self._required(session, ResearchSession, original.session_id, 'session')
            existing = self._existing_request(session, parent.id, request_id, request)
            if existing is not None:
                return self._run(existing)
            if original.status not in RETRYABLE:
                raise ResearchError('invalid_retry', 'Only failed, timed out, cancelled or interrupted runs can be retried.')
            if parent.state_revision != original.base_state_revision:
                raise ResearchError('stale_retry', 'Research state changed. Ask the original question as a new follow-up.')
            return self._new_run(session, parent, request_id=request_id, request=request, message=original.message,
                                 followup_target=_load(original.followup_json), material_revision=original.material_revision,
                                 settings_revision=original.settings_revision, snapshot_json=original.snapshot_json,
                                 settings_json=original.settings_json, budget_json=original.budget_json,
                                 base_state_revision=original.base_state_revision,
                                 context_json=original.context_json, retry_of=original.id)

    def list_events(self, session_id, *, after=0, limit=100):
        with self.sessions() as session:
            self._required(session, ResearchSession, str(session_id), 'session')
            return [{'id': row.id, 'session_id': row.session_id, 'run_id': row.run_id, 'type': row.type,
                     'payload': _load(row.payload_json), 'created_at': _utc(row.created_at).isoformat()}
                    for row in session.scalars(select(Event).where(Event.session_id == str(session_id), Event.id > after)
                                               .order_by(Event.id).limit(max(1, min(limit, 1000))))]

    def create_report(self, session_id, *, title='', content='', run_ids=None):
        with self._write() as session:
            parent = self._required(session, ResearchSession, str(session_id), 'session')
            if run_ids is None:
                runs = list(session.scalars(select(Run).where(Run.session_id == parent.id, Run.status == 'completed')
                                             .order_by(Run.created_at, Run.id)))
            else:
                runs = [self._required(session, Run, str(run_id), 'run') for run_id in dict.fromkeys(run_ids)]
            if any(row.session_id != parent.id or row.status != 'completed' for row in runs):
                raise ResearchError('invalid_report', 'Reports can include only completed runs from this session.', 422)
            evidence = []
            for run in runs:
                evidence.extend({'run_id': run.id, **self._evidence(session, link)} for link in session.scalars(
                    select(RunEvidence).where(RunEvidence.run_id == run.id).order_by(RunEvidence.alias)))
            state = session.get(StateRevision, (parent.id, parent.state_revision))
            report_id = str(uuid4())
            payload = {'id': report_id, 'session_id': parent.id, 'title': title or parent.title,
                       'version': 1 + session.scalar(select(func.count()).select_from(Report).where(Report.session_id == parent.id)),
                       'content': content, 'run_ids': [run.id for run in runs], 'state_revision': parent.state_revision,
                       'snapshot': {'goal': parent.goal, 'runs': [self._run(run) for run in runs],
                                    'items': _load(state.items_json, []) if state else [], 'evidence': evidence},
                       'created_at': utc_now().isoformat()}
            session.add(Report(id=report_id, session_id=parent.id, payload_json=_dump(payload)))
            self._event(session, parent.id, 'report_created', {'report_id': report_id})
            return payload

    def list_reports(self, session_id):
        with self.sessions() as session:
            self._required(session, ResearchSession, str(session_id), 'session')
            return [_load(row.payload_json) for row in session.scalars(select(Report)
                    .where(Report.session_id == str(session_id)).order_by(Report.created_at, Report.id))]
