import { Steps } from 'antd'

const labels = ['接受提案', '检查输入', '生成 XML', '执行预览', '明确确认', '解算', '运行监控']

export function WorkflowSteps({ current }: { current: number }) {
  return <div className="workflow-steps" aria-label="实验流程"><Steps current={current} size="small" responsive={false} items={labels.map((title,index)=>({title:<span aria-current={index===current?'step':undefined}>{title}</span>}))} /></div>
}
