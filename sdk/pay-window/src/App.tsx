import { useState } from 'react';
import {
  Box, Button, Card, CardBody, CardHeader, Container, HStack, Heading,
  Input, Radio, RadioGroup, Stack, Tag, Text, VStack,
} from '@chakra-ui/react';
import PayWindow from './components/PayWindow';

// ── Ultima Pay 模拟购买 demo：选套餐 → 下一步(加密支付) → PayWindow 支付窗口 ──
// 该 demo 演示"需求方产品如何接入 Ultima Pay"：产品只传 用户 + 套餐ID，
// 订单金额由 Ultima Pay 服务端锁死，支付窗口独立完成 拉起→支付→确认。

const PLANS = [
  { id: 'SP-01', name: '月卡', price: 198, days: 30 },
  { id: 'SP-02', name: '季卡', price: 458, days: 90 },
  { id: 'SP-03', name: '年卡', price: 1198, days: 365 },
  { id: 'SP-04', name: '体验卡', price: 0, days: 7 },
];

export default function App() {
  const [username, setUsername] = useState('gold_hunter');
  const [planId, setPlanId] = useState('SP-02');
  const [paying, setPaying] = useState(false);
  const [result, setResult] = useState<string>('');

  const plan = PLANS.find((p) => p.id === planId)!;

  return (
    <Container maxW="lg" py={8}>
      <VStack spacing={5} align="stretch">
        <Box textAlign="center">
          <Heading size="lg" mb={1}>Ultima Pay</Heading>
          <Text color="gray.500" fontSize="sm">聚合支付 SDK · 需求方接入示例</Text>
        </Box>

        <Card>
          <CardHeader pb={1}>
            <Heading size="sm">选择订阅套餐</Heading>
          </CardHeader>
          <CardBody>
            <RadioGroup value={planId} onChange={(v) => { setPlanId(v); setPaying(false); setResult(''); }}>
              <Stack spacing={2}>
                {PLANS.map((p) => (
                  <Radio key={p.id} value={p.id} isDisabled={p.price === 0}>
                    <HStack justify="space-between" w="100%">
                      <Text>{p.name} · {p.days} 天</Text>
                      <Text fontSize="sm" color={p.price === 0 ? 'gray.400' : 'gray.700'}>
                        {p.price === 0 ? '免费体验' : `${p.price} USDT`}
                      </Text>
                    </HStack>
                  </Radio>
                ))}
              </Stack>
            </RadioGroup>
            <Text fontSize="xs" color="gray.400" mt={2}>体验卡（0 价）无需支付，仅供新用户免费体验</Text>
          </CardBody>
        </Card>

        <Card>
          <CardHeader pb={1}>
            <Heading size="sm">用户标识</Heading>
          </CardHeader>
          <CardBody>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="输入用户 UID / 用户名" size="sm" />
            <Text fontSize="xs" color="gray.400" mt={2}>官网对接后由登录态自动传入</Text>
          </CardBody>
        </Card>

        {!paying ? (
          <Button
            colorScheme="purple" size="lg"
            isDisabled={plan.price === 0 || !username.trim()}
            onClick={() => { setResult(''); setPaying(true); }}
          >
            下一步：加密支付（{plan.name} · {plan.price} USDT）
          </Button>
        ) : (
          <Stack spacing={3}>
            <PayWindow
              username={username.trim()}
              planId={plan.id}
              planName={plan.name}
              amountUsdt={plan.price}
              onResult={(r) => setResult(`${r.status} · ${r.orderNo}${r.txHash ? ' · ' + r.txHash.slice(0, 12) + '…' : ''}`)}
            />
            <HStack justify="center">
              <Button size="xs" variant="ghost" onClick={() => setPaying(false)}>返回重新选择套餐</Button>
            </HStack>
          </Stack>
        )}

        {result && (
          <Box textAlign="center" p={3} bg="green.50" borderRadius="md">
            <Text fontSize="sm" color="green.700">支付结果：{result}</Text>
          </Box>
        )}
      </VStack>
    </Container>
  );
}
