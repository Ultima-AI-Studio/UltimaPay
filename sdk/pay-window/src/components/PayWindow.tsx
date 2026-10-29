import { useEffect, useRef, useState } from 'react';
import {
  Box, Button, Card, CardBody, CardHeader, Divider, HStack, Heading,
  Input, InputGroup, InputRightElement, Spinner, Stack, Tag, Text,
  useToast, VStack,
} from '@chakra-ui/react';
import { BrowserProvider, parseUnits, Interface } from 'ethers';
import { Connection, PublicKey, Transaction } from '@solana/web3.js';
import {
  createAssociatedTokenAccountInstruction, createTransferCheckedInstruction,
  getAccount, getAssociatedTokenAddress,
} from '@solana/spl-token';
import { createOrder, getOrder, confirmOrder, getChannels, qrcodeUrl, PayOrder } from '../api';
import { createPayCore, assetDecimals, amountToUnits, switchChainOutcome, receiptOutcome, displayStage, WindowStage } from '../pay_window_core';

// ── Ultima Pay 可嵌入支付窗口（产品无关，任何需求方前端直接引用）──
// 流程：拉起订单(服务端锁死金额+幂等键单订单) → 动态钱包通道(检测已装扩展→点亮可用通道/置灰未装)
//       → 唤起对应钱包签名(切链失败立即停止) → 提交回执(仅等待核验) → 轮询 → 服务端核验确认 → 履约状态展示
// UGP-20：金额精度按链（BSC=18/其余=6）、切链失败停止、二维码按链协议、本地 tx 不自行成功、
//         confirmed 不等于权益已开通（履约未完成显示"开通处理中"，仅 UGP-27 权益完成才显示会员可用）。

// 链/USDT 合约（钱包直付交易构造）
const USDT_TRC20 = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'; // TRON 主网 USDT TRC-20
const EVM_CHAINS = {
  ETH: { name: 'Ethereum', chainId: '0x1', contract: '0xdAC17F958D2ee523a2206206994597C13D831ec7', network: 'Ethereum ERC-20' },
  BSC: { name: 'BSC', chainId: '0x38', contract: '0x55d398326f99059fF775485246999027B3197955', network: 'BSC BEP-20' },
};
// Solana 主网 USDT SPL-token
const USDT_SPL_MINT = new PublicKey('Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB');
const SOLANA_RPC = 'https://api.mainnet-beta.solana.com';

export interface PayWindowProps {
  username: string;          // 用户标识
  userSignature: string;     // 宿主服务端签发；SDK 不持有签名密钥
  planId: string;            // 套餐/商品标识（金额由服务端按此锁定）
  planName?: string;         // 展示名
  amountUsdt?: number;       // 展示参考（实际以服务端订单为准）
  onResult?: (r: { status: string; orderNo: string; txHash?: string }) => void;
}

// 钱包探测结果
interface Probe { ethereum: boolean; tronWeb: boolean; solana: boolean; }

// 通道卡片模型（动态点亮：EVM/SOL 检测到钱包才可选；TRON 保留二维码兜底始终可选）
interface ChannelDef {
  key: string;            // 服务端 channel 标识（EVM 为 ETH/BSC 子链）
  label: string;          // 展示名
  note: string;           // 网络说明
  walletName: string;     // 需要检测的钱包
  detect: (p: Probe) => boolean;
  serverReady: boolean;   // 服务端通道是否已开通（收款地址已配置）
  fallback?: boolean;     // 无钱包时仍可选（二维码兜底）
  placeholder?: string;   // 未开通提示
}

