from __future__ import annotations
import json
import os
import time
from uuid import uuid4
import psutil
from sqlalchemy import select, text
from great_agent.db.conversation_models import ConversationModel, ConversationRunModel, ConversationMessageModel, ConversationEventModel
from great_agent.errors import ExperimentNotFoundError, RevisionConflictError

class ConversationConflict(RevisionConflictError): pass

class ConversationRepository:
    def __init__(self, session_factory): self.sessions = session_factory
    @staticmethod
    def require(session, conversation_id):
        row = session.get(ConversationModel, str(conversation_id))
        if row is None: raise ExperimentNotFoundError('unknown conversation')
        return row
    @staticmethod
    def event(session, run, kind):
        session.add(ConversationEventModel(conversation_id=run.conversation_id, run_id=run.id, kind=kind,
            payload_json=json.dumps({'run_id':run.id, 'request_id':run.request_id, 'status':kind})))
    @staticmethod
    def message(session, run, role, status, blocks):
        row = ConversationMessageModel(id=str(uuid4()), conversation_id=run.conversation_id, run_id=run.id,
            role=role,status=status,blocks_json=json.dumps(blocks,ensure_ascii=False),request_id=run.request_id)
        session.add(row)
        session.flush()
        return row
    @staticmethod
    def serialize(row):
        return {'id':row.id,'role':row.role,'status':row.status,'blocks':json.loads(row.blocks_json),'request_id':row.request_id}
    def interrupt(self, session, conversation, run):
        if run.status != 'started': return
        run.status = 'interrupted'
        self.message(session,run,'assistant','interrupted',[{'type':'text','text':'对话已中断，可重新发送。'}] +
            ([{'type':'proposal_ref','id':run.proposal_id}] if run.proposal_id else []))
        conversation.active_run_id = None
        self.event(session,run,'interrupted')
    def recover(self, session, conversation):
        if conversation.active_run_id:
            run = session.get(ConversationRunModel, conversation.active_run_id)
            if run and (run.lease_until <= time.time() or not psutil.pid_exists(run.owner_pid)):
                self.interrupt(session,conversation,run)
    @staticmethod
    def assert_owner(session, run_id):
        run = session.get(ConversationRunModel, run_id)
        conversation = session.get(ConversationModel, run.conversation_id)
        if run.status != 'started' or conversation.active_run_id != run.id or run.lease_until <= time.time():
            raise ConversationConflict('conversation turn no longer active')
        return run, conversation
    def claim(self, conversation_id, message, request_id, lease_seconds, *, execution_preview_id=None):
        if '\0' in message:
            raise ConversationConflict('message contains an unsupported NUL character')
        # Bind a request ID to the exact reviewed XML as well as the user's words.
        identity = message if execution_preview_id is None else '\0execution:' + json.dumps(
            [message, str(execution_preview_id)], ensure_ascii=False)
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            conversation = self.require(session,conversation_id)
            self.recover(session,conversation)
            run = session.scalar(select(ConversationRunModel).where(ConversationRunModel.conversation_id==conversation.id,
                ConversationRunModel.request_id==str(request_id)))
            if run:
                if run.input_text != identity: raise ConversationConflict('request ID already used with different content')
                if run.status == 'completed': return run, json.loads(run.response_json)
                if run.status == 'failed': return run, None
                raise ConversationConflict('request is active or interrupted; use a new request ID')
            if conversation.active_run_id: raise ConversationConflict('conversation already has an active turn')
            run = ConversationRunModel(id=str(uuid4()),conversation_id=conversation.id,request_id=str(request_id),
                input_text=identity,status='started',lease_until=time.time()+lease_seconds,owner_pid=os.getpid())
            session.add(run)
            session.flush()
            conversation.active_run_id = run.id
            self.message(session,run,'user','completed',[{'type':'text','text':message}])
            self.event(session,run,'started')
            return run,None
    def events(self, conversation_id, after=0):
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            conversation=self.require(session,conversation_id)
            self.recover(session,conversation)
            session.flush()
            rows=session.scalars(select(ConversationEventModel).where(ConversationEventModel.conversation_id==str(conversation_id),
                ConversationEventModel.id>after).order_by(ConversationEventModel.id)).all()
            return [{'id':r.id,'kind':r.kind,'data':json.loads(r.payload_json)} for r in rows]
