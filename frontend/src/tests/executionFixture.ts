import type { ExecutionPreview } from '../api/types'

export const executionPreview: ExecutionPreview = {
  id: 'preview-1', experiment_id: 'exp-1', revision: 3, revision_id: 'revision-3', intent: 'initial', expires_at: '2035-01-01T00:00:00Z',
  executable_path: 'D:\\GREAT\\GREAT_PVT.exe', executable_sha256: 'a'.repeat(64),
  template_path: 'F:\\templates\\base.xml', template_sha256: 'b'.repeat(64),
  spec: { schema_version: '1.0', workflow: 'pppflt', scenario: { mode: 'static', frequency: 'DF', ambiguity: 'float', reset_hours: 0 },
    date_segments: [{ year: 2024, start_doy: 339, end_doy: 339 }], excluded_dates: [], station_group_id: 'default_15',
    stations: Array.from({ length: 15 }, (_, i) => `S${i.toString().padStart(3, '0')}`), input_profile_id: 'local_default', solver_profile_id: 'great_local', template_profile_id: 'pppflt_base', concurrency: 1, outputs: ['flt', 'log'] },
  jobs: [{ year: 2024, doy: 339, attempt_number: 1, task_id: null, snapshot_path: 'F:\\runs\\previews\\p1\\config.xml',
    xml_path: 'F:\\runs\\exp-1\\tasks\\2024_339\\attempts\\0001\\config.xml',
    output_dir: 'F:\\runs\\exp-1\\tasks\\2024_339\\attempts\\0001\\results', cwd: 'F:\\runs\\exp-1\\tasks\\2024_339\\attempts\\0001',
    xml_content: '<config><filter reset_par="0" /></config>', xml_sha256: 'c'.repeat(64),
    command: ['D:\\GREAT\\GREAT_PVT.exe', '-x', 'F:\\runs\\exp-1\\tasks\\2024_339\\attempts\\0001\\config.xml'],
    inputs: [{ kind: 'observation', station: 'S000', path: 'F:\\obs\\s0003390.24o', state: 'present', size_bytes: 100, mtime_ns: 123 }] }],
}
