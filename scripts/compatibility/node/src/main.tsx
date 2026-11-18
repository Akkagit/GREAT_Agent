import { createRoot } from 'react-dom/client';
import { Button, Card, ConfigProvider, Flex, Table } from 'antd';
import { Bubble, Sender } from '@ant-design/x';

createRoot(document.getElementById('root')!).render(
  <ConfigProvider><Flex vertical gap="middle">
    <Card title="Synthetic fixture"><Table rowKey="id" pagination={false} columns={[{title: 'Value', dataIndex: 'value'}]} dataSource={[{id: 'synthetic', value: 1}]} /><Button>Review</Button></Card>
    <Bubble content="Synthetic compatibility message" /><Sender placeholder="Synthetic input only" />
  </Flex></ConfigProvider>,
);
