"""Bounded research reads and versioned comparisons, with run-local aliases."""
from __future__ import annotations

from copy import deepcopy
import difflib
import json
import math

from great_agent.research.materials import MaterialError, canonical, digest


_STRING={'type':'string','minLength':1,'maxLength':2000}
_ID={'type':'string','minLength':1,'maxLength':500}
_LINE={'type':'integer','minimum':1,'maximum':1000000}
_LIMIT={'type':'integer','minimum':1,'maximum':20}
_SCHEMAS={
    'list_research_materials':({},[], 'List the fixed material scope and artifact IDs; no content is evidence until read.'),
    'read_experiment_summary':({'material_id':_ID},['material_id'], 'Read the selected exact experiment task revision and attempt.'),
    'read_artifact_excerpt':({'material_id':_ID,'start_line':_LINE,'end_line':_LINE},['material_id'], 'Read up to 200 lines of an already registered, frozen JSON/CSV/XML artifact.'),
    'read_log_excerpt':({'material_id':_ID,'start_line':_LINE,'end_line':_LINE},['material_id'], 'Read a frozen log prefix, retaining original timescale and truncation.'),
    'search_code':({'query':_STRING,'limit':_LIMIT},['query'], 'Literal case-insensitive search only within selected frozen source files.'),
    'read_code_excerpt':({'material_id':_ID,'start_line':_LINE,'end_line':_LINE},['material_id'], 'Read source lines from a content snapshot; historical executable linkage remains qualified.'),
    'read_code_diff':({'before_material_id':_ID,'after_material_id':_ID},['before_material_id','after_material_id'], 'Read an unapplied textual difference between selected frozen source files.'),
    'compare_existing_metrics':({'baseline_material_id':_ID,'candidate_material_id':_ID,'metric':_ID,
        'stations':{'type':'array','items':{'type':'string','maxLength':16},'maxItems':100}},
        ['baseline_material_id','candidate_material_id','metric'], 'Compare existing analysis metrics for identical dates, reference, policy, sampling, units and scope; never recompute source data.'),
    'search_literature':({'query':_STRING,'limit':_LIMIT},['query'], 'Search only selected fixed literature versions; original locations and quality are retained.'),
    'read_literature_evidence':({'material_id':_ID,'chunk_id':_ID},['material_id','chunk_id'], 'Read an immutable original literature chunk with page coordinates and transcription quality.'),
    'load_prior_evidence':({'evidence_id':_ID},['evidence_id'], 'Reload prior evidence from a trusted source snapshot and assign an alias for this run.'),
}

# Units belong to these existing artifact schema fields, not generated values.
_METRIC_UNITS={'epoch_count':'count','valid_epoch_fraction':'fraction','fix_rate':'fraction',
    'joint_within_fraction':'fraction','rms_3d_all_epochs_m':'m','reference_sow':'s',
    'horizontal_convergence_minutes':'min','vertical_convergence_minutes':'min',
    'joint_convergence_minutes':'min','rms_all_epochs_m.east_m':'m',
    'rms_all_epochs_m.north_m':'m','rms_all_epochs_m.up_m':'m',
    'std_all_epochs_m.east_m':'m','std_all_epochs_m.north_m':'m','std_all_epochs_m.up_m':'m'}


def _error(message,code='invalid_tool_arguments'):
    raise MaterialError(code,message)


