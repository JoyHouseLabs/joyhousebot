# DNS 工具使用指南

本文档介绍 joyhousebot 的 DNS 工具，包括主机名解析和内网域名配置。

## 概述

joyhousebot 提供两个 DNS 相关的子命令：

1. **`dns lookup`** - 解析主机名，返回该主机对应的所有 IP 地址（JSON）
2. **`dns setup`** - 为指定的内网域名生成 DNS zone 配置路径和 Tailscale/CoreDNS 使用说明

---

## 一、dns lookup - 主机名解析

### 1.1 功能说明

`dns lookup` 命令用于解析主机名或域名，返回该主机对应的所有 IP 地址（支持 IPv4 和 IPv6）。

### 1.2 命令语法

```bash
joyhousebot dns lookup <主机名>
```

### 1.3 参数说明

| 参数 | 说明 | 必填 |
|------|------|------|
| `<主机名>` | 要解析的主机名或域名 | 是 |

### 1.4 使用示例

#### 示例 1：解析域名

```bash
joyhousebot dns lookup google.com
```

输出示例（JSON 格式）：
```json
{
  "host": "google.com",
  "addresses": [
    "142.250.185.14",
    "142.250.185.15",
    "142.250.185.46"
  ]
}
```

#### 示例 2：解析本地主机名

```bash
joyhousebot dns lookup localhost
```

输出示例：
```json
{
  "host": "localhost",
  "addresses": [
    "127.0.0.1",
    "::1"
  ]
}
```

#### 示例 3：解析内网主机

```bash
joyhousebot dns lookup myserver.local
```

输出示例：
```json
{
  "host": "myserver.local",
  "addresses": [
    "192.168.1.100"
  ]
}
```

### 1.5 注意事项

- 该命令返回的是**所有**解析到的 IP 地址（包括 IPv4 和 IPv6）
- 如果主机名无法解析，会返回空地址列表或报错
- 解析结果会自动去重并排序

---

## 二、dns setup - 内网域名配置

### 2.1 功能说明

`dns setup` 命令用于为指定的内网域名生成 DNS zone 配置，并提供 Tailscale/CoreDNS 的使用说明。

**用途**：
- 为内网服务配置自定义域名（如 `openclaw.internal`）
- 生成 DNS zone 文件路径
- 提供 Tailscale 和 CoreDNS 的配置指南

### 2.2 命令语法

```bash
joyhousebot dns setup [选项]
```

### 2.3 选项说明

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--domain` | `openclaw.internal` | 内网域名名称 |
| `--apply` | `False` | 是否创建 dns 目录 |
| `--dry-run` / `--no-dry-run` | `True` | 预览模式（默认启用，不实际写入文件） |

### 2.4 使用示例

#### 示例 1：预览配置（默认）

```bash
joyhousebot dns setup
```

输出示例：
```json
{
  "domain": "openclaw.internal",
  "apply": false,
  "dry_run": true,
  "zone_file": "/Users/joyhouse/.joyhousebot/dns/openclaw_internal.zone",
  "steps": [
    "ensure dns directory exists",
    "prepare zone file template",
    "print tailscale/coredns instructions"
  ]
}
```

#### 示例 2：指定自定义域名

```bash
joyhousebot dns setup --domain mycompany.internal
```

输出示例：
```json
{
  "domain": "mycompany.internal",
  "apply": false,
  "dry_run": true,
  "zone_file": "/Users/joyhouse/.joyhousebot/dns/mycompany_internal.zone",
  "steps": [
    "ensure dns directory exists",
    "prepare zone file template",
    "print tailscale/coredns instructions"
  ]
}
```

#### 示例 3：实际创建配置目录

```bash
# 先预览
joyhousebot dns setup --dry-run --domain mycompany.internal

# 确认无误后，执行创建
joyhousebot dns setup --apply --no-dry-run --domain mycompany.internal
```

执行后会在数据目录下创建 `dns` 文件夹。

### 2.5 配置文件路径

默认情况下，DNS zone 文件会保存在 joyhousebot 的数据目录下：

```
~/.joyhousebot/dns/<域名>.zone
```

例如：
- `openclaw.internal` → `~/.joyhousebot/dns/openclaw_internal.zone`
- `mycompany.internal` → `~/.joyhousebot/dns/mycompany_internal.zone`

### 2.6 与 Tailscale/CoreDNS 集成

#### Tailscale 集成

如果使用 Tailscale 作为内网解决方案，可以配置 Tailscale 的 MagicDNS 或自定义 DNS：

1. **启用 MagicDNS**（推荐）：
   - 在 Tailscale Admin Console 中启用 MagicDNS
   - 你的节点将自动获得 `your-name.ts.net` 域名

2. **使用自定义域名**：
   - 配置 `dns setup` 生成的 zone 文件
   - 在 Tailscale 中配置 DNS 服务器指向你的 CoreDNS

#### CoreDNS 集成

如果使用 CoreDNS 作为 DNS 服务器：

1. **安装 CoreDNS**：
```bash
# macOS
brew install coredns

