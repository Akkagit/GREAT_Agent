import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResizableWorkbench } from '../components/ResizableWorkbench'
import { XmlCode } from '../components/XmlCode'
import { ArtifactBrowser } from '../components/ArtifactBrowser'
import { legacyMonitorSnapshot } from './monitorFixture'

const mock = vi.hoisted(() => ({ listTasks: vi.fn(), getTask: vi.fn(), getTaskResults: vi.fn(), getTaskXml: vi.fn(),getExperimentMonitor:vi.fn(),openExperimentEvents:vi.fn() }))
vi.mock('../api/client', async original => ({ ...await original<typeof import('../api/client')>(), api: mock }))
beforeEach(() => { localStorage.clear(); vi.resetAllMocks(); mock.getExperimentMonitor.mockImplementation(id => legacyMonitorSnapshot(id,mock.listTasks,mock.getTask)); mock.openExperimentEvents.mockReturnValue(()=>{}) })

it('resizes by keyboard, restores saved widths and resets without losing pane content', async () => {
  const user = userEvent.setup()
  const props = { history: <p>历史卡片</p>, center: <p>参数画布</p>, chat: <textarea aria-label="对话草稿" defaultValue="保留消息" /> }
  const first = render(<ResizableWorkbench {...props} />)
  const divider = screen.getByRole('separator', { name: '调整历史区宽度' })
  const initial = Number(divider.getAttribute('aria-valuenow'))
  fireEvent.keyDown(divider, { key: 'ArrowRight' })
  const resized = Number(divider.getAttribute('aria-valuenow'))
  expect(resized).toBeGreaterThan(initial)
  expect(screen.getByLabelText('对话草稿')).toHaveValue('保留消息')
  first.unmount()
  render(<ResizableWorkbench {...props} />)
  expect(Number(screen.getByRole('separator', { name: '调整历史区宽度' }).getAttribute('aria-valuenow'))).toBeCloseTo(resized, 0)
  await user.click(screen.getByRole('button', { name: '恢复默认布局' }))
  expect(Number(screen.getByRole('separator', { name: '调整历史区宽度' }).getAttribute('aria-valuenow'))).toBeCloseTo(initial, 0)
})

it('ignores corrupt saved layout and clamps repeated keyboard resizing', () => {
  localStorage.setItem('great-agent-workbench-layout-v1', '{"history":-90,"chat":"broken"}')
  render(<ResizableWorkbench history="history" center="center" chat="chat" />)
  const divider = screen.getByRole('separator', { name: '调整历史区宽度' })
  for (let i=0;i<150;i++) fireEvent.keyDown(divider, { key: 'ArrowLeft' })
  expect(Number(divider.getAttribute('aria-valuenow'))).toBeGreaterThanOrEqual(180)
})

it('gives a research layout independent labels, DOM identities and saved widths', () => {
  const props = { history: '材料', center: '回答', chat: '证据', idPrefix: 'research', layoutKey: 'great-agent-research-layout-v1', labels: { history: '课题', center: '对话', chat: '证据' } }
  const first = render(<ResizableWorkbench {...props} />)
  const divider = screen.getByRole('separator', { name: '调整课题宽度' })
  fireEvent.keyDown(divider, { key: 'ArrowRight' })
  expect(divider).toHaveAttribute('aria-controls', 'research-history')
  expect(screen.getByRole('complementary', { name: '证据' })).toHaveAttribute('id', 'research-chat')
  expect(localStorage.getItem('great-agent-research-layout-v1')).not.toBeNull()
  expect(localStorage.getItem('great-agent-workbench-layout-v1')).toBeNull()
  first.unmount()
})

it('keeps a composed draft mounted while changing the visible mobile pane', async () => {
  const user = userEvent.setup()
  render(<ResizableWorkbench history="历史" center="工作区内容" chat={<textarea aria-label="对话草稿" />} />)
  // jsdom does not evaluate viewport media queries; expose the mobile controls.
  screen.getByRole('navigation', { hidden: true }).style.display = 'flex'
  const draft = screen.getByRole('textbox', { name: '对话草稿' })
  await user.type(draft, '尚未发送的修改')
  await user.click(screen.getByRole('button', { name: '实验' }))
  await user.click(screen.getByRole('button', { name: '对话' }))
  expect(screen.getByRole('textbox', { name: '对话草稿' })).toBe(draft)
  expect(draft).toHaveValue('尚未发送的修改')
})

it('renders unchanged XML as text, never interpreting source as markup', () => {
  const xml = '<?xml version="1.0"?>\n<config a="1">\n<!-- note -->\n<script>alert(1)</script>\n</config>'
  render(<XmlCode content={xml} />)
  const region = screen.getByRole('region', { name: 'XML 全文' })
  expect(region.textContent).toBe(xml)
  expect(region.querySelector('script')).toBeNull()
  expect(region.querySelector('.xml-token-tag')).not.toBeNull()
})

it('loads actual registered pictures for a selected attempt and does not render data files as images', async () => {
  mock.listTasks.mockResolvedValue([{ id:'t1', experiment_id:'e1', year:2024, doy:350, status:'succeeded' }])
  mock.getTask.mockResolvedValue({ id:'t1', attempts:[{id:'a1',number:1,status:'succeeded',exit_code:0,error_code:null}], active_attempt_id:'a1' })
  mock.getTaskResults.mockResolvedValue([
    { filename:'enu.png', station:null,kind:'plot',plot_type:'enu',size_bytes:100,url:'/api/v1/image-one' },
    { filename:'summary.csv', station:null,kind:'summary',size_bytes:90,url:'/api/v1/csv' },
  ])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)
  expect(await screen.findByRole('img', {name:'全局 ENU'})).toHaveAttribute('src','/api/v1/image-one')
  expect(screen.queryByRole('img',{name:'summary.csv'})).not.toBeInTheDocument()
  await waitFor(()=>expect(mock.getTaskResults).toHaveBeenCalledWith('t1','a1'))
})

it('shows an honest empty state when no real results exist', async () => {
  mock.listTasks.mockResolvedValue([])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)
  expect(await screen.findByText(/尚无可预览的图片/)).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
})

it('supports a controlled mobile pane without remounting drafts and reports manual navigation',()=>{
  const onMobilePaneChange=vi.fn()
  const props={history:'历史',center:'工作区内容',chat:<textarea aria-label="对话草稿" defaultValue="保留消息" />,onMobilePaneChange}
  const page=render(<ResizableWorkbench {...props} mobilePane="chat" />)
  screen.getByRole('navigation',{hidden:true}).style.display='flex'
  const draft=screen.getByRole('textbox',{name:'对话草稿'})
  expect(screen.getByRole('button',{name:'对话'})).toHaveAttribute('aria-pressed','true')
  fireEvent.click(screen.getByRole('button',{name:'工作区'}))
  expect(onMobilePaneChange).toHaveBeenCalledWith('center')
  expect(screen.getByRole('button',{name:'对话'})).toHaveAttribute('aria-pressed','true')
  page.rerender(<ResizableWorkbench {...props} mobilePane="center" />)
  expect(screen.getByRole('button',{name:'工作区'})).toHaveAttribute('aria-pressed','true')
  expect(screen.getByRole('textbox',{name:'对话草稿'})).toBe(draft)
  expect(draft).toHaveValue('保留消息')
})
