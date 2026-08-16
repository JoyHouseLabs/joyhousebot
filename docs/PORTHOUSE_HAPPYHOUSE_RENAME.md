# Porthouse / HappyHouse 一次性命名重构

## 最终命名

| 范围 | 展示名称 | 稳定技术标识 |
| --- | --- | --- |
| 开源执行 Runtime 与控制台 | Porthouse | `porthouse` |
| 个人产品（Desktop、Web、Mobile、Extension） | HappyHouse | `happyhouse` |
| 账号、同步、市场与授权服务 | HappyHouse Cloud | `happyhouse-cloud` |
| Runtime 控制台 | Porthouse Console | `porthouse-console` |

Porthouse 是独立、可部署的长期任务执行 Runtime；HappyHouse 是基于 Porthouse 构建的个人 Life OS。二者只通过版本化 HTTP/SSE、SDK 与公开协议协作，产品不得成为 Runtime 的依赖。

## 破坏性迁移原则

这是开发阶段的一次性重构。不会保留 `joyhousebot`、`joyhouse`、`JOYHOUSEBOT_*`、`JOYHOUSE_*`、旧 CLI、旧包名、旧路由或旧本地数据目录的兼容别名。

已有用户数据不作为兼容层处理：需要保留的数据以一次性、显式的数据库表/列重命名迁移处理；本地 Desktop 数据根切换到新标识后，由产品首次启动迁移工具导入。不得在新代码中长期读取旧名称。

## 标识映射

| 旧标识 | 新标识 |
| --- | --- |
| `JoyHouseBot` / `joyhousebot` | `Porthouse` / `porthouse` |
| `JoyHouse` / `joyhouse` | `HappyHouse` / `happyhouse` |
| `joyhousebot` Python package / CLI | `porthouse` Python package / CLI |
| `joyhousebot_*` Python extension packages | `porthouse_*` |
| `@joyhouse/*` | `@happyhouse/*` |
| `joyhouse_*` Product Python packages | `happyhouse_*` |
| `JOYHOUSEBOT_*` Runtime environment | `PORTHOUSE_*` |
| `JOYHOUSE_*` Product environment | `HAPPYHOUSE_*` |
| `me.joyhouse.desktop` | `me.happyhouse.desktop` |
| `JoyHouse.app` | `HappyHouse.app` |
| `joyhouse-local-node` | `happyhouse-local-node` |

数据库继续使用一个 PostgreSQL 连接，但 Porthouse、HappyHouse 与 Cloud/Market 只访问各自所有的表和迁移链。表名、迁移记录、索引、队列和本地状态文件中的旧前缀通过单向迁移改为新前缀；禁止保留双写、视图别名或跨模块直接访问。

## 范围与顺序

1. Runtime：仓库目录、Python package、CLI、extension distribution/entry point、Console、SDK、API、配置、部署、测试与文档。
2. Product：仓库目录、JS package scope、Product API Python packages、Desktop Bundle ID、运行时资源、本地数据目录、应用文案、部署与文档。
3. Cloud/Market：仓库和 Python package、OAuth/Cloud/Market 文案、协议导入、部署单元与配置。
4. 生态引用：Porthouse 官网、Smart Study 与官方 App 的 Runtime 客户端引用；只修改受本项目维护的源码和部署模板，不批量修改历史资料、用户上传文件、构建缓存或第三方代码。
5. 验证：残留扫描必须在源码、文档、脚本、部署和测试范围内为零；每个仓库执行对应类型检查、测试与构建；Desktop 重新打包并验证 Porthouse Runtime 的本地启动。

## 外部资源

Porthouse 的公开仓库归属 `https://github.com/HappyHouseLabs/porthouse`，官网为 `https://porthouse.happayhouselabs.com/`。DNS、TLS 证书、App Store Connect Bundle ID、Apple 签名 profile、Market/Cloud 生产服务和用户已安装的旧版应用属于外部切换项；必须在发布阶段核对后执行。
