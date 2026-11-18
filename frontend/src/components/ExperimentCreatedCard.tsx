import { Button, Card, Descriptions, Tag } from 'antd'
import type { ExperimentCreatedReceipt } from '../api/types'

export function ExperimentCreatedCard({ receipt, onOpen }: { receipt: ExperimentCreatedReceipt; onOpen?: (id: string) => void }) {
  const { spec } = receipt
  return <Card size="small" title="实验草稿已创建" extra={<Tag>草稿</Tag>}>
    <Descriptions size="small" column={1} items={[
      { key: 'dates', label: '日期', children: spec.date_segments.map(s => `${s.year} / DOY ${s.start_doy}${s.end_doy === s.start_doy ? '' : `–${s.end_doy}`}`).join('、') },
      { key: 'scenario', label: '方案', children: `${spec.scenario.mode === 'static' ? '静态' : '动态'} · ${spec.scenario.frequency} · ${spec.scenario.ambiguity === 'float' ? '浮点解' : '固定解'}` },
      { key: 'stations', label: '测站', children: spec.stations.join('、') },
    ]} />
    <p>尚未启动解算。进入实验后可调整计划、核对 XML 并确认执行。</p>
    <Button type="primary" {...(onOpen ? { onClick: () => onOpen(receipt.experiment_id) } : { href: `?experiment=${encodeURIComponent(receipt.experiment_id)}` })}>进入实验</Button>
  </Card>
}
