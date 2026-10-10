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
  spec: ExperimentSpec
}

export interface FieldDiff { path: string; before: unknown; after: unknown }

export interface Proposal {
  id: string
  source: 'card' | 'chat'
  candidate: ExperimentSpec
  explanation?: string
  diff: FieldDiff[]
}

export interface Revision {
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

export interface ValidationResult { ok: boolean; missing: MissingInput[] }