const buildChannels = (p: Probe, srv?: Record<string, { address: string; enabled: boolean }>): ChannelDef[] => {
  const ready = (k: string, dft: boolean) => {
    const sc = srv && srv[k];
    return sc ? (!!sc.address && sc.enabled) : dft;  // 服务端配置为准：地址配好+启停才可直付
  };
  return [
    { key: 'TRON', label: 'TRON', note: 'USDT TRC-20', walletName: 'TronLink',
      detect: (q) => q.tronWeb, serverReady: ready('TRON', true), fallback: true },
    { key: 'ETH', label: 'Ethereum', note: 'USDT ERC-20', walletName: 'MetaMask 等',
      detect: (q) => q.ethereum, serverReady: ready('ETH', true) },
    { key: 'BSC', label: 'BSC', note: 'USDT BEP-20', walletName: 'MetaMask 等',
      detect: (q) => q.ethereum, serverReady: ready('BSC', true) },
    { key: 'SOL', label: 'Solana', note: 'USDT SPL', walletName: 'Phantom',
      detect: (q) => q.solana, serverReady: ready('SOL', false) },
  ];
};

export default function PayWindow({ username, userSignature, planId, planName, amountUsdt, onResult }: PayWindowProps) {
  const toast = useToast();
  const [stage, setStage] = useState<WindowStage>('creating');
  const [order, setOrder] = useState<PayOrder | null>(null);
  const [channel, setChannel] = useState('TRON');
  const [probe, setProbe] = useState<Probe>({ ethereum: false, tronWeb: false, solana: false });
  const [serverChannels, setServerChannels] = useState<Record<string, { address: string; enabled: boolean }> | undefined>(undefined);
  const [paying, setPaying] = useState(false);
  const [leftSec, setLeftSec] = useState(0);
  const [errMsg, setErrMsg] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 幂等键：组件生命周期固定一次（重复挂载/切链/重试共用 → 服务端同键单订单）
  const coreRef = useRef(createPayCore({
    username, planId,
    api: { createOrder: (user, product, selectedChannel, idempotencyKey) =>
             createOrder(user, userSignature, product, selectedChannel, idempotencyKey),
           getOrder, confirmOrder,
           channels: async () => { /* 由下方独立 useEffect 拉取服务端通道配置 */ return {}; } },
  }));

  // 探测浏览器已装钱包扩展（MetaMask 聚合多链 → window.ethereum；TronLink → window.tronWeb；Phantom → window.solana）
  useEffect(() => {
    const tw = (window as any).tronWeb;
    setProbe({
      ethereum: typeof (window as any).ethereum !== 'undefined',
      tronWeb: Boolean(tw && tw.ready),
      solana: typeof (window as any).solana !== 'undefined',
    });
  }, []);

  // 拉取服务端通道配置（后台支付管理页可改）：地址配好+启停的链才点亮直付，拉取失败回落静态默认
  useEffect(() => {
    getChannels()
      .then((d) => {
        const map: Record<string, { address: string; enabled: boolean }> = {};
        (d.channels || []).forEach((c) => {
          map[c.key] = { address: c.address || '', enabled: !!c.enabled };
        });
        setServerChannels(map);
      })
      .catch(() => { /* 回落静态默认 */ });
  }, []);

  const channels = buildChannels(probe, serverChannels);
  const active = channels.find((c) => c.key === channel);

  // 拉起订单（幂等键固定；通道由服务端校验是否开通）
  const launch = async (ch?: string) => {
    setStage('creating');
    setErrMsg('');
    const s = await coreRef.current.launch(ch);
    setOrder(coreRef.current.order as PayOrder | null);
    if (ch) setChannel(ch);
    setStage(s);
    if (s === 'error') {
      setErrMsg(coreRef.current.order ? '' : '下单失败');
      setStage('error');
    }
  };

  useEffect(() => { launch(); }, []);

  // 轮询订单状态 + 倒计时（服务端 confirmed 且履约 done 才显示 confirmed；否则 confirming"开通处理中"）
  useEffect(() => {
    if (stage !== 'pending' && stage !== 'submitted' && stage !== 'confirming') return;
    if (!order) return;
    const tick = async () => {
      const left = Math.max(0, Math.floor((new Date(order.expiredAt).getTime() - Date.now()) / 1000));
      setLeftSec(left);
      const s = await coreRef.current.poll();
      setOrder(coreRef.current.order as PayOrder | null);
      setStage(s);
      if (s === 'confirmed') { if (onResult) onResult({ status: 'confirmed', orderNo: order.orderNo, txHash: order.txHash }); }
    };
    tick();
    pollRef.current = setInterval(tick, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, order?.orderNo]);

  const submitReceipt = async (o: PayOrder, tx: string) => {
    try {
      const out = await coreRef.current.submitReceipt(tx);
      setOrder(coreRef.current.order as PayOrder | null);
      // UGP-20：本地 tx hash 不自行变成功——只有服务端 confirmed 才算；否则仅等待核验
      if (out === 'confirmed') {
        setStage('confirmed');
        if (onResult) onResult({ status: 'confirmed', orderNo: o.orderNo, txHash: tx });
      } else {
        setStage('submitted');
        toast({ title: '已提交，等待链上核验', description: `tx: ${tx}`, status: 'info', duration: 8000 });
      }
    } catch {
      toast({ title: '已广播，等待链上确认', description: `tx: ${tx}`, status: 'info', duration: 5000 });
    }
  };

  // TRON 钱包直付（TronLink）
  const tronPay = async () => {
    const tw = (window as any).tronWeb;
    if (!tw || !tw.ready) {
      toast({ title: '未检测到 TronLink', description: '请安装 TronLink 扩展后重试', status: 'warning', duration: 4000 });
      return;
    }
    setPaying(true);
    try {
      const contract = await tw.contract().at(USDT_TRC20);
      const amountSun = amountToUnits(order!.amountUsdt, 'TRON');
      const tx = await contract.transfer(order!.address, amountSun).send({ feeLimit: 30_000_000 });
      await submitReceipt(order!, tx);
    } catch (e) {
      toast({ title: '支付失败', description: e instanceof Error ? e.message : '用户取消或转账失败', status: 'error', duration: 4000 });
    } finally {
      setPaying(false);
    }
  };

  // EVM 钱包直付（MetaMask 等聚合钱包：切链 + USDT ERC-20/BEP-20 转账）
  const evmPay = async () => {
    const eth = (window as any).ethereum;
    if (!eth) {
      toast({ title: '未检测到 EVM 钱包', description: '请安装 MetaMask 等扩展后重试', status: 'warning', duration: 4000 });
      return;
    }
    setPaying(true);
    try {
      const cfg = EVM_CHAINS[channel as keyof typeof EVM_CHAINS];
      if (!cfg) throw new Error('不支持的 EVM 链');
      // 切链：失败立即停止，不能忽略错误后继续发送
      let outcome: ReturnType<typeof switchChainOutcome> = 'ok';
      try {
        await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: cfg.chainId }] });
      } catch (e: any) {
        outcome = switchChainOutcome(e?.code);
      }
      if (outcome === 'need_add_chain') {
        toast({ title: `请在钱包中添加 ${cfg.name} 网络后重试`, status: 'warning', duration: 5000 });
        return;
      }
      if (outcome === 'block') {
        toast({ title: '切链失败，已停止', description: '请检查钱包网络状态后重试', status: 'error', duration: 4000 });
        return;
      }
      const provider = new BrowserProvider(eth);
      const signer = await provider.getSigner();
      const iface = ['function transfer(address to, uint256 value) returns (bool)'];
      // UGP-20：金额精度按链（ETH USDT=6 位、BSC USDT=18 位），不得混用 18 位
      const dec = assetDecimals(channel);
      const data = new Interface(iface).encodeFunctionData('transfer', [
        order!.address, parseUnits(order!.amountUsdt.toFixed(dec), dec),
      ]);
      const tx = await signer.sendTransaction({ to: cfg.contract, data });
      await submitReceipt(order!, tx.hash);
    } catch (e) {
      toast({ title: '支付失败', description: e instanceof Error ? e.message : '用户取消或转账失败', status: 'error', duration: 4000 });
    } finally {
      setPaying(false);
    }
  };

  // Solana 钱包直付（Phantom：SPL USDT 转账，收款 ATA 不存在时自动创建）
  const solanaPay = async () => {
    const sol = (window as any).solana;
    if (!sol || !sol.isPhantom) {
      toast({ title: '未检测到 Phantom', description: '请安装 Phantom 扩展后重试', status: 'warning', duration: 4000 });
      return;
    }
    setPaying(true);
    try {
      const resp = await sol.connect();
      const sender = new PublicKey(resp.publicKey);
      const recipient = new PublicKey(order!.address!);
      const connection = new Connection(SOLANA_RPC, 'confirmed');
      const senderATA = await getAssociatedTokenAddress(USDT_SPL_MINT, sender);
      const recipientATA = await getAssociatedTokenAddress(USDT_SPL_MINT, recipient);
      const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('finalized');
      const tx = new Transaction({ feePayer: sender, blockhash, lastValidBlockHeight });
      // 收款方 ATA 不存在 → 先创建（发送方作为 payer 支付手续费）
      try {
        await getAccount(connection, recipientATA);
      } catch {
        tx.add(createAssociatedTokenAccountInstruction(sender, recipientATA, recipient, USDT_SPL_MINT));
      }
      // SPL 转账（USDT 6 位小数）
      const amount = amountToUnits(order!.amountUsdt, 'SOL');
      tx.add(createTransferCheckedInstruction(senderATA, USDT_SPL_MINT, recipientATA, sender, amount, 6));
      const signed = await sol.signTransaction(tx);
      const sig = await connection.sendRawTransaction(signed.serialize());
      await submitReceipt(order!, sig);
    } catch (e) {
      toast({ title: '支付失败', description: e instanceof Error ? e.message : '用户取消或转账失败', status: 'error', duration: 4000 });
    } finally {
      setPaying(false);
    }
  };

  const walletPay = () => {
    if (channel === 'TRON') return tronPay();
    if (channel === 'ETH' || channel === 'BSC') return evmPay();
    if (channel === 'SOL') return solanaPay();
    toast({ title: '该链适配器接入中', description: '暂请选择 TRON（USDT TRC-20）', status: 'info', duration: 4000 });
  };

  const copyAddress = async () => {
    if (!order?.address) return;
    try { await navigator.clipboard.writeText(order.address); toast({ title: '地址已复制', status: 'success', duration: 2000 }); }
    catch { toast({ title: '复制失败，请手动复制', status: 'error', duration: 2000 }); }
  };

  // 选择通道：EVM/SOL 需检测到钱包；TRON 有二维码兜底始终可进；服务端未开通的链提示待开通
  const selectChannel = (c: ChannelDef) => {
    if (!c.detect(probe) && !c.fallback) {
      toast({ title: `未检测到 ${c.walletName}`, description: `安装 ${c.walletName} 浏览器扩展后可选用 ${c.label} 通道`, status: 'info', duration: 3500 });
      return;
    }
    if (c.detect(probe) && !c.serverReady) {
      toast({ title: `${c.label} 通道待开通`, description: c.placeholder || '收款地址配置完成后即可使用', status: 'warning', duration: 3500 });
      return;
    }
    setChannel(c.key);
  };

  // ── 渲染 ──
  if (stage === 'creating') {
    return (
      <Card maxW="md" mx="auto" mt={6}>
        <CardBody textAlign="center" py={10}>
          <Spinner size="lg" mx="auto" mb={3} />
          <Text color="gray.500">正在拉起订单，锁定支付金额…</Text>
        </CardBody>
      </Card>
    );
  }

  if (stage === 'error') {
    return (
      <Card maxW="md" mx="auto" mt={6}>
        <CardBody textAlign="center" py={8}>
          <Heading size="sm" mb={3}>下单失败</Heading>
          <Text color="red.500" mb={4}>{errMsg}</Text>
          <Button size="sm" onClick={() => launch()}>重试</Button>
        </CardBody>
      </Card>
    );
  }

  if (stage === 'confirmed') {
    return (
      <Card maxW="md" mx="auto" mt={6} borderColor="green.300">
        <CardBody textAlign="center" py={10}>
          <Text fontSize="5xl" mb={2}>✅</Text>
          <Heading size="md" color="green.600" mb={2}>会员权益已开通</Heading>
          <Text color="gray.500" fontSize="sm">订单 {order?.orderNo} 已确认到账</Text>
          {order?.txHash && <Text color="gray.400" fontSize="xs" mt={2} wordBreak="break-all">{order.txHash}</Text>}
        </CardBody>
      </Card>
    );
  }

  if (stage === 'confirming') {
    return (
      <Card maxW="md" mx="auto" mt={6} borderColor="green.300">
        <CardBody textAlign="center" py={10}>
          <Text fontSize="5xl" mb={2}>⏳</Text>
          <Heading size="md" color="green.600" mb={2}>已到账，权益开通处理中</Heading>
          <Text color="gray.500" fontSize="sm">付款已确认，权益即将开通（如有延迟请稍候）</Text>
          {order?.txHash && <Text color="gray.400" fontSize="xs" mt={2} wordBreak="break-all">{order.txHash}</Text>}
        </CardBody>
      </Card>
    );
  }

  if (stage === 'expired') {
    return (
      <Card maxW="md" mx="auto" mt={6} borderColor="red.300">
        <CardBody textAlign="center" py={10}>
          <Heading size="md" color="red.500" mb={2}>订单已失效</Heading>
          <Text color="gray.500" fontSize="sm" mb={4}>未在 30 分钟内完成支付</Text>
          <Button size="sm" onClick={() => launch()}>重新下单</Button>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card maxW="md" mx="auto" mt={6} borderColor="gray.200">
      <CardHeader pb={2}>
        <HStack justify="space-between">
          <Heading size="sm">Ultima Pay 支付</Heading>
          <Text fontSize="xs" color="gray.400">订单 {order?.orderNo}</Text>
        </HStack>
      </CardHeader>
      <CardBody pt={0}>
        {/* 金额（服务端锁死） */}
        <Box p={3} bg="gray.50" borderRadius="md" mb={4}>
          <HStack justify="space-between">
            <Text fontSize="sm" color="gray.500">{order?.planName || planName || '商品'}</Text>
            <Text fontSize="2xl" fontWeight="bold" color="gray.800">
              {order?.amountUsdt.toFixed(2) ?? amountUsdt?.toFixed(2)} <Text as="span" fontSize="sm" color="gray.400">USDT</Text>
            </Text>
          </HStack>
          {order && order.tail > 0 && (
            <Text fontSize="xs" color="orange.500" mt={1}>
              含防撞尾数 {order.tail.toFixed(1)}，请按 {order.amountUsdt.toFixed(2)} USDT 精确支付
            </Text>
          )}
        </Box>

        {/* 动态钱包通道：检测到钱包才点亮 */}
        <Text fontSize="sm" color="gray.500" mb={2}>选择支付通道（自动检测已装钱包）</Text>
        <HStack spacing={2} mb={4} flexWrap="wrap">
          {channels.map((c) => {
            const detected = c.detect(probe);
            const selected = channel === c.key;
            const usable = detected || c.fallback;
            return (
              <Button
                key={c.key} size="sm"
                variant={selected ? 'solid' : 'outline'}
                colorScheme={selected ? 'purple' : detected ? 'green' : 'gray'}
                isDisabled={!usable}
                onClick={() => selectChannel(c)}
                title={detected ? (c.serverReady ? c.note : `${c.note} · ${c.placeholder}`) : (c.fallback ? `${c.note} · 未检测到 ${c.walletName}，可扫码转账` : `未检测到 ${c.walletName}，安装后可用`)}
              >
                {c.label}{detected ? ' ✓' : c.fallback ? ' · 兜底' : ' · 未装'}
              </Button>
            );
          })}
        </HStack>
        {active && (
          <Text fontSize="xs" color={active.serverReady ? 'gray.500' : 'orange.500'} mb={4}>
            {active.detect(probe)
              ? (active.serverReady ? `${active.walletName} 已就绪 · ${active.note}` : active.placeholder)
              : `未检测到 ${active.walletName}，安装后可选用该通道`}
          </Text>
        )}

        {/* 钱包直付 / 二维码兜底 */}
        {active && active.detect(probe) && active.serverReady ? (
          <VStack spacing={3} align="stretch" mb={4}>
            <Tag colorScheme="green" size="sm" w="fit-content">✓ 检测到 {active.walletName}</Tag>
            <Button colorScheme="purple" isLoading={paying} onClick={walletPay} size="lg">
              唤起 {active.walletName} 支付 {order?.amountUsdt.toFixed(2)} USDT
            </Button>
            <Text fontSize="xs" color="gray.400" textAlign="center">
              将在钱包中弹出转账确认（{order?.network} · {order?.address?.slice(0, 8)}…{order?.address?.slice(-6)}）
            </Text>
          </VStack>
        ) : (
          <VStack spacing={3} align="stretch" mb={4}>
            {active && active.detect(probe) && !active.serverReady && (
              <Tag colorScheme="orange" size="sm" w="fit-content">⚠ {active.walletName} 已就绪，{active.placeholder}</Tag>
            )}
            {active && !active.detect(probe) && (
              <Tag colorScheme="gray" size="sm" w="fit-content">
                未检测到 {active.walletName}，可扫码/复制地址转账（{order?.network}）
              </Tag>
            )}
            {!active && (
              <Tag colorScheme="gray" size="sm" w="fit-content">未检测到可用钱包扩展</Tag>
            )}
            {order?.address && (
              <HStack spacing={4} align="flex-start">
                <Box border="1px solid" borderColor="gray.200" borderRadius="md" p={2}>
                  <img src={qrcodeUrl(order)} width={112} height={112} alt="收款二维码" style={{ display: 'block' }} />
                </Box>
                <Stack spacing={2} flex={1}>
                  <Text fontSize="sm" color="gray.500">扫码或复制地址转账（{order?.network}）</Text>
                  <InputGroup size="sm">
                    <Input value={order?.address || ''} readOnly pr="4.5rem" fontSize="xs" />
                    <InputRightElement width="4.5rem">
                      <Button size="xs" h="1.75rem" onClick={copyAddress}>复制</Button>
                    </InputRightElement>
                  </InputGroup>
                  <Text fontSize="xs" color="orange.500">
                    金额 {order?.amountUsdt.toFixed(2)} USDT（含尾数，务必精确）
                  </Text>
                  <Text fontSize="xs" color="gray.400">转账后系统自动确认到账</Text>
                </Stack>
              </HStack>
            )}
          </VStack>
        )}

        <Divider mb={3} />

        {/* 状态与倒计时 */}
        <HStack justify="space-between">
          <HStack>
            <Box w={2} h={2} borderRadius="full" bg={stage === 'submitted' ? 'blue.400' : 'orange.400'} />
            <Text fontSize="sm" color="gray.600">
              {stage === 'submitted' ? '等待链上核验' : '待支付'}
            </Text>
          </HStack>
          <Text fontSize="sm" color="gray.500">
            剩余 <Text as="b" color={leftSec < 60 ? 'red.500' : 'gray.700'}>{Math.floor(leftSec / 60)}:{String(leftSec % 60).padStart(2, '0')}</Text>
          </Text>
        </HStack>
      </CardBody>
    </Card>
  );
}