# Linux
sudo apt install coredns  # Debian/Ubuntu
```

2. **配置 CoreDNS**：
编辑 CoreDNS 配置文件（通常是 `Corefile`）：

```coredns
.:53 {
    errors
    health
    log
    reload
    file ~/.joyhousebot/dns/openclaw_internal.zone openclaw.internal
    forward . 8.8.8.8 8.8.4.4
}
```

3. **创建 zone 文件**：
根据 `dns setup` 提供的路径创建 zone 文件：

```dns
$ORIGIN openclaw.internal.
$TTL 3600

@       IN SOA   ns1.openclaw.internal. admin.openclaw.internal. (
                2024010101 ; serial
                3600       ; refresh
                1800       ; retry
                604800     ; expire
                86400      ; minimum
        )

        IN NS    ns1.openclaw.internal.
        IN NS    ns2.openclaw.internal.

ns1     IN A     192.168.1.10
ns2     IN A     192.168.1.11

gateway IN A     192.168.1.100
agent1   IN A     192.168.1.101
agent2   IN A     192.168.1.102
```

4. **启动 CoreDNS**：
```bash
coredns -conf Corefile
```

---

## 三、典型使用场景

### 场景 1：检查主机可解析性

```bash
# 检查内网主机是否可达
joyhousebot dns lookup myserver.local

# 检查公网服务是否可达
joyhousebot dns lookup api.example.com
```

### 场景 2：部署内网 DNS 服务

```bash
# 1. 生成配置
joyhousebot dns setup --domain company.internal

# 2. 创建配置目录
joyhousebot dns setup --apply --no-dry-run --domain company.internal

# 3. 根据输出路径编辑 zone 文件
nano ~/.joyhousebot/dns/company_internal.zone

# 4. 配置 CoreDNS 或其他 DNS 服务器
# （参考上文 CoreDNS 集成部分）
```

### 场景 3：在 agent 中使用 DNS 工具

在 agent 的工作流中，可以使用 `dns lookup` 来动态解析主机名：

```python
# 示例：检查多个主机的可用性
hosts = ["db.internal", "cache.internal", "api.internal"]
for host in hosts:
    result = joyhousebot dns lookup host
    if result["addresses"]:
        print(f"{host} is available at {result['addresses'][0]}")
    else:
        print(f"{host} is not reachable")
```

---

## 四、故障排查

### Q: dns lookup 返回空地址列表？

**可能原因**：
1. 主机名拼写错误
2. DNS 服务器配置问题
3. 网络连接问题
4. 主机不存在或已下线

**解决方法**：
```bash
# 1. 检查网络连接
ping -c 3 8.8.8.8

# 2. 检查 DNS 服务器配置
cat /etc/resolv.conf

# 3. 尝试使用不同的 DNS 服务器
nslookup host 8.8.8.8
```

### Q: dns setup 创建的目录没有权限？

**解决方法**：
```bash
# 手动创建目录并设置权限
mkdir -p ~/.joyhousebot/dns
chmod 755 ~/.joyhousebot/dns

# 然后重新运行
joyhousebot dns setup --apply --no-dry-run
```

### Q: 如何验证 DNS 配置是否生效？

**验证方法**：
```bash
# 使用 nslookup 验证
nslookup gateway.company.internal

# 使用 dig 验证（需要安装）
dig @127.0.0.1 gateway.company.internal

# 使用 ping 测试
ping gateway.company.internal
```

---

## 五、最佳实践

1. **使用有意义的主机名**：为内网服务使用清晰、有意义的命名规范（如 `db-master.internal`、`api-gateway.internal`）

2. **定期备份 DNS 配置**：zone 文件和 CoreDNS 配置应该定期备份

3. **监控 DNS 解析性能**：使用工具监控 DNS 解析延迟和成功率

4. **使用 TTL 合理设置**：根据网络环境调整 TTL 值（通常 3600 秒是一个合理的默认值）

5. **启用日志记录**：在 CoreDNS 配置中启用日志，便于排查问题

---

## 六、相关命令

以下命令可能与 DNS 工具配合使用：

```bash
# 查看网络状态
joyhousebot system status

# 查看设备列表（可能包含主机名）
joyhousebot devices list

# 查看目录服务
joyhousebot directory list

# 测试网络连接
ping <主机名>
traceroute <主机名>
```

---

## 七、相关文档

- [CLI 参考](CLI_REFERENCE.md) - 完整的命令与参数说明
- [设计与架构](DESIGN_AND_ARCHITECTURE.md) - 分层设计、核心组件
- [Agent 自主决策配置](AGENT_AUTONOMOUS_DECISION.md) - 如何配置 agent 自主决策
