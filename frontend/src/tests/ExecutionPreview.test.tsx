import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExecutionPreview } from '../components/ExecutionPreview'
import { selectOption } from './selectOption'
import { executionPreview } from './executionFixture'

describe('ExecutionPreview', () => {
  it('shows the actual XML, executable, command, inputs and output before confirmation', () => {
    render(<ExecutionPreview preview={executionPreview} busy={false} onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByRole('region', { name: 'XML 全文' })).toHaveTextContent(executionPreview.jobs[0].xml_content)
    expect(screen.getAllByText(executionPreview.executable_path).length).toBeGreaterThan(0)
    expect(screen.getByText(executionPreview.jobs[0].output_dir)).toBeInTheDocument()
    expect(screen.getByText('F:\\obs\\s0003390.24o')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认并开始解算' })).toBeDisabled()
  })

  it('requires a fresh checkbox and does not execute on close', async () => {
    const user = userEvent.setup(), onConfirm = vi.fn(), onClose = vi.fn()
    render(<ExecutionPreview preview={executionPreview} busy={false} onConfirm={onConfirm} onClose={onClose} />)
    await user.click(screen.getByRole('button', { name: '返回查看计划' }))
    expect(onClose).toHaveBeenCalledOnce()
    expect(onConfirm).not.toHaveBeenCalled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认并开始解算' }))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('resets confirmation when switching daily XMLs', async () => {
    const user = userEvent.setup()
    const preview = { ...executionPreview, jobs: [...executionPreview.jobs, { ...executionPreview.jobs[0], doy: 340 }] }
    render(<ExecutionPreview preview={preview} busy={false} onConfirm={vi.fn()} onClose={vi.fn()} />)
    await user.click(screen.getByRole('checkbox'))
    await selectOption(user, '查看日期 XML', '2024 / DOY 340 · Attempt 1')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '确认并开始解算' })).toBeDisabled()
  })

  it('blocks an expired preview without sending confirmation', () => {
    render(<ExecutionPreview preview={{ ...executionPreview, expires_at: '2020-01-01T00:00:00Z' }} busy={false} onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByRole('button', { name: '确认并开始解算' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('过期')
  })
})
