import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExecutionReviewPage } from '../pages/ExecutionReviewPage'
import { executionPreview } from './executionFixture'

const apiMock = vi.hoisted(() => ({ getExecutionPreview: vi.fn(), confirmExecution: vi.fn() }))
vi.mock('../api/client', async (importOriginal) => ({ ...await importOriginal<typeof import('../api/client')>(), api: apiMock }))
describe('ExecutionReviewPage', () => {
  beforeEach(() => { vi.clearAllMocks(); apiMock.getExecutionPreview.mockResolvedValue(executionPreview); apiMock.confirmExecution.mockResolvedValue({ count: 1, tasks: ['task-1'] }) })
  it('loads a persisted preview and enters monitoring only after explicit confirmation', async () => {
    const user = userEvent.setup(), onStarted = vi.fn()
    render(<ExecutionReviewPage experimentId="exp-1" previewId="preview-1" onStarted={onStarted} onBack={vi.fn()} />)
    await screen.findByRole('button', { name: '确认并开始解算' })
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认并开始解算' }))
    expect(apiMock.confirmExecution).toHaveBeenCalledWith('exp-1', { preview_id: 'preview-1', expected_revision: 3, confirmed: true })
    expect(onStarted).toHaveBeenCalledOnce()
  })
  it('retains a failed preview and clears the checkbox while showing the specific error', async () => {
    const user = userEvent.setup()
    apiMock.confirmExecution.mockRejectedValue(new Error('模板已变化，请重新生成执行预览'))
    render(<ExecutionReviewPage experimentId="exp-1" previewId="preview-1" onStarted={vi.fn()} onBack={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认并开始解算' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('模板已变化')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '确认并开始解算' })).toBeDisabled()
  })

  it('preserves the authorized submission but does not navigate after leaving its preview', async () => {
    let finish!: (value: {count:number;tasks:string[]}) => void
    apiMock.confirmExecution.mockImplementation(() => new Promise(resolve => { finish=resolve }))
    const user=userEvent.setup(), onStarted=vi.fn()
    const page=render(<ExecutionReviewPage experimentId="exp-1" previewId="preview-1" onStarted={onStarted} onBack={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox'))
    await user.click(screen.getByRole('button',{name:'确认并开始解算'}))
    page.unmount()
    await act(async () => { finish({count:1,tasks:['task-1']}) })
    expect(apiMock.confirmExecution).toHaveBeenCalledOnce()
    expect(onStarted).not.toHaveBeenCalled()
  })
})
