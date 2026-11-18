from __future__ import annotations
import asyncio
import json
import logging
from contextlib import contextmanager
from time import perf_counter
from uuid import UUID, uuid4
from sqlalchemy import select, text
from pydantic import ValidationError
from great_agent.adapters.model_provider import canonicalize_station_selection, ModelProposalError, ModelProviderError, MODEL_ERROR_MESSAGES, ModelTimeout
from great_agent.api.proposal_models import ProposalResponse
from great_agent.db.conversation_models import ConversationModel, ConversationRunModel, ConversationMessageModel
from great_agent.db.conversation_repository import ConversationRepository, ConversationConflict
from great_agent.db.models import DailyTaskModel, ExperimentModel, PlanRevisionModel
from great_agent.domain.plans import ExperimentSpec
from great_agent.domain.experiment_defaults import default_experiment_spec
from great_agent.domain.execution_intent import is_execution_approval
from great_agent.errors import ExecutionReviewError, TaskStateError, PlanFrozenError, RevisionConflictError, PlanValidationError
from great_agent.errors import ExperimentNotFoundError

logger = logging.getLogger('uvicorn.error.great_agent.chat')


def _error_metadata(error):
    # Never serialize exceptions: SDK messages, bodies and request URLs may
    # contain credentials or user content. Inspect only bounded safe metadata.
    chain=[]
    current=error
    while current is not None and len(chain)<5 and all(current is not item for item in chain):
        chain.append(current)
        current=current.__cause__ or current.__context__
    def type_name(item):
        name=type(item).__name__
        return name if name.isidentifier() and len(name)<=80 else 'Exception'
    category=('interrupted' if isinstance(error,asyncio.CancelledError)
        else 'timeout' if isinstance(error,(TimeoutError,ModelTimeout))
        else 'schema' if isinstance(error,(ValidationError,PlanValidationError))
        else 'model' if isinstance(error,(ModelProviderError,ModelProposalError)) else 'internal')
    metadata={'error_category':category,'exception_type':type_name(error),
        'cause_types':[type_name(item) for item in chain[1:]]}
    for item in chain:
        status=getattr(item,'upstream_status',None)
        if status is None: status=getattr(item,'status_code',None)
        if status is None: status=getattr(getattr(item,'response',None),'status_code',None)
        if 'upstream_status' not in metadata and type(status) is int and 100<=status<=599:
            metadata['upstream_status']=status
        code=getattr(item,'diagnostic_code',None)
        if type(code) is str and code=='missing_api_key':
            metadata['diagnostic_code']='missing_api_key'
    return metadata


@contextmanager
def _stage(run,stage,**metadata):
    started=perf_counter()
    record={'request_id':run.request_id,'conversation_id':run.conversation_id,'run_id':run.id,
        'stage':stage,**metadata}
    def emit(event,**details):
        logger.info('chat_conversation_stage %s',json.dumps({**record,'event':event,
            'elapsed_ms':round((perf_counter()-started)*1000,2),**details}))
    emit('started')
    try:
        yield
    except (Exception,asyncio.CancelledError) as error:
        emit('failed',**_error_metadata(error))
        raise
    else:
        emit('completed')