class ResearchToolset:
    def __init__(self,materials,snapshot,*,prior_evidence=None):
        self.materials,self.snapshot=materials,deepcopy(snapshot)
        self.materials.validate(self.snapshot)
        self.evidence=[]
        self._by_id={}
        prior=prior_evidence or []
        self._prior={e['id']:deepcopy(e) for e in (prior.values() if isinstance(prior,dict) else prior)}

    def definitions(self)->list[dict]:
        return [{'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':deepcopy(props),'required':list(required),'additionalProperties':False}}}
            for name,(props,required,description) in _SCHEMAS.items()]

    @staticmethod
    def _validate_arguments(name,arguments):
        if name not in _SCHEMAS:
            _error('tool is not registered','tool_forbidden')
        props,required,_=_SCHEMAS[name]
        if not isinstance(arguments,dict) or set(arguments)-set(props) or any(k not in arguments for k in required):
            _error('tool arguments do not match the registered schema')
        for key,value in arguments.items():
            schema=props[key]
            if schema['type']=='string' and (not isinstance(value,str) or not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',2000)):
                _error('invalid text or material identifier')
            if schema['type']=='integer' and (type(value) is not int or not schema['minimum']<=value<=schema['maximum']):
                _error('invalid bounded integer argument')
            if schema['type']=='array' and (not isinstance(value,list) or len(value)>schema['maxItems'] or
                    any(not isinstance(v,str) or not v or len(v)>16 for v in value)):
                _error('invalid station selection')

    def _material(self,ident,kinds=None,*,snapshot=None):
        for material in self.materials.iter_materials(snapshot or self.snapshot):
            if material['id']==ident and (kinds is None or material['kind'] in kinds):
                return material
        _error('material is outside the fixed source scope','material_forbidden')

    def _evidence(self,material,text,locator=None,*,kind=None,flags=None):
        locator={**material['locator'],'material_id':material['id'],'material_sha256':material['sha256'],**(locator or {})}
        kind=kind or material['kind']
        text_hash=digest(text)
        ident='evidence:'+digest(canonical({'kind':kind,'locator':locator,'text_sha256':text_hash}))
        evidence={'id':ident,
            'kind':kind,'title':material['title'],'text':text,'locator':locator,'sha256':text_hash,
            'quality_flags':list(dict.fromkeys(material.get('quality_flags',[])+(flags or []))),
            'available':True}
        return self._register(evidence)

    def _register(self,evidence):
        evidence=deepcopy({k:v for k,v in evidence.items() if k!='alias'})
        ident=evidence['id']
        if ident in self._by_id:
            existing={k:v for k,v in self._by_id[ident].items() if k!='alias'}
            if evidence!=existing:
                _error('immutable evidence identity has conflicting metadata','evidence_unavailable')
            return deepcopy(self._by_id[ident])
        prefix='C' if evidence['kind']=='code' else 'L' if evidence['kind']=='literature' else 'E'
        evidence['alias']=prefix+str(1+sum(e['alias'].startswith(prefix) for e in self.evidence))
        self.evidence.append(evidence)
        self._by_id[ident]=evidence
        return deepcopy(evidence)

    def _excerpt(self,material,args):
        lines=material['content'].splitlines(keepends=True)
        start=args.get('start_line',1)
        end=args.get('end_line',min(len(lines),start+99))
        if not lines and start==1 and 'end_line' not in args:
            end=1
        if start>end or end-start>=200 or start>max(1,len(lines)):
            _error('excerpt range must select at most 200 existing lines')
        end=min(end,max(1,len(lines)))
        selected=''.join(lines[start-1:end])
        text=selected[:20000]
        truncated=end<len(lines) or bool(material.get('partial')) or len(text)<len(selected)
        evidence=self._evidence(material,text,{'start_line':start,'end_line':end,
            'byte_end':material.get('byte_end'),'partial_source':bool(material.get('partial')),
            'excerpt_char_end':len(text),'excerpt_truncated':len(text)<len(selected)})
        return {'evidence':evidence,'truncated':truncated,'coverage':{'start_line':start,'end_line':end,
            'captured_lines':len(lines),'captured_bytes':material.get('size_bytes'),
            'source_bytes_at_capture':material.get('source_size_bytes')},
            'source_status':self.materials.source_status(material)}

    async def call(self,name:str,arguments:dict)->dict:
        self._validate_arguments(name,arguments)
        self.materials.validate(self.snapshot)
        args=arguments
        if name=='list_research_materials':
            return {'items':self.materials.summary(self.snapshot),'limitations':self.snapshot.get('limitations',[])}
        if name=='read_experiment_summary':
            material=self._material(args['material_id'],{'experiment'})
            return {'evidence':self._evidence(material,material['content'][:20000],
                    {'truncated':len(material['content'])>20000}),
                'artifacts':self.materials._summary(material).get('artifacts',[]),
                'truncated':len(material['content'])>20000,
                'coverage':'Exact selected task revision and attempt; registered text artifacts only.'}
        if name in {'read_code_excerpt','read_artifact_excerpt','read_log_excerpt'}:
            kinds={'read_code_excerpt':{'code'},'read_artifact_excerpt':{'artifact'},'read_log_excerpt':{'log'}}[name]
            return self._excerpt(self._material(args['material_id'],kinds),args)
        if name=='search_code':
            query=args['query'].casefold()
            if not query.strip():
                _error('search query must contain non-whitespace text')
            limit=args.get('limit',8)
            hits=[]
            for material in self.snapshot['materials']:
                if material['kind']!='code':
                    continue
                for number,line in enumerate(material['content'].splitlines(keepends=True),1):
                    if query in line.casefold():
                        hits.append((material,number,line))
            return {'items':[{'material_id':m['id'],'line':number,
                    'evidence':self._excerpt(m,{'start_line':number,'end_line':number})['evidence']}
                    for m,number,line in hits[:limit]],'truncated':len(hits)>limit,
                'coverage':'Literal search of selected frozen files only; no dynamic call graph.'}
        if name=='read_code_diff':
            before=self._material(args['before_material_id'],{'code'})
            after=self._material(args['after_material_id'],{'code'})
            lines=list(difflib.unified_diff(before['content'].splitlines(keepends=True),
                after['content'].splitlines(keepends=True),fromfile=before['title'],tofile=after['title']))
            text=''.join(lines[:200])[:20000]
            locator={'derived_type':'code_diff','before_material_id':before['id'],'before_sha256':before['sha256'],
                'after_material_id':after['id'],'after_sha256':after['sha256'],'algorithm_version':'unified_diff_v1'}
            return {'evidence':self._evidence(after,text,locator,flags=['unapplied_suggestion_only']),
                'truncated':len(lines)>200 or len(''.join(lines[:200]))>20000}
        if name=='compare_existing_metrics':
            return self._compare(args)
        if name=='search_literature':
            result=await self.materials.search_literature(self.snapshot,args['query'],args.get('limit',8))
            items=[]
            for chunk in result['items']:
                material=next(m for m in self.snapshot['materials'] if m['kind']=='literature' and m['locator']['version_id']==chunk['version_id'])
                items.append({'material_id':material['id'],'chunk_id':chunk['id'],
                    'evidence':self._literature(material,chunk),'source_status':self.materials.source_status(material)})
            return {'items':items,'mode':result['mode'],'coverage':'Selected immutable literature versions only.',
                **({'warning':result['warning']} if result.get('warning') else {})}
        if name=='read_literature_evidence':
            material=self._material(args['material_id'],{'literature'})
            chunk=next((c for c in material['chunks'] if c['id']==args['chunk_id']),None)
            if chunk is None:
                _error('chunk is outside the selected fixed literature version','material_forbidden')
            return {'evidence':self._literature(material,chunk),'source_status':self.materials.source_status(material)}
        if name=='load_prior_evidence':
            return self._load_prior(args['evidence_id'])
        _error('tool is not registered','tool_forbidden')

    def _literature(self,material,chunk):
        text=chunk['text'][:20000]
        return self._evidence(material,text,{'chunk_id':chunk['id'],'locations':chunk['locations'],
            'char_start':0,'char_end':len(text),'section':chunk['section'],
            'truncated':len(text)<len(chunk['text'])},flags=chunk['quality_flags'])

    @staticmethod
    def _metric_value(station,metric):
        value=station
        for part in metric.split('.'):
            if not isinstance(value,dict):
                return None
            value=value.get(part)
        return value if type(value) in {float,int} and math.isfinite(value) else None

    def _analysis(self,material):
        try:
            data=json.loads(material['content'],parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,KeyError):
            _error('comparison requires registered existing analysis JSON')
        if not isinstance(data,dict) or not isinstance(data.get('stations'),list):
            _error('comparison requires registered existing station metrics')
        return data

    @staticmethod
    def _pointer(data,pointer):
        value=data
        for part in pointer.lstrip('/').split('/'):
            part=part.replace('~1','/').replace('~0','~')
            value=value[int(part)] if isinstance(value,list) else value[part]
        return value

    def _metric_input(self,material,data,stations,metric):
        pointers=['/'+key for key in ('schema_version','policy','statistics','reference_method','units') if key in data]
        for index,station in enumerate(data['stations']):
            if not isinstance(station,dict) or station.get('station') not in stations:
                continue
            for field in ('station','reference_sha256','reference_sow'):
                if field in station:
                    pointers.append(f'/stations/{index}/{field}')
            pointer=f'/stations/{index}/'+metric.replace('.','/')
            try:
                self._pointer(data,pointer)
                pointers.append(pointer)
            except (KeyError,IndexError,TypeError):
                pass
        text=canonical({pointer:self._pointer(data,pointer) for pointer in pointers})
        if len(text)>20000:
            _error('selected comparison context is too large; narrow the station scope')
        return self._evidence(material,text,{'json_pointers':pointers,'extraction':'original_json_values_v1'})

    def _compare(self,args):
        baseline=self._material(args['baseline_material_id'],{'artifact'})
        candidate=self._material(args['candidate_material_id'],{'artifact'})
        metric=args['metric']
        if metric not in _METRIC_UNITS:
            _error('metric is not a supported existing artifact field')
        a,b=self._analysis(baseline),self._analysis(candidate)
        unit_a=a.get('units',{}).get(metric,_METRIC_UNITS[metric])
        unit_b=b.get('units',{}).get(metric,_METRIC_UNITS[metric])
        reasons=[]
        for field in ('policy','statistics','reference_method','schema_version'):
            if a.get(field) is None or b.get(field) is None or a.get(field)!=b.get(field):
                reasons.append(f'{field} missing or different')
        if unit_a!=unit_b or unit_a!=_METRIC_UNITS[metric]:
            reasons.append('metric units missing or different from the registered schema')
        for field in ('year','doy'):
            if baseline['locator'].get(field)!=candidate['locator'].get(field):
                reasons.append(f'{field} differs')
        # Comparing scenarios is intentional. Sampling and policy must still be
        # identical; configuration identity is recorded rather than invented.
        left={s.get('station'):s for s in a['stations'] if isinstance(s,dict) and s.get('station')}
        right={s.get('station'):s for s in b['stations'] if isinstance(s,dict) and s.get('station')}
        stations=sorted(set(left)&set(right))
        if args.get('stations'):
            stations=[s for s in stations if s in args['stations']]
        if not stations:
            reasons.append('no common selected station/date pairs')
        for station in stations:
            for field in ('reference_sha256','reference_sow'):
                if left[station].get(field) is None or left[station].get(field)!=right[station].get(field):
                    reasons.append(f'{station}: {field} missing or different')
        inputs=[self._metric_input(m,data,stations,metric) for m,data in ((baseline,a),(candidate,b))]
        rows=[]
        if not reasons:
            for station in stations:
                old,new=self._metric_value(left[station],metric),self._metric_value(right[station],metric)
                missing=old is None or new is None
                delta=None if missing else new-old
                rows.append({'station':station,'year':baseline['locator']['year'],'doy':baseline['locator']['doy'],
                    'baseline':old,'candidate':new,'delta':delta,'unit':unit_a,'missing':missing,
                    'relative_change':None if missing or old==0 else delta/abs(old)})
        result={'compatible':not reasons,'incompatibilities':list(dict.fromkeys(reasons)),'metric':metric,
            'rows':rows,'algorithm_version':'existing_metrics_v1','missing_policy':'missing values remain null; never zero-filled',
            'unpaired_stations':sorted(set(left)^set(right)),
            'limitations':['Reference endpoints are not independent ground truth.',
                'Configuration differences and binary provenance require separate interpretation.']}
        evidence=self._evidence(candidate,canonical(result),{'derived_type':'metric_comparison',
            'algorithm_version':'existing_metrics_v1','metric':metric,'parameters':deepcopy(args),
            'input_evidence_ids':[e['id'] for e in inputs],
            'input_materials':[{'id':m['id'],'sha256':m['sha256']} for m in (baseline,candidate)]},kind='derived')
        return {**result,'evidence':evidence}

    def _load_prior(self,ident):
        prior=self._prior.get(ident)
        if not prior or not prior.get('available',True) or digest(prior.get('text',''))!=prior.get('sha256'):
            _error('prior evidence is unavailable or failed integrity validation','evidence_unavailable')
        snapshot=self.snapshot
        self.materials.validate(snapshot)
        # Prior scope cannot widen this run. Source identity/version and code
        # paths must remain selected even when a trusted older copy is supplied.
        loc=prior.get('locator',{})
        material=self._material(loc.get('material_id'),snapshot=snapshot)
        current=self._material(loc.get('material_id'))
        if material['sha256']!=loc.get('material_sha256'):
            _error('prior evidence source version is not selected','evidence_unavailable')
        if current['kind']!=material['kind']:
            _error('prior evidence source is outside current scope','evidence_unavailable')
        reloaded_inputs=[]
        if loc.get('derived_type'):
            # Reload the deterministic computation and every input, rather than
            # granting old model calculations evidence status.
            if loc['derived_type']=='metric_comparison':
                temporary=ResearchToolset(self.materials,snapshot)
                recomputed=temporary._compare(loc['parameters'])['evidence']
                reloaded_inputs=[e for e in temporary.evidence if e['id']!=recomputed['id']]
            elif loc['derived_type']=='code_diff':
                before=self._material(loc['before_material_id'],{'code'},snapshot=snapshot)
                after=self._material(loc['after_material_id'],{'code'},snapshot=snapshot)
                if before['sha256']!=loc['before_sha256'] or after['sha256']!=loc['after_sha256']:
                    _error('prior code difference source version is not selected','evidence_unavailable')
                text=''.join(list(difflib.unified_diff(before['content'].splitlines(keepends=True),
                    after['content'].splitlines(keepends=True),fromfile=before['title'],tofile=after['title']))[:200])[:20000]
                recomputed={**prior,'text':text}
            else:
                _error('unknown prior derived evidence','evidence_unavailable')
            expected=recomputed['text']
        elif loc.get('json_pointers'):
            data=self._analysis(material)
            expected=canonical({pointer:self._pointer(data,pointer) for pointer in loc['json_pointers']})
        elif material['kind']=='literature':
            chunk=next((c for c in material['chunks'] if c['id']==loc.get('chunk_id')),None)
            if chunk is None:
                _error('prior literature chunk is unavailable','evidence_unavailable')
            expected=chunk['text'][loc.get('char_start',0):loc.get('char_end',len(chunk['text']))]
        elif 'start_line' in loc:
            lines=material['content'].splitlines(keepends=True)
            expected=''.join(lines[loc['start_line']-1:loc['end_line']])[:loc.get('excerpt_char_end')]
        else:
            expected=material['content'][:20000] if loc.get('truncated') else material['content']
        if expected!=prior['text']:
            _error('prior evidence does not match its source excerpt','evidence_unavailable')
        # Use the original source locator, but allocate a new run-local alias.
        expected_id='evidence:'+digest(canonical({'kind':prior['kind'],'locator':loc,'text_sha256':digest(expected)}))
        if expected_id!=prior['id']:
            _error('prior evidence identity failed validation','evidence_unavailable')
        for source_id in loc.get('input_evidence_ids',[]):
            if source_id in self._prior and self._prior[source_id]['kind']=='artifact':
                self._load_prior(source_id)
            else:
                source=next((e for e in reloaded_inputs if e['id']==source_id),None)
                if source is None:
                    _error('prior comparison input evidence is unavailable','evidence_unavailable')
                self._register(source)
        # Preserve immutable provenance from the original capture even if HEAD,
        # display metadata or active artifact counters have since advanced.
        evidence=self._register({k:prior[k] for k in ('id','kind','title','text','locator',
            'sha256','quality_flags','available')})
        return {'evidence':evidence,'previous_alias':prior.get('alias'),
            'source_status':self.materials.source_status(material)}
