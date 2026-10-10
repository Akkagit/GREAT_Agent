import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { browserConfig } from '../api/client'
import { ExperimentWorkspace } from '../pages/ExperimentWorkspace'


const baseSpec = {
  schema_version: '1.0',
  workflow: 'pppflt',
  scenario: {
    mode: 'static',
    frequency: 'DF',
    ambiguity: 'float',
    reset_hours: 0,
  },
  date_segments: [{ year: 2024, start_doy: 350, end_doy: 360 }],
  excluded_dates: [],
  station_group_id: 'default_15',
  stations: Array.from({ length: 15 }, (_, index) => `S${String(index).padStart(3, '0')}`),
  input_profile_id: 'local_default',
  solver_profile_id: 'great_local',
  template_profile_id: 'pppflt_base',
  concurrency: 1,
  outputs: ['flt', 'log'],
}

const apiMock = vi.hoisted(() => ({
  getExperiment: vi.fn(),
  proposeChat: vi.fn(),
  previewProposal: vi.fn(),
  acceptRevision: vi.fn(),
  validateExperiment: vi.fn(),
  freezeExperiment: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: apiMock }
})

const experiment = {
  id: 'exp-1',
  status: 'draft',
  current_revision: 1,
  spec: baseSpec,
}

const chatProposal = {
  id: 'proposal-chat',
  source: 'chat',
  explanation: '切换到三频解算',
  candidate: {
    ...baseSpec,
    scenario: { ...baseSpec.scenario, frequency: 'TF' },
  },
  diff: [{ path: '/scenario/frequency', before: 'DF', after: 'TF' }],
}

describe('ExperimentWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.getExperiment.mockResolvedValue(experiment)
    apiMock.proposeChat.mockResolvedValue(chatProposal)
    apiMock.previewProposal.mockResolvedValue(chatProposal)
    apiMock.acceptRevision.mockResolvedValue({
      number: 2,
      spec: chatProposal.candidate,
      is_frozen: false,
    })
    apiMock.validateExperiment.mockResolvedValue({
      ok: false,
      missing: [{ kind: 'observation', station: 'S000', year: 2024, doy: 350 }],
    })
    apiMock.freezeExperiment.mockResolvedValue({ is_frozen: true })
  })

  it('previews chat changes before accepting a revision', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await screen.findByText('Revision 1')
    await user.type(screen.getByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: /发送/i }))

    expect(await screen.findByText('frequency')).toBeInTheDocument()
    expect(screen.getByText('DF → TF')).toBeInTheDocument()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })

  it('submits card edits through the same proposal flow', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.selectOptions(await screen.findByLabelText('频率'), 'TF')
    await user.click(screen.getByRole('button', { name: /预览修改/i }))

    expect(apiMock.previewProposal).toHaveBeenCalledWith(
      'exp-1',
      expect.objectContaining({
        source: 'card',
        candidate: expect.objectContaining({
          scenario: expect.objectContaining({ frequency: 'TF' }),
        }),
      }),
    )
  })

  it('keeps freeze disabled while validation fails', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.click(await screen.findByRole('button', { name: /检查输入/i }))

    expect(await screen.findByText(/S000/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /冻结计划/i })).toBeDisabled()
  })

  it('never exposes an API key in browser configuration', () => {
    const serialized = JSON.stringify(browserConfig).toLowerCase()

    expect(serialized).not.toContain('api_key')
    expect(serialized).not.toContain('apikey')
    expect(serialized).not.toContain('secret')
  })

  it('replaces the loading state with an actionable load error', async () => {
    apiMock.getExperiment.mockRejectedValueOnce(new Error('offline'))

    render(<ExperimentWorkspace experimentId="exp-1" />)

    expect(await screen.findByRole('alert')).toHaveTextContent('确认本地服务正在运行')
    expect(screen.queryByText('正在读取实验计划…')).not.toBeInTheDocument()
  })

  it('reloads the latest plan after a stale revision conflict', async () => {
    const user = userEvent.setup()
    apiMock.acceptRevision.mockRejectedValueOnce(
      Object.assign(new Error('revision changed'), { code: 'revision_conflict' }),
    )
    apiMock.getExperiment
      .mockResolvedValueOnce(experiment)
      .mockResolvedValueOnce({ ...experiment, current_revision: 2 })
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: /发送/i }))
    await user.click(await screen.findByRole('button', { name: /接受修改/i }))

    expect(await screen.findByText(/计划已被更新/)).toBeInTheDocument()
    await waitFor(() => expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Revision 2')).toBeInTheDocument()
  })
})
