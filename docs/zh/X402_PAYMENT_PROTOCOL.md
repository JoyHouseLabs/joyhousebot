# x402 支付协议文档

x402 是基于 HTTP 402 状态码的微支付协议，支持使用 USDC/USDT 进行小额支付。

---

## 一、协议概述

### 1.1 设计目标

- **微支付**：支持小额、频繁的链下支付
- **自动化**：客户端自动检测 402 响应并完成支付
- **安全性**：基于 EIP-712 签名，无需暴露私钥
- **兼容性**：基于标准 HTTP 状态码，易于集成

### 1.2 协议流程

```
客户端                     服务器
  |                          |
  |--- 1. GET /endpoint --->|
  |                          |
  |<-- 2. 402 + Requirements --|
  |                          |
  |--- 3. 检查余额 -------->|
  |                          |
  |<-- 4. 余额信息 ---------|
  |                          |
  |--- 5. 签名 EIP-712 ---->|
  |                          |
  |--- 6. GET /endpoint ---->| (带 X-Payment header)
  |                          |
  |<-- 7. 200 + Content ---|
```

### 1.3 基础设施

- **区块链网络**：Base、Arbitrum、Polygon、Ethereum、BNB Smart Chain
- **支持的代币**：USDC、USDT
- **签名标准**：EIP-712 Typed Data
- **支付方式**：TransferWithAuthorization (EIP-3009)

---

## 二、协议规范

### 2.1 402 响应格式

服务器在需要支付时返回 402 状态码，包含以下信息：

**响应头**：
```
X-Payment-Required: base64编码的 JSON
```

**响应体**（JSON）：
```json
{
  "x402Version": 1,
  "accepts": [
    {
      "scheme": "exact",
      "network": "eip155:8453",
      "maxAmountRequired": "0.05",
      "payToAddress": "0x1234567890abcdef1234567890abcdef12345678",
      "requiredDeadlineSeconds": 300,
      "usdcAddress": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
      "usdtAddress": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
    }
  ]
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x402Version` | int | 是 | 协议版本（当前为 1） |
| `scheme` | string | 是 | 支付方案（"exact" 或其他） |
| `network` | string | 是 | 区块链网络 ID（CAIP-2 格式，如 `eip155:8453`） |
| `maxAmountRequired` | string | 是 | 最大支付金额（美元，如 "0.05"） |
| `payToAddress` | string | 是 | 收款地址 |
| `requiredDeadlineSeconds` | int | 否 | 授权有效期（秒，默认 300） |
| `usdcAddress` | string | 是 | USDC 合约地址 |
| `usdtAddress` | string | 否 | USDT 合约地址（可选） |

### 2.2 支付请求格式

客户端在重试请求时添加 `X-Payment` 请求头：

```json
{
  "x402Version": 1,
  "scheme": "exact",
  "network": "eip155:8453",
  "token": "USDC",
  "payload": {
    "type": "EIP712",
    "types": {
      "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"}
      ],
      "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"}
      ]
    },
    "primaryType": "TransferWithAuthorization",
    "domain": {
      "name": "USDC",
      "version": "1",
      "chainId": 8453,
      "verifyingContract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    },
    "message": {
      "from": "0x...",
      "to": "0x...",
      "value": "50000000",
      "validAfter": 1672531200,
      "validBefore": 1672531500,
      "nonce": "0x..."
    },
    "signature": "0x..."
  }
}
```

**请求头**：
```
X-Payment: base64编码的 JSON
```

---

## 三、支持的区块链网络

| 网络 | Chain ID | USDC 地址 | USDT 地址 |
|------|-----------|-----------|-----------|
| Base | 8453 | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | `0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2` |
| Arbitrum One | 42161 | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9` |
| Polygon | 137 | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | `0xc2132D05D31c914a87C6611C10748AEb04B58e8F` |
| Ethereum | 1 | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | `0xdAC17F958D2ee523a2206206994597C13D831ec7` |
| BNB Smart Chain | 56 | `0x8AC76a50cc25877605Fd8aB64762B617D09F42fB` | `0x55d398326f99059fF775485246999027B3197955` |
| Base Sepolia (测试网) | 84532 | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | - |

---

## 四、实现

### 4.1 客户端实现

**位置**：`joyhousebot/financial/x402_client.py`

**核心类**：

| 类 | 职责 |
|----|------|
| `X402Client` | 主要客户端，处理支付流程 |
| `PaymentRequirement` | 解析 402 响应中的支付要求 |
| `X402Policy` | 支付策略约束（限额、域名等） |

**使用示例**：

```python
from joyhousebot.financial.x402_client import X402Client, X402Policy
from joyhousebot.identity.evm import EvmIdentity

# 创建客户端
policy = X402Policy(
    max_single_payment_cents=100,  # $1.00
    max_daily_spend_cents=1000,   # $10.00
    allowed_domains=["*"],          # 允许所有域名
)
client = X402Client(policy=policy)

# 获取身份
identity = await get_identity()

# 发起支付请求
result = await client.fetch_with_payment(
    url="https://api.example.com/endpoint",
    identity=identity,
    method="GET",
)

