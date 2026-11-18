export type Frequency = 'DF' | 'TF'
export type ScenarioMode = 'static' | 'kinematic'
export type AmbiguityMode = 'float' | 'fixed'

export interface Scenario {
  mode: ScenarioMode
  frequency: Frequency
  ambiguity: AmbiguityMode
  reset_hours: 0 | 4 | 8
}

export interface DateSegment {
  year: number
  start_doy: number
  end_doy: number
}

export interface YearDoy { year: number; doy: number }

export interface ExperimentSpec {
  schema_version: '1.0'
  workflow: 'pppflt'
  scenario: Scenario
  date_segments: DateSegment[]
  excluded_dates: YearDoy[]
  station_group_id: string
  stations: string[]
  input_profile_id: string
  solver_profile_id: string
  template_profile_id: string
  concurrency: number
  outputs: Array<'flt' | 'log'>
}

export interface ExperimentView {
  id: string
  status: string
  current_revision: number
  current_revision_id: string
  spec: ExperimentSpec
}

export interface CreatedExperiment {
  id: string
  status: string
  current_revision: number
  current_revision_id: string
}

export interface FieldDiff { path: string; before: unknown; after: unknown }

export interface ProposalTimings {
  plan_load_ms: number; model_request_ms: number; validation_ms: number; total_ms: number; api_total_ms: number
}

export interface Proposal {
  id: string
  source: 'card' | 'chat'
  candidate: ExperimentSpec
  explanation: string
  base_revision_id: string
  base_revision_number: number
  candidate_hash: string
  status: 'pending' | 'accepted' | 'rejected' | 'superseded' | 'stale'
  source_proposal_id: string | null
  diff: FieldDiff[]
  request_id?: string
  timings?: ProposalTimings
}

export interface Revision {
  id: string
  number: number
  spec: ExperimentSpec
  is_frozen: boolean
}

export interface MissingInput {
  kind: string
  year: number
  doy: number
  station?: string | null
  path?: string
  state?: string
}

export interface RevisionIdentity { revision_id: string; revision: number }
export interface ValidationResult extends RevisionIdentity { ok: boolean; missing: MissingInput[] }

export interface InputSnapshot { kind: string; station: string | null; path: string; state: 'present'; size_bytes: number; mtime_ns: number }
export interface ExecutionJob {
  year: number; doy: number; attempt_number: number; task_id: string | null
  snapshot_path: string; xml_path: string; xml_content: string; xml_sha256: string
  output_dir: string; cwd: string; command: string[]; inputs: InputSnapshot[]
}
export interface ExecutionPreview {
  id: string; experiment_id: string; revision: number; revision_id: string; intent: 'initial' | 'retry'; expires_at: string
  executable_path: string; executable_sha256: string; template_path: string; template_sha256: string
  spec: ExperimentSpec; jobs: ExecutionJob[]
}
export interface ExperimentSummary extends ExperimentView { task_count: number; succeeded_count: number; run_status: string }

export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export interface TaskSummary { id: string; experiment_id: string; year: number; doy: number; status: TaskStatus }
export type AnalysisStatus = 'unknown' | 'not_started' | 'running' | 'completed' | 'failed' | 'skipped'
export interface AnalysisJob {
  id: string; task_id: string; attempt_id: string; revision_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'; run_count: number
  config: Record<string, unknown>; error_message: string | null
}
export interface RecoverySummary { state: string; reason: string | null; last_checked_at: string | null; version: number }
export interface TaskAttempt { id: string; number: number; status: TaskStatus; exit_code: number | null; error_code: string | null; analysis_status?: AnalysisStatus; artifact_version?: number; recovery?: RecoverySummary }
export interface TaskDetail extends TaskSummary { active_attempt_id: string | null; attempts: TaskAttempt[] }
export interface MonitorTask extends TaskDetail { analysis_status: AnalysisStatus; artifact_version: number; recovery: RecoverySummary; next_attempt_number?: number }
export interface ExperimentMonitorSnapshot { experiment_id: string; cursor: string; tasks: MonitorTask[] }
export interface ExperimentMonitorEvent { schema_version: 1; event_id: string; experiment_id: string; task_id: string | null; attempt_id: string | null; type: string; data: Record<string, unknown> }
export interface ResultFile { station: string | null; kind: string; filename: string; size_bytes: number; url: string; plot_type?: 'enu' | 'convergence' | 'fix_rate' | null }
export interface TaskEvent { id: string; event: string; data: { status?: TaskStatus; [key: string]: unknown } }


export interface ExperimentCreatedReceipt {
  type: 'experiment_created'
  experiment_id: string
  status: 'draft'
  current_revision: 1
  spec: ExperimentSpec
}
export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  status: 'completed' | 'failed' | 'interrupted'
  blocks: Array<{type:'text';text:string}|{type:'proposal';proposal:Proposal}|{type:'execution';experiment_id:string;preview_id:string;task_ids:string[]}|ExperimentCreatedReceipt>
  request_id?: string
}
export interface ConversationDetail { id:string;experiment_id:string|null;title:string;messages:ConversationMessage[] }
export interface ConversationTurn {
  conversation_id:string;run_id:string;request_id:string;status:'completed'
  user_message:ConversationMessage;assistant_message:ConversationMessage;timings:Partial<ProposalTimings>
}
