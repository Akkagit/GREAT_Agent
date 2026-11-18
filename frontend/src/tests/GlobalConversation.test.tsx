import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { GlobalConversation } from '../components/GlobalConversation'
import { executionPreview } from './executionFixture'

const apiMock = vi.hoisted(() => ({ ensureConversation: vi.fn(), sendConversationMessage: vi.fn(), getConversation: vi.fn() }))
vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { ...original.api, ...apiMock } }
})
const receipt = { type: 'experiment_created', experiment_id: 'created-id', status: 'draft', current_revision: 1, spec: executionPreview.spec }
const assistant = { id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text: '已保存实验草稿。' }, receipt] }

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.ensureConversation.mockResolvedValue({ id: 'global', experiment_id: null, title: '全局对话', messages: [] })
})

it('shows a persisted creation receipt with configuration and opens its experiment', async () => {
  apiMock.ensureConversation.mockResolvedValue({ id: 'global', experiment_id: null, title: '全局对话', messages: [assistant] })
  const open = vi.fn()
  render(<GlobalConversation onOpenExperiment={open} />)
  expect(await screen.findByText('实验草稿已创建')).toBeInTheDocument()
  expect(screen.getByText(/尚未启动解算/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '进入实验' }))
  expect(open).toHaveBeenCalledWith('created-id')
})

it('renders a new receipt and refreshes the experiment list after a successful turn', async () => {
  apiMock.sendConversationMessage.mockResolvedValue({ conversation_id: 'global', run_id: 'r', request_id: 'q', status: 'completed', timings: {},
    user_message: { id: 'u', role: 'user', status: 'completed', blocks: [{ type: 'text', text: '创建实验' }] }, assistant_message: assistant })
  const created = vi.fn()
  render(<GlobalConversation onExperimentCreated={created} />)
  const user = userEvent.setup()
  await user.type(await screen.findByRole('textbox', { name: /message/i }), '创建实验')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByText('实验草稿已创建')).toBeInTheDocument()
  expect(created).toHaveBeenCalledTimes(1)
})

it('recovers a committed creation receipt when its HTTP response is lost', async () => {
  let requestId = ''
  apiMock.sendConversationMessage.mockImplementation(async (_id, _message, request) => {
    requestId = request
    throw new TypeError('Failed to fetch')
  })
  apiMock.getConversation.mockImplementation(async () => ({id:'global',experiment_id:null,title:'全局对话',messages:[
    {id:'u',request_id:requestId,role:'user',status:'completed',blocks:[{type:'text',text:'创建实验'}]},
    {...assistant,request_id:requestId},
  ]}))
  const created = vi.fn()
  render(<GlobalConversation onExperimentCreated={created} />)
  const user=userEvent.setup()
  await user.type(await screen.findByRole('textbox',{name:/message/i}),'创建实验')
  await user.click(screen.getByRole('button',{name:'发送'}))
  expect(await screen.findByText('实验草稿已创建')).toBeInTheDocument()
  expect(screen.getAllByText('创建实验')).toHaveLength(1)
  expect(apiMock.sendConversationMessage).toHaveBeenCalledTimes(1)
  expect(created).toHaveBeenCalledTimes(1)
})