if result.success:
    print(f"支付成功: ${result.amount_paid}")
    print(f"响应: {result.response}")
else:
    print(f"支付失败: {result.error}")

# 关闭客户端
await client.close()
```

### 4.2 工具实现

**位置**：`joyhousebot/agent/tools/x402_payment.py`

**可用工具**：

| 工具名 | 说明 | 参数 |
|--------|------|------|
| `check_token_balance` | 查询 USDC/USDT 余额 | `address`, `network`, `token` |
| `x402_fetch` | 发起带 x402 支付的 HTTP 请求 | `url`, `method`, `body`, `max_payment_cents` |
| `get_wallet_status` | 查询钱包状态 | - |
| `get_supported_networks` | 列出支持的区块链网络 | - |

**工具示例**：

```python
# 查询余额
balance = await tool_registry.execute(
    "check_token_balance",
    params={
        "network": "base",
        "token": "USDC",
    }
)

# 发起支付请求
response = await tool_registry.execute(
    "x402_fetch",
    params={
        "url": "https://api.example.com/endpoint",
        "max_payment_cents": 50,  # $0.50
    }
)
```

### 4.3 后端实现（服务器端）

**位置**：`backend/app/api/v1/endpoints/x402.py`

**端点**：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/x402/pay/{amount}/{wallet}` | GET | 支付端点 |
| `/x402/pay` | POST | 支付端点（POST） |
| `/x402/balance/{wallet}` | GET | 查询余额 |
| `/x402/payment/{id}` | GET | 查询支付状态 |
| `/x402/networks` | GET | 获取支持的网络 |

**使用示例**：

```bash
# 发起支付请求
curl https://api.example.com/x402/pay/0.05/0x123...

# 服务器返回 402
HTTP/1.1 402 Payment Required
X-Payment-Required: eyJ4MDJWZXJzaW9uIjoxLCJhY2NlcHRzIjpb...}

# 客户端重试带支付信息
curl https://api.example.com/x402/pay/0.05/0x123... \
  -H "X-Payment: eyJ4MDJWZXJzaW9uIjoxLi4ufQ=="
```

---

## 五、安全考虑

### 5.1 支付策略

**X402Policy** 配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_single_payment_cents` | 100 | 单次支付上限（$1.00） |
| `max_hourly_spend_cents` | 500 | 每小时消费上限（$5.00） |
| `max_daily_spend_cents` | 1000 | 每日消费上限（$10.00） |
| `allowed_domains` | `["*"]` | 允许的域名列表 |
| `require_confirmation_above_cents` | 50 | 超过此金额需确认（$0.50） |
| `default_deadline_seconds` | 300 | 默认授权有效期（300 秒） |
| `preferred_token` | `USDC` | 首选代币 |
| `preferred_network` | `eip155:8453` | 首选网络（Base） |

### 5.2 安全最佳实践

1. **限制域名白名单**：只允许受信任的域名
2. **设置合理的支付限额**：防止意外大额支付
3. **监控支付记录**：定期审查支付历史
4. **使用测试网**：在主网前先在测试网验证
5. **保护私钥**：私钥不应离开安全环境

### 5.3 风险提示

- **不可逆交易**：区块链交易一旦确认无法撤销
- **网络费用**：部分网络可能需要额外的 gas 费用
- **价格波动**：USDC/USDT 理论上是稳定币，但仍有脱锚风险
- **智能合约风险**：代币合约可能存在漏洞

---

## 六、故障排查

### 6.1 常见错误

**错误：`Wallet is not unlocked`**

```bash
# 启动时解锁钱包
joyhousebot --unlock-wallet
```

**错误：`Insufficient balance`**

```bash
# 检查余额
joyhousebot agent --message "check_token_balance network=base token=USDC"
```

**错误：`Domain not allowed by policy`**

```bash
# 检查策略配置
joyhousebot config get tools.x402_payment
```

**错误：`Payment exceeds limit`**

```bash
# 调整支付限额或使用更低的金额
```

### 6.2 调试技巧

1. **启用详细日志**：
```bash
export LOG_LEVEL=DEBUG
joyhousebot ...
```

2. **检查支付历史**：
```bash
# 查看数据库中的支付记录
sqlite3 ~/.joyhousebot/db/x402_payments.db "SELECT * FROM payments ORDER BY created_at DESC LIMIT 10"
```

3. **测试网络连接**：
```bash
# 检查 RPC 端点
curl https://mainnet.base.org
```

---

## 七、相关文档

- [CLI 参考](CLI_REFERENCE.md) - 完整的命令与参数说明
- [设计与架构](DESIGN_AND_ARCHITECTURE.md) - 分层设计、核心组件
- [钱包管理](../backend/docs/WALLET_MANAGEMENT.md) - 钱包解锁和身份管理

---

## 八、参考资源

- [EIP-3009: TransferWithAuthorization](https://eips.ethereum.org/EIPS/eip-3009)
- [EIP-712: Typed Structured Data Hashing and Signing](https://eips.ethereum.org/EIPS/eip-712)
- [CAIP-2: Blockchain ID Specification](https://chainagnostic.org/CAIPs/caip-2)
- [x402 Protocol Specification](https://x402.org)