class ConversationService:
    """Persist turns and bind deterministic proposal writes to an owned run.

    Agent state is memory only; every turn loads fresh domain permissions.
    """
    def __init__(self, session_factory, *, plan_service, engine, timeout_seconds=90, execution_service=None):
        self.sessions=session_factory
        self.plan_service=plan_service
        self.engine=engine
        self.execution_service=execution_service
        self.timeout_seconds=timeout_seconds
        self.repository=ConversationRepository(session_factory)
    def ensure_conversation(self, experiment_id=None):
        scope=str(experiment_id) if experiment_id else 'global'
        if experiment_id: self.plan_service.get_experiment(UUID(str(experiment_id)))
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row=session.scalar(select(ConversationModel).where(ConversationModel.scope==scope))
            if row is None:
                row=ConversationModel(id=str(uuid4()),scope=scope,experiment_id=str(experiment_id) if experiment_id else None,
                    title='实验对话' if experiment_id else '全局对话',state_json='{}')
                session.add(row)
                session.flush()
            conversation_id=row.id
        return self.get_conversation(conversation_id)
    def list_conversations(self):
        with self.sessions() as session:
            return [{'id':r.id,'experiment_id':r.experiment_id,'title':r.title}
                for r in session.scalars(select(ConversationModel).where(
                    (ConversationModel.scope=='global') | (ConversationModel.scope==ConversationModel.experiment_id)
                ).order_by(ConversationModel.id))]
    def _hydrate(self, message, experiment_id):
        blocks=[]
        for block in message['blocks']:
            if block['type'] in {'proposal','proposal_ref'} and experiment_id:
                proposal_id=block.get('id') or block['proposal']['id']
                proposal=self.plan_service.get_proposal(UUID(experiment_id),UUID(str(proposal_id)))
                blocks.append({'type':'proposal','proposal':ProposalResponse.model_validate(proposal).model_dump(mode='json')})
            elif block['type'] in {'text','execution','experiment_created'}: blocks.append(block)
        return {**message,'blocks':blocks}
    def get_conversation(self, conversation_id):
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row=self.repository.require(session,conversation_id)
            if row.scope!='global' and row.scope!=row.experiment_id:
                raise ExperimentNotFoundError('conversation not found')
            self.repository.recover(session,row)
            session.flush()
            messages=[self.repository.serialize(m) for m in session.scalars(select(ConversationMessageModel).where(
                ConversationMessageModel.conversation_id==row.id).order_by(ConversationMessageModel.sequence))]
            detail={'id':row.id,'experiment_id':row.experiment_id,'title':row.title,'messages':messages}
        detail['messages']=[self._hydrate(m,detail['experiment_id']) for m in messages]
        return detail
    def _context(self, experiment_id):
        if not experiment_id: return None
        with self.sessions() as session:
            experiment=session.get(ExperimentModel,experiment_id)
            revision=session.get(PlanRevisionModel,experiment.current_revision_id)
            tasks=session.scalars(select(DailyTaskModel).where(DailyTaskModel.experiment_id==experiment_id)).all()
            return {'experiment_id':experiment_id,'revision_id':revision.id,'revision':revision.number,
                'spec':json.loads(revision.spec_json), 'read_only':bool(experiment.status=='ready' or revision.is_frozen or tasks),
                'tasks':[{'id':t.id,'status':t.status,'year':t.year,'doy':t.doy} for t in tasks]}
    async def send_message(self, conversation_id, message, client_request_id, *, execution_preview_id=None):
        self.get_conversation(conversation_id)  # Commit recovery even on replay conflict.
        run,replay=self.repository.claim(conversation_id,message,client_request_id,self.timeout_seconds,
            execution_preview_id=execution_preview_id)
        if replay: return replay
        if run.status=='failed': raise ModelProposalError(category=run.error_category,request_id=run.request_id)
        started=perf_counter()
        generation=None
        try:
            with _stage(run,'plan_load'):
                detail=self.get_conversation(conversation_id)
                context=self._context(detail['experiment_id'])
                history=[{'role':m['role'],'text':'\n'.join(
                    b['text'] if b['type']=='text' else 'Server experiment creation receipt: '+json.dumps(b,ensure_ascii=False)
                    for b in m['blocks'] if b['type'] in {'text','experiment_created'})}
                    for m in detail['messages'][:-1]][-40:]
                with self.sessions() as session:
                    try:
                        persisted=json.loads(session.get(ConversationModel,str(conversation_id)).state_json)
                    except (ValueError,TypeError):
                        persisted={}
                state=persisted.get('data') if isinstance(persisted,dict) and persisted.get('version')==1 else None
                if not isinstance(state,dict): state=None
            if is_execution_approval(message):
                with _stage(run,'execution_confirmation',preview_attached=execution_preview_id is not None):
                    return self._confirm_execution(run,context,execution_preview_id,started)
            if context is not None:
                context['execution']={'chat_confirmation_supported':self.execution_service is not None,
                    'preview_id':str(execution_preview_id) if execution_preview_id else None,
                    'requires_explicit_approval':True}
            proposed=False
            saved_proposal=None
            creation_attempted=False
            staged_creation=None
            defaults=default_experiment_spec() if context is None else None
            async def create(candidate):
                nonlocal creation_attempted,staged_creation
                with _stage(run,'creation_validation'):
                    if context is not None or creation_attempted:
                        raise ConversationConflict('only one global experiment creation is allowed per turn')
                    creation_attempted=True
                    with self.sessions() as session:
                        self.repository.assert_owner(session,run.id)
                    validated=ExperimentSpec.model_validate(candidate)
                    staged_creation=self.plan_service.validation_service.validate(validated)
                    return {'status':'validated','pending':True,'spec':staged_creation.model_dump(mode='json')}
            async def propose(candidate, explanation):
                nonlocal proposed,saved_proposal
                with _stage(run,'proposal_validation'):
                    if proposed: raise ConversationConflict('only one proposal is allowed per turn')
                    proposed=True
                    if not context or context['read_only']: raise PlanFrozenError('experiment is read-only')
                    normalized=canonicalize_station_selection({'candidate':candidate},ExperimentSpec.model_validate(context['spec']))
                    validated=ExperimentSpec.model_validate(normalized['candidate'])
                def guard(session):
                    self.repository.assert_owner(session,run.id)
                    experiment=session.get(ExperimentModel,context['experiment_id'])
                    current=session.get(PlanRevisionModel,experiment.current_revision_id)
                    if experiment.status=='ready' or current.is_frozen or session.scalar(select(DailyTaskModel.id).where(
                        DailyTaskModel.experiment_id==experiment.id).limit(1)):
                        raise PlanFrozenError('experiment is read-only')
                    if current.id!=context['revision_id']: raise RevisionConflictError('experiment changed during turn')
                with _stage(run,'persistence'):
                    preview=self.plan_service.preview_revision(UUID(context['experiment_id']),validated,source='chat',
                        base_revision_id=UUID(context['revision_id']),explanation=explanation,
                        write_guard=guard,skip_no_change=True,conversation_run_id=run.id)
                    if preview is None: return {'proposal':None,'no_change':True}
                    saved_proposal=ProposalResponse.model_validate(preview).model_dump(mode='json')
                    return {'proposal':saved_proposal,'no_change':False}
            with _stage(run,'model_request'):
                creation_options={'create':create,'creation_defaults':defaults.model_dump(mode='json')} if context is None else {}
                generation=asyncio.create_task(self.engine.reply(message,history=history,context=context,state=state,propose=propose,**creation_options))
                done,_=await asyncio.wait((generation,),timeout=self.timeout_seconds)
                if not done: raise TimeoutError()
                result=await generation
            # Only a server callback result is authoritative; model prose cannot inject a proposal.
            blocks=[{'type':'text','text':result.text}]
            if saved_proposal: blocks.append({'type':'proposal','proposal':saved_proposal})
            with _stage(run,'persistence'),self.sessions.begin() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                self.repository.assert_owner(session,run.id)
                if staged_creation is not None:
                    experiment=self.plan_service.create_experiment(staged_creation,session=session)
                    blocks.append({'type':'experiment_created','experiment_id':str(experiment.id),
                        'status':'draft','current_revision':1,'spec':staged_creation.model_dump(mode='json')})
                # SDK state predates the server receipt. Rebuild from durable
                # history next turn so follow-up questions can cite the saved ID.
                response=self._complete(session,run,blocks,started,metrics=result.metrics,
                    state=None if staged_creation is not None else result.state)
            return response
        except asyncio.CancelledError:
            self._fail(run.id,'interrupted',None)
            raise
        except Exception as error:
            category=error.category if isinstance(error,ModelProposalError) else ('timeout' if isinstance(error,(TimeoutError,ModelTimeout))
                else 'schema' if isinstance(error,(ValidationError,PlanValidationError)) else 'provider')
            self._fail(run.id,'failed',category)
            raise ModelProposalError(category=category,request_id=run.request_id) from None
        finally:
            if generation is not None and not generation.done():
                generation.cancel()
                # Revoke ownership above before yielding to even a cancellation-suppressing SDK.
                await asyncio.wait((generation,), timeout=.25)
                if not generation.done():
                    generation.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                elif not generation.cancelled():
                    generation.exception()
    def _complete(self,session,run,blocks,started,*,metrics=None,state=None):
        owned,conversation=self.repository.assert_owner(session,run.id)
        assistant=self.repository.message(session,owned,'assistant','completed',blocks)
        user=session.scalar(select(ConversationMessageModel).where(ConversationMessageModel.run_id==run.id,
            ConversationMessageModel.role=='user'))
        response={'conversation_id':conversation.id,'run_id':run.id,'request_id':run.request_id,'status':'completed',
            'user_message':self.repository.serialize(user),'assistant_message':self.repository.serialize(assistant),
            'timings':{'total_ms':(perf_counter()-started)*1000,**(metrics or {})}}
        owned.status='completed'
        owned.response_json=json.dumps(response,ensure_ascii=False)
        conversation.active_run_id=None
        # A deterministic server reply has no SDK state. Clear the old cache so
        # the next model turn rebuilds from history including this receipt.
        conversation.state_json=json.dumps({'version':1,'data':state},ensure_ascii=False)
        self.repository.event(session,owned,'completed')
        return response

    def _confirm_execution(self,run,context,preview_id,started):
        # No model call or await may sit between approval, queueing and receipt.
        # The worker sees the approval only after this transaction commits.
        with _stage(run,'persistence'),self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            self.repository.assert_owner(session,run.id)
            receipt=None
            if context is None:
                message='请先进入要执行的实验，打开并核对 XML 预览，再明确确认开始解算。'
            elif preview_id is None:
                message='请先打开并核对本次 XML 预览，再发送“确认并开始解算”。尚未启动实验。'
            elif self.execution_service is None:
                message='当前服务尚未启用对话执行确认，请在 XML 预览中确认并开始解算。'
            else:
                try:
                    # A refusal must roll back every partial task/approval write.
                    with session.begin_nested():
                        tasks=self.execution_service.confirm(UUID(context['experiment_id']),preview_id=UUID(str(preview_id)),
                            expected_revision=context['revision'],confirmed=True,session=session)
                    receipt={'type':'execution','experiment_id':context['experiment_id'],
                        'preview_id':str(preview_id),'task_ids':[str(task.id) for task in tasks]}
                    message=f'已确认当前 XML 预览，{len(tasks)} 个日任务已入队，将由本地工作进程启动解算。请查看实验进度。'
                except (ExecutionReviewError,RevisionConflictError,TaskStateError) as error:
                    message=f'未提交本次执行确认：{error}'
            blocks=[{'type':'text','text':message}]
            if receipt: blocks.append(receipt)
            # Recheck the lease after file validation; expiry rolls back queueing.
            return self._complete(session,run,blocks,started)

    def _fail(self, run_id, status, category):
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            run=session.get(ConversationRunModel,run_id)
            conversation=session.get(ConversationModel,run.conversation_id)
            if run.status!='started' or conversation.active_run_id!=run.id: return
            if status=='interrupted':
                self.repository.interrupt(session,conversation,run)
                return
            run.status=status
            run.error_category=category
            conversation.active_run_id=None
            blocks=[{'type':'text','text':MODEL_ERROR_MESSAGES[category]}]
            if run.proposal_id: blocks.append({'type':'proposal_ref','id':run.proposal_id})
            self.repository.message(session,run,'assistant',status,blocks)
            self.repository.event(session,run,status)
