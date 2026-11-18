"""Read-only knowledge turns; all evidence and model requests remain auditable."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import logging
import re
import time

import httpx
from sqlalchemy import select, text

from great_agent.adapters.model_provider import ModelProposalError, ModelTimeout
from great_agent.db.conversation_models import ConversationRunModel
from great_agent.db.knowledge_chat_repository import (
    KnowledgeChatConflict, KnowledgeChatScopeChanged, KnowledgeChatValidationError,
)
from great_agent.knowledge.formula_recognition import FormulaRecognitionError, is_formula_question

logger=logging.getLogger(__name__)
_SAFE_FAILURE_CODES={
    'knowledge evidence budget exceeded; narrow the question':'evidence_budget',
    'knowledge context budget exceeded; narrow the question':'context_budget',
    'model request limit exceeded':'request_budget',
    'knowledge model returned an invalid or ungrounded answer':'answer_schema',
    'knowledge answer or citations failed validation':'answer_schema',
    'knowledge model response was refused or incomplete':'refused_or_truncated',
    'knowledge model request failed':'model_exception',
    'tool call limit exceeded':'tool_budget',
    'knowledge model provider request failed':'provider_http',
    'knowledge evidence could not be read':'evidence_read',
    'knowledge scope validation failed before model request':'scope_check',
}


class KnowledgeChatService:
    def __init__(self, repository, retriever, engine, model_settings, *, timeout_seconds=90, formula_service=None):
        self.repository, self.retriever, self.engine = repository, retriever, engine
        self.model_settings = model_settings
        self.formula_service = formula_service
        if formula_service is not None:
            if getattr(formula_service, 'allow_remote', False):
                raise ValueError('knowledge chat formula helper must remain local')
            self.repository.formula_validator = formula_service.validate_transcriptions
        self.timeout_seconds = min(timeout_seconds, 90)
        self._active = {}
        self._pending = set()
        self._closed = False
        self._probe_lock = asyncio.Lock()

    def model_profiles(self):
        return [{'id':'default', 'name':self.model_settings.name,
                 'configured':bool(self.model_settings.api_key.get_secret_value().strip()),
                 'location':'cloud', 'provider':'OpenAI-compatible',
                 'capabilities':['tool_calling','citations']}]

    def validate_profile(self, identifier):
        if identifier != 'default':
            raise KnowledgeChatValidationError('unknown server model profile')

    def _formula_region_context(self, snapshot, question):
        """Resolve explicit, unique equation numbers using scoped layout metadata."""
        if self.formula_service is None or not is_formula_question(question):
            return [], []
        numbers=list(dict.fromkeys(re.findall(r'[（(]\s*([1-9][0-9]{0,2})\s*[）)]',question)))
        numbers.extend(number for number in re.findall(
            r'(?:equations?|formulas?|公式|方程|式)\s*([1-9][0-9]{0,2})(?!\d)',question,re.I) if number not in numbers)
        if not numbers:
            return [], []
        sources={source['version_id']:source for source in snapshot['document_versions']}
        matches={number:[] for number in numbers}
        selected=[]; followups=[]
        from great_agent.db.knowledge_models import KnowledgeVersionModel
        with self.repository.sessions() as session:
            self.repository._revalidate(session,snapshot)
            # Read layout metadata only. Chunk text is read only after one
            # overlapping parent has been selected on the requested page.
            versions=session.execute(select(KnowledgeVersionModel.id,KnowledgeVersionModel.metadata_json)
                .where(KnowledgeVersionModel.id.in_(sources)))
            for version_id,encoded in versions:
                try:
                    metadata=json.loads(encoded)
                    regions=metadata.get('equation_regions',[])
                    if not isinstance(regions,list):
                        raise ValueError('invalid formula metadata')
                except (ValueError,TypeError,AttributeError):
                    raise KnowledgeChatValidationError('formula metadata unavailable') from None
                for region in regions:
                    if self.formula_service._valid_region(region) and region['equation_number'] in matches:
                        matches[region['equation_number']].append((sources[version_id],region))
            for number in numbers:
                if len(matches[number]) != 1:
                    continue
                source,region=matches[number][0]
                rows=session.execute(text('''SELECT c.id,c.locations_json,c.ordinal FROM knowledge_chunks c
                    WHERE c.document_id=:document AND c.version_id=:version AND EXISTS (
                        SELECT 1 FROM json_each(c.locations_json) AS location
                        WHERE json_extract(location.value,'$.page_index')=:page)
                    ORDER BY c.ordinal,c.id'''),{'document':source['document_id'],
                        'version':source['version_id'],'page':region['page_index']})
                best=None; best_area=0; following=[]
                a,b,c,d=region['bbox']
                for identifier,locations_json,ordinal in rows:
                    for location in json.loads(locations_json):
                        if location.get('page_index') != region['page_index'] or not location.get('bbox'):
                            continue
                        x0,y0,x1,y1=location['bbox']
                        horizontal=max(0,min(c,x1)-max(a,x0))
                        area=horizontal*max(0,min(d,y1)-max(b,y0))
                        if area>best_area:
                            best,best_area=identifier,area
                        # Definitions often start beside the equation and
                        # continue below its crop. Prefer a same-column parent
                        # crossing the lower edge over the next ordinal, which
                        # may contain only one short formula line.
                        if horizontal>(c-a)/2:
                            if y0<=d<=y1:
                                following.append((0,d-y0,-horizontal,ordinal,identifier))
                            elif d<y0<=d+60:
                                following.append((1,y0-d,-horizontal,ordinal,identifier))
                if best is not None:
                    selected.append((best,source,region))
                    next_parent=next((entry[-1] for entry in sorted(following) if entry[-1]!=best),None)
                    if next_parent is not None:
                        followups.append((next_parent,source,region))
                if len(selected)>=2:
                    break
        for following in followups:
            if len(selected)>=2:
                break
            if following[0] not in {entry[0] for entry in selected}:
                selected.append(following)
        parents=[]; candidates=[]; seen=set()
        for identifier,source,region in selected:
            if identifier not in seen:
                parents.append(self.repository.read_snapshot_chunk(snapshot,identifier))
                seen.add(identifier)
            candidates.append({'document_id':source['document_id'],'version_id':source['version_id'],
                'chunk_id':identifier,'parent_chunk_id':identifier,'channel':'formula_region',
                'page_index':region['page_index'],'equation_number':region['equation_number']})
        return parents,candidates

    async def test_model(self, identifier):
        self.validate_profile(identifier)
        if not self.model_profiles()[0]['configured']:
            return {'ok':False,'message':'服务端尚未配置模型 API Key。'}
        if self._probe_lock.locked():
            raise KnowledgeChatConflict('model connectivity test is already running')
        # A synthetic probe never reads corpus content or echoes credentials/errors.
        async with self._probe_lock:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r=await client.post(str(self.model_settings.base_url).rstrip('/')+'/chat/completions',
                        headers={'Authorization':'Bearer '+self.model_settings.api_key.get_secret_value()},
                        json={'model':self.model_settings.name,'messages':[{'role':'user','content':'Reply OK.'}],
                              'max_tokens':16,'stream':False})
                    ok=r.is_success and bool(r.json().get('choices'))
            except Exception:
                ok=False
            return {'ok':ok,'message':'模型连接成功。' if ok else '模型连接失败，请检查服务端凭据、地址和模型名称。'}

    async def send_message(self, conversation_id, message, request_id):
        if self._closed:
            raise KnowledgeChatConflict('knowledge service is closing')
        cid, rid = str(conversation_id), str(request_id)
        task=asyncio.current_task()
        self._pending.add(task)
        # Finish the short claim if its caller disconnects, then revoke its owner.
        claim=asyncio.create_task(asyncio.to_thread(self.repository.claim_turn,cid,message,rid,self.timeout_seconds+5))
        try:
            run,replay,snapshot=await asyncio.shield(claim)
        except asyncio.CancelledError:
            with suppress(Exception):
                run,replay,_=await claim
                if replay is None and run.status=='started':
                    await asyncio.to_thread(self.repository.fail_turn,run.id,'interrupted','interrupted')
            raise
        finally:
            self._pending.discard(task)
        if replay is not None:
            return replay
        if run.status != 'started':
            raise ModelProposalError(category=run.error_category or 'provider',request_id=rid)
        if self._closed:
            await asyncio.to_thread(self.repository.fail_turn,run.id,'interrupted','interrupted')
            raise KnowledgeChatConflict('knowledge service is closing')
        self._active[(cid,rid)]=(run.id,task)
        started=time.monotonic()
        generation=None
        evidence=[]; by_chunk={}; queries=[]; candidates=[]; outbound=[]
        formula_run=self.formula_service.start_run(snapshot) if self.formula_service else None
        formula_by_chunk={}
        mode='lexical'; timings={}; last_budget={}

        async def persist():
            await asyncio.to_thread(self.repository.save_trace,run.id,queries=queries,candidates=candidates,
                evidence=evidence,outbound_messages=outbound,budget=last_budget,timings=timings,mode=mode)

        async def progress(stage,action):
            summary=await asyncio.to_thread(self.repository.get_retrieval_summary,run.id)
            await asyncio.to_thread(self.repository.stage,run.id,stage,{'retrieval':{**summary,'action':action}})

        def public(item):
            result={k:item[k] for k in ('source_id','evidence_text','title','quality_flags')}
            pages=[]; seen=set()
            for location in item['locations']:
                identity=(location.get('page_index'),location.get('page_label'))
                if identity not in seen and any(value is not None for value in identity):
                    seen.add(identity)
                    pages.append({key:location[key] for key in ('page_index','page_label') if key in location})
            # Bounding boxes remain local in the registry/trace/citation; the
            # answer model only needs unique page identities and source labels.
            public_result={**result,'pages':pages}
            if item.get('formula_transcriptions'):
                public_result['formula_transcriptions']=[{key:formula[key] for key in (
                    'equation_number','page_index','status')} for formula in item['formula_transcriptions']]
            return public_result

        def wire_size(value):
            # This is the same compact, Unicode JSON representation counted by
            # KnowledgeChatEngine for every tool result, including its metadata.
            return len(json.dumps(value,ensure_ascii=False,separators=(',',':')))

        tool_characters=0

        def tool_result(items,existing_source_ids,warning=None):
            nonlocal tool_characters
            remaining_requests=max(0,4-last_budget.get('physical_model_requests',0))
            result={'items':items,'existing_source_ids':list(dict.fromkeys(existing_source_ids)),
                    'mode':mode,'remaining_model_requests':remaining_requests,
                    'budget_remaining':{'tool_characters':0,
                        'registry_characters':max(0,5200-wire_size([public(e) for e in evidence]))}}
            if warning:
                result['warning']=warning
            if remaining_requests<=1:
                result['guidance']='Use the final model request to answer from existing evidence or state insufficiency. Do not request more tools.'
            # Account for the report itself. A few iterations settle the digit
            # count of the remaining-budget number without undercounting bytes.
            for _ in range(4):
                size=wire_size(result)
                remaining=max(0,12000-tool_characters-size)
                if result['budget_remaining']['tool_characters']==remaining:
                    break
                result['budget_remaining']['tool_characters']=remaining
            size=wire_size(result)
            if tool_characters+size>12000:
                raise KnowledgeChatValidationError('knowledge evidence budget exhausted; narrow the question')
            tool_characters+=size
            return result

        def boundaries(body):
            import re
            # A decimal point is never a sentence boundary. Formula chunks are
            # kept whole below; plain text may end at complete lines/sentences.
            ends={m.end() for m in re.finditer(r'\n+|(?<!\d)\.(?=\s)|[!?。！？](?=\s|$)',body)}
            return sorted({0,len(body),*ends})

        def uncovered(item):
            ident=item.get('chunk_id',item.get('id'))
            spans=sorted((value['source_spans'][0]['start'],value['source_spans'][0]['end'])
                         for value in by_chunk.get(ident,[]))
            cursor=0; gaps=[]
            for start,end in spans:
                if cursor<start: gaps.append((cursor,start))
                cursor=max(cursor,end)
            if cursor<len(item['text']): gaps.append((cursor,len(item['text'])))
            # Continue the current passage before going back to earlier context.
            return sorted(gaps,key=lambda gap:(gap[0]<cursor,gap[0]))

        def register(item,*,expand=False):
            ident=item.get('chunk_id',item.get('id'))
            previous=by_chunk.get(ident,[])
            if (previous and not expand) or len(evidence)>=8:
                return None
            body=item['text']
            if not body.strip(): return None

            def make(start,end):
                text=body[start:end]
                flags=list(item.get('quality_flags',[]))
                if start or end<len(body): flags.append('evidence_excerpt')
                value={'source_id':f'S{len(evidence)+1}','document_id':item['document_id'],
                    'version_id':item['version_id'],'chunk_id':ident,'title':item['title'],'filename':item['filename'],
                    'source_sha256':item['source_sha256'],'evidence_text':text,
                    'evidence_text_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'source_spans':[{'chunk_id':ident,'start':start,'end':end,'offset_unit':'unicode_codepoint'}],
                    'locations':item.get('locations',[]),'quality_flags':flags}
                if formula_by_chunk.get(ident):
                    value['formula_transcriptions']=formula_by_chunk[ident]
                return value

            def fits(value):
                return wire_size([public(x) for x in evidence]+[public(value)])<=5200

            def accept(value):
                evidence.append(value)
                by_chunk.setdefault(ident,[]).append(value)
                return value

            # Prefer the complete immutable parent whenever the round's registry
            # has room. No arbitrary 1400-character cut may alter a number/unit.
            if not previous and len(body)<=5200:
                full=make(0,len(body))
                if fits(full): return accept(full)
            if item.get('kind') in ('formula','equation'):
                return None
            breaks=boundaries(body)
            ranges=uncovered(item) if previous else [(0,len(body))]
            if not previous and item.get('matched_source_spans') and item.get('kind')!='table':
                target=item['matched_source_spans'][0]['start']
                ranges=[(max(point for point in breaks if point<=target),len(body))]
            for start,stop in ranges:
                if not body[start:stop].strip(): continue
                ends=[point for point in breaks if start<point<=stop]
                preferred=sorted((point for point in ends if point-start<=1400),reverse=True)
                # If one complete paragraph/row is longer, try it whole rather
                # than cutting its scientific content in the middle.
                longer=[point for point in ends if point-start>1400]
                for end in preferred+longer[:1]:
                    if not body[start:end].strip(): continue
                    value=make(start,end)
                    if fits(value): return accept(value)
            return None

        async def search(query):
            nonlocal mode
            await progress('retrieving','searching')
            result=await self.retriever.search(snapshot,query,limit=8)
            mode=result['mode']; queries.extend(result.get('queries',[query]))
            candidates.extend(result.get('candidates',[])); timings.update(result.get('timings',{}))
            ranked_items=list(result['items'])
            if formula_run is not None and is_formula_question(message):
                formula_parents,formula_candidates=await asyncio.to_thread(self._formula_region_context,snapshot,message)
                candidates.extend(formula_candidates)
                parent_ids={parent.get('chunk_id',parent.get('id')) for parent in formula_parents}
                ranked_items=formula_parents+[item for item in ranked_items
                    if item.get('chunk_id',item.get('id')) not in parent_ids]
            if ranked_items:
                import re
                top=ranked_items[0]
                explicit_table_query=bool(re.search(r'\btables?\b|\btable\s*\d+\b|表格|表\s*\d+',query,re.IGNORECASE))
                has_caption=bool(re.search(r'\btable\s*\d+\b|表\s*\d+',top.get('text',''),re.IGNORECASE))
                pages={location['page_index'] for location in top.get('locations',[])
                       if location.get('page_index') is not None}
                # A table header and its following values can be split between
                # immutable chunks. Reserve only that immediate, same-page
                # context before filling lower-ranked passages. General prose
                # queries retain their original order even if they mention a table.
                if has_caption and pages and (explicit_table_query or top.get('kind')=='table'):
                    def following_chunk():
                        from great_agent.db.knowledge_models import KnowledgeChunkModel
                        with self.repository.sessions() as session:
                            return session.scalar(select(KnowledgeChunkModel.id).where(
                                KnowledgeChunkModel.document_id==top['document_id'],
                                KnowledgeChunkModel.version_id==top['version_id'],
                                KnowledgeChunkModel.ordinal==top['ordinal']+1))
                    following_id=await asyncio.to_thread(following_chunk)
                    if following_id is not None:
                        following=await asyncio.to_thread(self.repository.read_snapshot_chunk,snapshot,following_id)
                        next_pages={location['page_index'] for location in following.get('locations',[])
                                    if location.get('page_index') is not None}
                        if pages & next_pages:
                            ranked_items=[top,following]+[item for item in ranked_items[1:]
                                if item.get('chunk_id',item.get('id'))!=following_id]
            if formula_run is not None and (is_formula_question(message) or is_formula_question(query)):
                try:
                    formula_by_chunk.update(await formula_run.enrich(
                        message if is_formula_question(message) else query,ranked_items))
                except FormulaRecognitionError:
                    pass
                # Give the verified formula's parent an evidence slot before
                # lower-ranked passages consume the round's text budget.
                ranked_items.sort(key=lambda item: item.get('chunk_id',item.get('id')) not in formula_by_chunk)
            items=[]; existing=[]; omitted=False
            for candidate in ranked_items:
                ident=candidate.get('chunk_id',candidate.get('id'))
                if ident in by_chunk:
                    existing.extend(value['source_id'] for value in by_chunk[ident])
                    continue
                value=register(candidate)
                if value is not None:
                    items.append(public(value))
                    omitted=omitted or 'evidence_excerpt' in value['quality_flags']
                else:
                    omitted=True
            await persist()
            await progress('generating','answering')
            warnings=[str(result['warning'])[:512]] if result.get('warning') else []
            if omitted:
                warnings.append('Some source content was omitted or excerpted at complete boundaries to fit the evidence budget. Narrow the question for more context.')
            return tool_result(items,existing,' '.join(warnings) or None)

        async def read(source_ids):
            if not isinstance(source_ids,list) or not source_ids or len(source_ids)>8 or any(
                    not isinstance(s,str) or s not in {e['source_id'] for e in evidence} for s in source_ids):
                raise KnowledgeChatValidationError('unknown evidence ID')
            await asyncio.to_thread(self.repository.revalidate_snapshot,snapshot)
            await progress('generating','reading_evidence')
            selected=[e for e in evidence if e['source_id'] in source_ids]
            items=[]; existing=list(source_ids); seen=set(); omitted=False
            for source in selected:
                ident=source['chunk_id']
                if ident in seen: continue
                seen.add(ident)
                parent=await asyncio.to_thread(self.repository.read_snapshot_chunk,snapshot,ident)
                gaps=uncovered(parent)
                if gaps:
                    value=register(parent,expand=True)
                    if value is not None: items.append(public(value))
                    else: omitted=True
                    continue
                # Query only adjacent IDs; never materialize an entire library's
                # text. Each resulting chunk is authorized against the same
                # fixed snapshot before its text can enter this registry.
                def neighbors():
                    from great_agent.db.knowledge_models import KnowledgeChunkModel
                    with self.repository.sessions() as session:
                        return list(session.scalars(select(KnowledgeChunkModel.id).where(
                            KnowledgeChunkModel.document_id==parent['document_id'],
                            KnowledgeChunkModel.version_id==parent['version_id'],
                            KnowledgeChunkModel.ordinal.in_([parent['ordinal']-1,parent['ordinal']+1]))
                            .order_by(KnowledgeChunkModel.ordinal)))
                for neighbor_id in await asyncio.to_thread(neighbors):
                    if neighbor_id in by_chunk:
                        existing.extend(value['source_id'] for value in by_chunk[neighbor_id])
                        continue
                    neighbor=await asyncio.to_thread(self.repository.read_snapshot_chunk,snapshot,neighbor_id)
                    value=register(neighbor)
                    if value is not None: items.append(public(value))
                    else: omitted=True
            await persist()
            warning=('Further complete source passages do not fit the evidence budget. Narrow the question.'
                     if omitted else None)
            return tool_result(items,existing,warning)

        async def on_request(messages,budget):
            nonlocal last_budget
            # Revalidate persisted prior history too: a scope may shrink mid-turn.
            await asyncio.to_thread(self.repository.revalidate_snapshot,snapshot)
            current=await asyncio.to_thread(self.repository.safe_history,cid,snapshot)
            if current != history:
                raise KnowledgeChatScopeChanged('knowledge history scope changed')
            last_budget=budget
            outbound.append({'messages':messages,'budget':budget})
            await persist()

        try:
            self.validate_profile(snapshot['model_profile_id'])
            history=await asyncio.to_thread(self.repository.safe_history,cid,snapshot)
            await progress('generating','preparing')
            generation=asyncio.create_task(self.engine.reply(message,history=history,
                search_knowledge=search,read_evidence=read,on_request=on_request))
            done,_=await asyncio.wait((generation,),timeout=self.timeout_seconds)
            if not done: raise ModelTimeout('knowledge model timeout')
            result=await generation
            await persist()
            timings.update(result.get('metrics',{}))
            timings.update(total_ms=round((time.monotonic()-started)*1000,2),retrieval_mode=mode)
            return await asyncio.to_thread(self.repository.complete_turn,run.id,answer=result['text'],
                source_ids=result['source_ids'],timings=timings,answerable=result['answerable'])
        except asyncio.CancelledError:
            await asyncio.to_thread(self.repository.fail_turn,run.id,'interrupted','interrupted')
            raise
        except Exception as error:
            scope=isinstance(error,KnowledgeChatScopeChanged)
            # Engine intentionally sanitizes callback errors; independently check scope.
            if not scope:
                try: await asyncio.to_thread(self.repository.revalidate_snapshot,snapshot)
                except KnowledgeChatScopeChanged: scope=True
            category='scope_changed' if scope else 'timeout' if isinstance(error,(TimeoutError,ModelTimeout)) else 'provider'
            logger.warning('knowledge_turn_failed run_id=%s category=%s diagnostic=%s exception_type=%s',
                run.id,category,_SAFE_FAILURE_CODES.get(str(error),'unclassified'),type(error).__name__)
            await asyncio.to_thread(self.repository.fail_turn,run.id,'interrupted' if scope else 'failed',category)
            raise ModelProposalError(category='timeout' if category=='timeout' else 'provider',request_id=rid) from None
        finally:
            self._active.pop((cid,rid),None)
            if generation is not None and not generation.done():
                generation.cancel()
                generation.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def cancel(self, conversation_id, request_id):
        cid,rid=str(conversation_id),str(request_id)
        await asyncio.to_thread(self.repository.get_conversation,cid)
        def find():
            with self.repository.sessions() as session:
                return session.scalar(select(ConversationRunModel.id).where(
                    ConversationRunModel.conversation_id==cid,ConversationRunModel.request_id==rid,
                    ConversationRunModel.status=='started'))
        run_id=await asyncio.to_thread(find)
        if not run_id: return {'status':'inactive','request_id':rid}
        await asyncio.to_thread(self.repository.fail_turn,run_id,'interrupted','interrupted')
        active=self._active.get((cid,rid))
        if active and active[1] is not asyncio.current_task(): active[1].cancel()
        return {'status':'interrupted','request_id':rid}

    async def close(self):
        self._closed=True
        tasks=list(({task for _,task in self._active.values()} | self._pending)-{asyncio.current_task()})
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        await self.retriever.close()
        if self.formula_service is not None:
            await self.formula_service.close()
