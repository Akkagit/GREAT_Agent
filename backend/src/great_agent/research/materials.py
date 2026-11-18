"""Server-scoped, immutable research copies; this module never runs source code."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from great_agent.db.models import (DailyTaskModel, ExperimentModel, PlanRevisionModel,
    ResultFileModel, TaskAttemptModel)
from great_agent.db.knowledge_models import (KnowledgeBlobModel, KnowledgeChunkModel,
    KnowledgeDocumentModel, KnowledgeVersionModel)
from great_agent.knowledge.retrieval import KnowledgeRetriever
from great_agent.knowledge.embedding import EmbeddingUnavailable
from great_agent.knowledge.vector_index import QdrantVectorIndex


MAX_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 8 * MAX_FILE_BYTES
MAX_FILES = 200
_BLOCKED_PARTS = {'.git', '.aws', '.codex', '.agents', '.ssh', '.venv', 'venv',
    'node_modules', '__pycache__', 'config', 'data', 'runs'}
_TEXT_SUFFIXES = {'.py','.pyi','.c','.cc','.cpp','.cxx','.h','.hpp','.hxx','.f',
    '.f90','.m','.rs','.go','.java','.js','.jsx','.ts','.tsx','.css','.scss','.html',
    '.md','.txt','.json','.xml','.csv','.cmake','.sh','.ps1'}
_SECRET_NAME = re.compile(r'(^\.env($|\.)|secret|credential|(^|[._-])(token|password|private|id_rsa)([._-]|$))', re.I)
_SECRET_CONTENT = re.compile(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)|\b(?:api[_-]?key|password|access[_-]?token)[\"\']?\s*[:=]\s*[\"\'][^\"\'\n]{8,}[\"\']', re.I)


class MaterialError(ValueError):
    """Safe boundary error, compatible with ordinary input validation handlers."""
    def __init__(self, code, message, status_code=422):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.details = []


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode('utf-8')).hexdigest()


def _manifest(snapshot):
    return digest(canonical({k:v for k,v in snapshot.items() if k != 'manifest_sha256'}))


def _fail(code, message):
    raise MaterialError(code,message)


def _no_links(path,root):
    if not path.is_relative_to(root) or path==root:
        _fail('material_forbidden','material path is outside its registered source')
    current=path
    while True:
        if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):
            _fail('material_forbidden','links are not allowed in research sources')
        try:
            info=current.lstat()
            if getattr(info,'st_file_attributes',0)&0x400 or (current==path and current.is_file() and info.st_nlink>1):
                _fail('material_forbidden','links are not allowed in research sources')
        except FileNotFoundError:
            pass
        except PermissionError:
            _fail('material_forbidden','source access is no longer permitted')
        if current==root:
            break
        current=current.parent
    if not path.resolve().is_relative_to(root.resolve()):
        _fail('material_forbidden','material path is outside its registered source')


def _read_stable(path, *, prefix=False):
    """Read twice with metadata fences so mutable sources cannot mix versions."""
    try:
        before=path.stat()
        with path.open('rb') as stream:
            data=stream.read(MAX_FILE_BYTES+1)
        partial=len(data)>MAX_FILE_BYTES
        if partial and not prefix:
            _fail('material_too_large','selected text file exceeds the 1 MiB limit')
        data=data[:MAX_FILE_BYTES]
        with path.open('rb') as stream:
            second=stream.read(len(data))
        after=path.stat()
        if data!=second or (not prefix and (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)):
            _fail('material_changed','material changed while capturing its immutable content')
        partial=partial or after.st_size>len(data)
        try:
            text=data.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            if not prefix or not partial or error.reason!='unexpected end of data':
                raise
            data=data[:error.start]
            text=data.decode('utf-8-sig')
        if '\x00' in text:
            _fail('material_forbidden','binary content is not a research text source')
        if _SECRET_CONTENT.search(text):
            _fail('material_forbidden','selected source contains credential material')
        return {'content':text,'sha256':digest(data),'size_bytes':len(data),
            'source_size_bytes':after.st_size,'partial':partial,'byte_end':len(data),
            'content_sha256':digest(text)}
    except (OSError,UnicodeError):
        _fail('material_unavailable','selected source file is unavailable or not UTF-8 text')


def _git_commit(root):
    """Read only Git identity metadata; never invoke Git or inspect credentials."""
    try:
        git=Path(root)/'.git'
        if git.is_file():
            pointer=git.read_text(encoding='utf-8')[:4096].strip()
            if not pointer.startswith('gitdir: '):
                return None
            git=(Path(root)/pointer[8:]).resolve()
        head=(git/'HEAD').read_text(encoding='ascii').strip()
        if re.fullmatch(r'[0-9a-fA-F]{40,64}',head):
            return head.lower()
        if head.startswith('ref: ') and re.fullmatch(r'refs/[\w./-]+',head[5:]) and '..' not in PurePosixPath(head[5:]).parts:
            ref=head[5:]
            for metadata in (git,git.parent.parent if git.parent.name=='worktrees' else git):
                value=metadata/ref
                if value.is_file():
                    commit=value.read_text(encoding='ascii').strip()
                    if re.fullmatch(r'[0-9a-fA-F]{40,64}',commit):
                        return commit.lower()
                packed=metadata/'packed-refs'
                if packed.is_file() and packed.stat().st_size<=MAX_FILE_BYTES:
                    for line in packed.read_text(encoding='ascii').splitlines():
                        fields=line.split()
                        if len(fields)==2 and fields[1]==ref and re.fullmatch(r'[0-9a-fA-F]{40,64}',fields[0]):
                            return fields[0].lower()
    except (OSError,UnicodeError):
        pass
    return None


def _check_source_access(path):
    try:
        with path.open('rb') as stream:
            stream.read(1)
    except FileNotFoundError:
        # Retained historical text remains readable after ordinary file removal.
        pass
    except PermissionError:
        _fail('material_forbidden','source access is no longer permitted')
    except OSError:
        _fail('material_unavailable','source is not an accessible regular file')


class ResearchMaterials:
    def __init__(self,session_factory,settings,*,knowledge_service=None,retriever=None):
        self.sessions,self.settings=session_factory,settings
        self.knowledge_service,self.retriever=knowledge_service,retriever

    def _roots(self):
        research=getattr(self.settings,'research',None)
        roots=getattr(research,'code_roots',None)
        return {'project':Path(self.settings.project_root)} if roots is None else {str(k):Path(v) for k,v in roots.items()}

    def _code_path(self,source_id,path):
        root=self._roots().get(source_id)
        if root is None:
            _fail('material_forbidden','code source is no longer registered')
        if not isinstance(path,str) or not path or any(c in path for c in ('\\',':','\x00')):
            _fail('material_forbidden','invalid code path')
        relative=PurePosixPath(path)
        if relative.is_absolute() or '..' in relative.parts:
            _fail('material_forbidden','code path traversal is forbidden')
        if any(p.lower() in _BLOCKED_PARTS or _SECRET_NAME.search(p) for p in relative.parts):
            _fail('material_forbidden','secret or private source paths are forbidden')
        if relative.suffix.lower() not in _TEXT_SUFFIXES and relative.name not in {'CMakeLists.txt','Makefile'}:
            _fail('material_forbidden','only supported source text files may be selected')
        root=root.absolute()
        candidate=root.joinpath(*relative.parts)
        _no_links(candidate,root)
        return candidate

    def _artifact_path(self,path):
        root=Path(self.settings.run_root).absolute()
        candidate=Path(path).absolute()
        _no_links(candidate,root)
        if _SECRET_NAME.search(candidate.name):
            _fail('material_forbidden','secret source files are forbidden')
        return candidate

    def _knowledge_path(self,relative):
        root=Path(getattr(self.settings.knowledge,'root',None) or Path(self.settings.project_root)/'data'/'knowledge').absolute()
        if not isinstance(relative,str) or any(c in relative for c in ('\\',':','\x00')):
            _fail('material_forbidden','invalid literature original path')
        value=PurePosixPath(relative)
        if value.is_absolute() or '..' in value.parts:
            _fail('material_forbidden','invalid literature original path')
        path=root.joinpath(*value.parts)
        _no_links(path,root)
        return path

    @staticmethod
    def _hash_original(path):
        try:
            sha=hashlib.sha256()
            with path.open('rb') as stream:
                while data:=stream.read(MAX_FILE_BYTES):
                    sha.update(data)
            return sha.hexdigest()
        except PermissionError:
            _fail('material_forbidden','source access is no longer permitted')
        except OSError:
            return None

    def catalog(self):
        with self.sessions() as session:
            experiments=[]
            for experiment in session.scalars(select(ExperimentModel).order_by(ExperimentModel.created_at)):
                revision=session.get(PlanRevisionModel,experiment.current_revision_id) if experiment.current_revision_id else None
                spec=json.loads(revision.spec_json) if revision else {}
                tasks=[]
                for task in session.scalars(select(DailyTaskModel).where(DailyTaskModel.experiment_id==experiment.id)):
                    attempts=[{'id':a.id,'number':a.number,'status':a.status} for a in session.scalars(
                        select(TaskAttemptModel).where(TaskAttemptModel.task_id==task.id).order_by(TaskAttemptModel.number))]
                    tasks.append({'id':task.id,'year':task.year,'doy':task.doy,'revision_id':task.revision_id,'attempts':attempts})
                experiments.append({'id':experiment.id,'title':spec.get('title') or spec.get('name') or experiment.id,'tasks':tasks})
            documents=[]
            if getattr(self.settings.knowledge,'enabled',True):
                for doc in session.scalars(select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.enabled.is_(True))):
                    versions=[{'id':v.id,'status':v.status} for v in session.scalars(select(KnowledgeVersionModel).where(
                        KnowledgeVersionModel.document_id==doc.id)) if v.status in {'completed','completed_with_warnings','ready'}]
                    documents.append({'id':doc.id,'title':doc.filename,'base_id':doc.base_id,
                        'version_id':doc.active_version_id,'versions':versions})
        model=getattr(self.settings,'model',None)
        secret=getattr(model,'api_key','')
        configured=bool(secret.get_secret_value() if hasattr(secret,'get_secret_value') else secret)
        return {'experiments':experiments,'sources':[{'id':k,'name':k} for k in self._roots()],
            'documents':documents,'profiles':[{'id':'default','name':getattr(model,'name','Default'),
            'configured':configured,'capabilities':['read_only_research']}]}

    def _capture_experiment(self,selection):
        with self.sessions() as session:
            task=session.get(DailyTaskModel,selection.get('task_id'))
            attempt=session.get(TaskAttemptModel,selection.get('attempt_id'))
            if not task or not attempt or task.experiment_id!=selection.get('experiment_id') or attempt.task_id!=task.id:
                _fail('material_unavailable','experiment/task/attempt selection is unavailable')
            revision=session.get(PlanRevisionModel,task.revision_id)
            locator={'experiment_id':task.experiment_id,'revision_id':task.revision_id,'task_id':task.id,
                'attempt_id':attempt.id,'attempt_number':attempt.number,'artifact_version':attempt.artifact_version,
                'year':task.year,'doy':task.doy,'role':selection.get('role','comparison')}
            content=canonical({'plan':json.loads(revision.spec_json),'attempt_status':attempt.status})
            material={'id':f'experiment:{task.id}:{attempt.id}','kind':'experiment','title':f'{task.year}/{task.doy:03d} attempt {attempt.number}',
                'locator':locator,'content':content,'sha256':digest(content),'quality_flags':[], 'artifacts':[]}
            for artifact in session.scalars(select(ResultFileModel).where(ResultFileModel.task_id==task.id,
                    ResultFileModel.attempt_id==attempt.id).order_by(ResultFileModel.id)):
                path=self._artifact_path(artifact.path)
                if path.suffix.lower() not in {'.json','.csv','.log','.txt','.xml'}:
                    continue
                copied=_read_stable(path,prefix=path.suffix.lower() in {'.log','.txt'})
                material['artifacts'].append({'id':f'artifact:{artifact.id}','kind':'log' if path.suffix.lower()=='.log' or artifact.kind in {'log','stdout','stderr'} else 'artifact',
                    'title':path.name,'locator':{**locator,'artifact_id':artifact.id,'artifact_kind':artifact.kind,
                        'station':artifact.station,'filename':path.name},'quality_flags':['partial_log_prefix'] if copied['partial'] else [],**copied})
            if attempt.log_path and not any(a['title']==Path(attempt.log_path).name for a in material['artifacts']):
                path=self._artifact_path(attempt.log_path)
                copied=_read_stable(path,prefix=True)
                material['artifacts'].append({'id':f'log:{attempt.id}','kind':'log','title':path.name,
                    'locator':{**locator,'attempt_log':True,'filename':path.name},
                    'quality_flags':['partial_log_prefix'] if copied['partial'] else [],**copied})
            return material

    def _capture_literature(self,selection):
        if not getattr(self.settings.knowledge,'enabled',True):
            _fail('material_unavailable','literature source is disabled')
        with self.sessions() as session:
            doc=session.get(KnowledgeDocumentModel,selection.get('document_id'))
            version=session.get(KnowledgeVersionModel,selection.get('version_id'))
            if not doc or not doc.enabled or not version or version.document_id!=doc.id or version.status not in {'completed','completed_with_warnings','ready'}:
                _fail('material_unavailable','selected literature document/version is unavailable')
            blob=session.get(KnowledgeBlobModel,doc.blob_id)
            path=self._knowledge_path(blob.original_relpath)
            if self._hash_original(path)!=blob.sha256:
                _fail('material_unavailable','literature original is unavailable or changed')
            locator={'base_id':doc.base_id,'document_id':doc.id,'version_id':version.id,
                'original_sha256':blob.sha256,'version_fingerprint':version.fingerprint}
            chunks=[]
            for chunk in session.scalars(select(KnowledgeChunkModel).where(KnowledgeChunkModel.version_id==version.id).order_by(KnowledgeChunkModel.ordinal)):
                chunks.append({'id':chunk.id,'document_id':doc.id,'version_id':version.id,'text':chunk.text,
                    'locations':json.loads(chunk.locations_json),'quality_flags':json.loads(chunk.quality_flags_json),
                    'kind':chunk.kind,'section':chunk.section,'element_ids':json.loads(chunk.element_ids_json),
                    'sha256':digest(chunk.text)})
            return {'id':f'literature:{doc.id}:{version.id}','kind':'literature','title':doc.filename,
                'locator':locator,'chunks':chunks,'sha256':digest(canonical(chunks)),
                'quality_flags':json.loads(version.warnings_json)}

    def _capture_historical_code(self,selection):
        from great_agent.research.errors import ResearchError
        from great_agent.research.repository import ResearchRepository

        selectors=('snapshot_run_id','snapshot_material_id')
        if any(not isinstance(selection.get(key),str) or not 1<=len(selection[key])<=500 for key in selectors):
            _fail('invalid_materials','historical code requires both snapshot run and material identifiers')
        paths=selection['paths']
        if len(paths)!=1:
            _fail('invalid_materials','historical code must select one exact source path')
        # Apply the current registry and path policy before reading any retained
        # data. Browser selections can only name persisted server snapshots.
        self._code_path(selection['source_id'],paths[0])
        try:
            original=ResearchRepository(self.sessions).get_run_snapshot(selection['snapshot_run_id'])
        except ResearchError:
            _fail('material_unavailable','selected historical research snapshot is unavailable')
        self.validate(original)
        material=next((m for m in original['materials']
            if m['id']==selection['snapshot_material_id'] and m['kind']=='code'),None)
        if material is None:
            _fail('material_unavailable','selected code material is not present in the historical snapshot')
        locator=material['locator']
        if locator.get('source_id')!=selection['source_id'] or PurePosixPath(locator.get('path',''))!=PurePosixPath(paths[0]):
            _fail('material_forbidden','historical code does not match the selected registered source and path')
        if 'provenance' in selection and selection['provenance']!=locator.get('provenance','unknown'):
            _fail('invalid_materials','historical code provenance must retain its original declaration')
        return deepcopy(material)

    def capture(self,selection:list[dict])->dict:
        if not isinstance(selection,list) or len(selection)>50:
            _fail('invalid_materials','materials must be an array with at most 50 selections')
        captured=[]
        for item in selection:
            if not isinstance(item,dict):
                _fail('invalid_materials','each material selection must be an object')
            kind=item.get('kind')
            allowed={'experiment':{'kind','experiment_id','task_id','attempt_id','role'},
                'code':{'kind','source_id','paths','provenance','snapshot_run_id','snapshot_material_id'},
                'literature':{'kind','document_id','version_id'}}
            if not isinstance(kind,str) or kind not in allowed or set(item)-allowed[kind]:
                _fail('invalid_materials','unsupported material selection')
            identifiers={'experiment':('experiment_id','task_id','attempt_id'),
                'code':('source_id',),'literature':('document_id','version_id')}[kind]
            if any(not isinstance(item.get(key),str) or not 1<=len(item[key])<=500 for key in identifiers):
                _fail('invalid_materials','material identifiers must be nonempty strings')
            if 'role' in item and (not isinstance(item['role'],str) or len(item['role'])>80):
                _fail('invalid_materials','invalid material comparison role')
            if kind=='experiment':
                captured.append(self._capture_experiment(item))
            elif kind=='literature':
                captured.append(self._capture_literature(item))
            else:
                provenance=item.get('provenance','unknown')
                if not isinstance(provenance,str) or provenance not in {'unknown','user_declared'}:
                    _fail('invalid_materials','code provenance requires a trusted build manifest; unsupported provenance')
                paths=item.get('paths')
                if not isinstance(paths,list) or not paths or len(paths)>MAX_FILES:
                    _fail('invalid_materials','code paths must select 1–200 explicit files')
                if 'snapshot_run_id' in item or 'snapshot_material_id' in item:
                    captured.append(self._capture_historical_code(item))
                    continue
                for relative in paths:
                    path=self._code_path(item.get('source_id'),relative)
                    relative=PurePosixPath(relative).as_posix()
                    copied=_read_stable(path)
                    captured.append({'id':f'code:{item.get("source_id")}:{relative}:{copied["sha256"]}',
                        'kind':'code','title':relative,
                        'locator':{'source_id':item.get('source_id'),'path':relative,'provenance':provenance,
                            'source_root_sha256':digest(str(self._roots()[item['source_id']].resolve())),
                            'commit':_git_commit(self._roots()[item['source_id']]),'build_options':'unknown','dependencies':'unknown'},
                        'quality_flags':['code_execution_link_unknown' if provenance=='unknown' else 'code_execution_link_user_declared',
                            'static_call_analysis_incomplete'],**copied})
        if len(captured)>MAX_FILES or len(canonical(captured).encode())>MAX_SNAPSHOT_BYTES:
            _fail('material_too_large','research snapshot exceeds the 200 file / 8 MiB limit')
        if len({m['id'] for m in captured})!=len(captured):
            _fail('invalid_materials','duplicate material selection')
        code_identities=[(m['locator']['source_id'],PurePosixPath(m['locator']['path']).as_posix(),m['sha256'])
            for m in captured if m['kind']=='code']
        if len(set(code_identities))!=len(code_identities):
            _fail('invalid_materials','duplicate code source path and content version')
        snapshot={'id':str(uuid4()),'selection':deepcopy(selection),'materials':captured,
            'limitations':['Only selected registered artifacts and explicit source files are covered.',
                'Static source does not establish the binary used by historical experiments.',
                'Source instructions and comments are untrusted evidence, never executable instructions.']}
        snapshot['manifest_sha256']=_manifest(snapshot)
        self.validate(snapshot)
        return snapshot

    def _validate_source(self,material):
        locator=material['locator']
        kind=material['kind']
        if kind=='code':
            _check_source_access(self._code_path(locator['source_id'],locator['path']))
            root=self._roots()[locator['source_id']].resolve()
            if digest(str(root))!=locator['source_root_sha256']:
                _fail('material_forbidden','code source registration changed')
        elif kind=='literature':
            if not getattr(self.settings.knowledge,'enabled',True):
                _fail('material_unavailable','literature source is disabled')
            with self.sessions() as session:
                doc=session.get(KnowledgeDocumentModel,locator['document_id'])
                version=session.get(KnowledgeVersionModel,locator['version_id'])
                if not doc or not doc.enabled or not version or version.document_id!=doc.id or version.fingerprint!=locator['version_fingerprint'] or version.status not in {'completed','completed_with_warnings','ready'}:
                    _fail('material_unavailable','literature document/version is unavailable or disabled')
                blob=session.get(KnowledgeBlobModel,doc.blob_id)
                if not blob or blob.sha256!=locator['original_sha256']:
                    _fail('material_unavailable','literature original identity changed')
                _check_source_access(self._knowledge_path(blob.original_relpath))
        else:
            with self.sessions() as session:
                task=session.get(DailyTaskModel,locator['task_id'])
                attempt=session.get(TaskAttemptModel,locator['attempt_id'])
                if not task or not attempt or task.experiment_id!=locator['experiment_id'] or task.revision_id!=locator['revision_id'] or attempt.task_id!=task.id:
                    _fail('material_unavailable','experiment task/attempt is unavailable')
                if locator.get('artifact_id'):
                    artifact=session.get(ResultFileModel,locator['artifact_id'])
                    if not artifact or artifact.task_id!=task.id or artifact.attempt_id!=attempt.id:
                        _fail('material_unavailable','registered experiment artifact is unavailable')
                    _check_source_access(self._artifact_path(artifact.path))
                elif locator.get('attempt_log'):
                    if not attempt.log_path:
                        _fail('material_unavailable','attempt log registration is unavailable')
                    _check_source_access(self._artifact_path(attempt.log_path))

    def validate(self,snapshot:dict)->None:
        try:
            if _manifest(snapshot)!=snapshot['manifest_sha256']:
                _fail('material_changed','research snapshot manifest integrity failed')
            for material in self.iter_materials(snapshot):
                self._validate_source(material)
                if 'content' in material and digest(material['content'])!=material.get('content_sha256',material['sha256']):
                    _fail('material_changed','research snapshot content integrity failed')
                for chunk in material.get('chunks',[]):
                    if digest(chunk['text'])!=chunk['sha256']:
                        _fail('material_changed','research literature content integrity failed')
        except (KeyError,TypeError):
            _fail('invalid_materials','invalid research snapshot')

    @staticmethod
    def iter_materials(snapshot):
        for material in snapshot['materials']:
            yield material
            yield from material.get('artifacts',[])

    def source_status(self,material):
        """Original availability differs from permission to view retained excerpts."""
        self._validate_source(material)
        locator=material['locator']
        if material['kind']=='code':
            path=self._code_path(locator['source_id'],locator['path'])
        elif material['kind']=='literature':
            with self.sessions() as session:
                doc=session.get(KnowledgeDocumentModel,locator['document_id'])
                path=self._knowledge_path(session.get(KnowledgeBlobModel,doc.blob_id).original_relpath)
        elif locator.get('artifact_id') or locator.get('attempt_log'):
            with self.sessions() as session:
                registered=session.get(ResultFileModel,locator['artifact_id']).path if locator.get('artifact_id') else session.get(TaskAttemptModel,locator['attempt_id']).log_path
                path=self._artifact_path(registered)
        else:
            return {'original_available':True,'original_matches_snapshot':True}
        original=self._hash_original(path)
        expected=locator.get('original_sha256',material.get('sha256'))
        return {'original_available':original is not None,'original_matches_snapshot':original==expected,
            'retained_text_available':True}

    def summary(self,snapshot:dict)->list[dict]:
        return [self._summary(material) for material in snapshot['materials']]

    def _summary(self,material):
        summary={k:deepcopy(material[k]) for k in ('id','kind','title','locator','sha256','quality_flags')}
        summary['material_id']=material['id']
        summary['available']=True
        summary['size_bytes']=material.get('size_bytes',sum(len(c['text'].encode()) for c in material.get('chunks',[])))
        if 'artifacts' in material:
            summary['artifacts']=[self._summary(a) for a in material['artifacts']]
        if 'chunks' in material:
            summary['chunk_count']=len(material['chunks'])
        return summary

    async def search_literature(self,snapshot,query,limit):
        # The legacy retriever's repository expects a live knowledge-chat binding.
        # A fixed research adapter preserves its ranking without creating a chat.
        adapter=_ResearchLiteratureRepository(self,snapshot)
        dense=getattr(self.retriever,'dense',None)
        retriever=KnowledgeRetriever(adapter,SimpleNamespace(embedding_enabled=False),
            Path(self.settings.project_root),dense=_BorrowedDense(dense) if dense is not None else None)
        if dense is None and getattr(self.settings.knowledge,'embedding_enabled',False):
            retriever._unavailable=True
        try:
            fixed={**snapshot,'document_versions':[m['locator'] for m in snapshot['materials'] if m['kind']=='literature']}
            return await retriever.search(fixed,query,limit)
        finally:
            await retriever.close()


class _BorrowedDense:
    """Borrow shared ready indices without starting legacy chat-scoped builds."""
    def __init__(self,dense):
        self.dense=dense

    async def search(self,snapshot,query,limit=20):
        if not isinstance(self.dense,QdrantVectorIndex):
            return await self.dense.search(snapshot,query,limit=limit)
        index=self.dense
        if index._closed or index._client is None:
            raise EmbeddingUnavailable('Semantic index is not open; keyword retrieval was used without building a legacy chat index.')
        generations=[]
        for source in snapshot['document_versions']:
            version=source['version_id']
            generation=index._ready.get(version) or await index._run(index._generation,version)
            if not generation:
                raise EmbeddingUnavailable('Selected fixed-version semantic index is not ready; keyword retrieval was used without creating a chat binding.')
            generations.append(generation)
        return await index._run(index._search,generations,query,limit) if generations else []

    async def close(self):
        # The knowledge subsystem owns the shared embedding and worker lifetime.
        pass


class _ResearchLiteratureRepository:
    def __init__(self,materials,snapshot):
        self.materials,self.snapshot=materials,snapshot
        self.chunks={c['id']:{**c,'title':m['title']} for m in snapshot['materials'] if m['kind']=='literature' for c in m['chunks']}

    def revalidate_snapshot(self,snapshot):
        self.materials.validate(self.snapshot)

    def read_snapshot_chunk(self,snapshot,chunk_id):
        return self.chunks.get(chunk_id)

    def search_snapshot(self,snapshot,query,limit=20):
        terms=re.findall(r'\w+',query.casefold())
        ranked=[(sum(c['text'].casefold().count(t) for t in terms),c) for c in self.chunks.values()]
        ranked.sort(key=lambda row:(-row[0],row[1]['id']))
        return {'items':[c for score,c in ranked if score][:limit]}
